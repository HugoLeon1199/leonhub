"""Point-in-time DuckDB warehouse — the single source of truth.

Every published JSON is a *view* over this store, never the store itself.

The one rule that makes the quant layer possible: observations are append-only
and carry both `as_of` (the moment the fact was true in the world) and
`fetched_at` (the moment we learned it). Nothing is ever updated in place. A
backtest that reads `WHERE fetched_at <= t` therefore sees exactly what we knew
at t, which is what keeps track records honest instead of look-ahead-fitted.
"""

from __future__ import annotations

import gzip
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

log = logging.getLogger(__name__)

try:  # pandas is present wherever a collector uses it, optional elsewhere
    from pandas import NA as _PD_NA
except Exception:
    _PD_NA = object()

REPO_ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE_DIR = REPO_ROOT / "warehouse"
DB_PATH = WAREHOUSE_DIR / "leonhub.duckdb"
# How long a collector waits for the writer slot before giving up. Long enough
# to outlast another collector's final flush, short enough to fail a stuck job.
WRITE_LOCK_WAIT = 300.0

SCHEMA = """
-- Real-estate listings, one row per observation of a listing.
CREATE TABLE IF NOT EXISTS re_listing (
    list_id        BIGINT      NOT NULL,
    source         VARCHAR     NOT NULL,
    as_of          TIMESTAMPTZ NOT NULL,   -- listing publish time
    fetched_at     TIMESTAMPTZ NOT NULL,   -- when we saw it
    region         VARCHAR,                -- province / city
    district       VARCHAR,
    ward           VARCHAR,
    category       VARCHAR,                -- Nhà ở / Đất / Căn hộ ...
    house_type     VARCHAR,
    price          DOUBLE,                 -- VND, asking
    size_m2        DOUBLE,
    price_per_m2   DOUBLE,                 -- million VND / m2
    rooms          INTEGER,
    latitude       DOUBLE,
    longitude      DOUBLE,
    street_name    VARCHAR,
    subject        VARCHAR,
    PRIMARY KEY (list_id, fetched_at)
);

-- Daily equity quote + foreign flow, one row per ticker per fetch.
CREATE TABLE IF NOT EXISTS eq_quote (
    symbol             VARCHAR     NOT NULL,
    as_of              DATE        NOT NULL,   -- trading date
    fetched_at         TIMESTAMPTZ NOT NULL,
    exchange           VARCHAR,
    price              DOUBLE,
    ref_price          DOUBLE,
    open_price         DOUBLE,
    high               DOUBLE,
    low                DOUBLE,
    volume             BIGINT,
    value              DOUBLE,
    listed_share       BIGINT,
    foreign_buy_value  DOUBLE,
    foreign_sell_value DOUBLE,
    foreign_buy_vol    BIGINT,
    foreign_sell_vol   BIGINT,
    PRIMARY KEY (symbol, as_of, fetched_at)
);

-- Quarterly fundamentals panel, one row per ticker per period per metric.
CREATE TABLE IF NOT EXISTS eq_fundamental (
    symbol      VARCHAR     NOT NULL,
    period      VARCHAR     NOT NULL,   -- '2026-Q2'
    metric      VARCHAR     NOT NULL,   -- normalized metric key
    value       DOUBLE,
    fetched_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (symbol, period, metric, fetched_at)
);

-- Ticker reference data.
CREATE TABLE IF NOT EXISTS eq_listing (
    symbol      VARCHAR     NOT NULL,
    organ_name  VARCHAR,
    exchange    VARCHAR,
    industry    VARCHAR,
    fetched_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (symbol, fetched_at)
);

-- Generic timeseries for macro / flows / derivatives, so new sources need no DDL.
CREATE TABLE IF NOT EXISTS metric_ts (
    series      VARCHAR     NOT NULL,   -- 'etf.btc.net_flow', 'defi.stablecoin.supply'
    as_of       TIMESTAMPTZ NOT NULL,
    value       DOUBLE,
    source      VARCHAR     NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL,
    meta        JSON,
    PRIMARY KEY (series, as_of, fetched_at)
);

-- Every collector run, so a silent source failure is visible instead of a gap.
CREATE TABLE IF NOT EXISTS run_log (
    run_id      VARCHAR     NOT NULL,
    collector   VARCHAR     NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status      VARCHAR,                -- ok | partial | failed
    rows_in     BIGINT,
    rows_new    BIGINT,
    detail      JSON,
    PRIMARY KEY (run_id, collector)
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the warehouse, creating the schema on first use.

    DuckDB permits a single process to hold the database, and collectors are
    long-running: a market-wide fundamentals pass keeps the write handle for
    ~15 minutes. A reader that arrives during one would simply fail, so readers
    fall back to a snapshot copy (see `connect_reader`) rather than blocking a
    build behind a collector.
    """
    WAREHOUSE_DIR.mkdir(parents=True, exist_ok=True)

    # A collector that has spent an hour fetching must not lose that work
    # because another job holds the single writer slot for a few seconds. Wait
    # for it rather than failing: build steps use connect_reader(), which fails
    # fast instead, because a stale build is worse than a late one.
    deadline = time.monotonic() + (0 if read_only else WRITE_LOCK_WAIT)
    while True:
        try:
            con = duckdb.connect(str(DB_PATH), read_only=read_only)
            break
        except duckdb.IOException:
            if time.monotonic() >= deadline:
                raise
            log.info("warehouse is locked by another writer; waiting")
            time.sleep(5)

    if not read_only:
        con.execute(SCHEMA)
    return con


def connect_reader() -> duckdb.DuckDBPyConnection:
    """Open the warehouse for reading, with a clear message if a writer holds it.

    DuckDB allows a single process to hold the database, and Windows locks the
    file outright — it cannot even be copied aside while a collector runs. So a
    build step that starts during a long collection has no way to read the live
    database, and pretending otherwise would mean publishing from a half-written
    copy. Failing with an explanation is the honest outcome.
    """
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"No warehouse at {DB_PATH}. Run a collector first, e.g. "
            "`python -m pipeline.sources.vn_equity --what board`."
        )
    try:
        return duckdb.connect(str(DB_PATH), read_only=True)
    except duckdb.IOException as exc:
        raise RuntimeError(
            "The warehouse is held by a running collector, so this build cannot "
            "read it. Wait for the collector to finish, then re-run. (Collectors "
            "and build steps are sequenced in the workflows for this reason.)"
        ) from exc


def _clean(value: Any) -> Any:
    """Normalize pandas null sentinels to SQL NULL.

    Collectors that go through pandas hand us float('nan') or pd.NA for empty
    cells, and DuckDB refuses to cast either into an integer column. Coercing
    here means every collector inherits the fix instead of each rediscovering
    it against a different table.
    """
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    if value is _PD_NA:
        return None
    return value


def append(
    con: duckdb.DuckDBPyConnection,
    table: str,
    rows: list[dict[str, Any]],
) -> int:
    """Append observations, skipping ones already recorded at the same instant.

    Returns the number of rows actually inserted. Duplicate primary keys are
    dropped rather than raising, so a re-run after a partial failure is safe.
    """
    if not rows:
        return 0

    columns = [c[0] for c in con.execute(f"DESCRIBE {table}").fetchall()]
    payload = [tuple(_clean(r.get(c)) for c in columns) for r in rows]

    # Inserted as one relation in a single statement. The row-at-a-time
    # alternative (executemany) bracketed by a count(*) either side to measure
    # the delta is what turned a fundamentals pass into a CPU-bound crawl:
    # both of those costs scale with total table size rather than batch size.
    import pandas as pd

    incoming = pd.DataFrame(payload, columns=columns)
    con.register("_incoming", incoming)
    try:
        result = con.execute(
            f"INSERT OR IGNORE INTO {table} SELECT * FROM _incoming"
        ).fetchone()
        return int(result[0]) if result else len(payload)
    finally:
        con.unregister("_incoming")


def log_run(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    collector: str,
    started_at: datetime,
    status: str,
    rows_in: int = 0,
    rows_new: int = 0,
    detail: dict[str, Any] | None = None,
) -> None:
    import json as _json

    con.execute(
        "INSERT OR REPLACE INTO run_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id, collector, started_at, utcnow(), status,
            rows_in, rows_new, _json.dumps(detail or {}, ensure_ascii=False),
        ),
    )


def snapshot(dest: Path | None = None) -> Path:
    """Compress the warehouse for the monthly committed snapshot.

    The live .duckdb file stays out of git; only this .gz is committed, and only
    monthly, because the repo has a ~1GB soft cap.
    """
    dest = dest or WAREHOUSE_DIR / "leonhub.duckdb.gz"
    with open(DB_PATH, "rb") as src, gzip.open(dest, "wb", compresslevel=9) as out:
        shutil.copyfileobj(src, out)
    log.info("warehouse snapshot -> %s (%.1f MB)", dest, dest.stat().st_size / 1e6)
    return dest
