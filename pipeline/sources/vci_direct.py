"""Vietcap fundamentals collector, calling the upstream API directly.

Why this exists alongside `vn_equity.py`:

`vnstock` is a convenient wrapper, but it enforces its own client-side quota on
top of the upstream service. Measured on this network, an unauthenticated
caller is cut off after ~12 `ratio()` calls even when paced at 9 requests per
minute, and the breach is signalled by raising `SystemExit` -- so an unguarded
loop dies mid-run and takes everything not yet flushed with it. At that rate a
full-market pass over ~1,750 tickers is simply not reachable.

The same data comes from Vietcap's own endpoint, which vnstock is wrapping.
Measured directly: 25/25 tickers at a sustained 127 requests/minute, no quota,
putting a full-market pass at roughly 15 minutes. It also arrives in a better
shape -- one record per quarter with ratios as named columns, rather than a
metric-per-row table that has to be pivoted.

`vn_equity.py` is still the right tool for the daily price board and the
listing/industry reference data, where vnstock's batching is genuinely useful
and the call count is low.
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from datetime import datetime
from typing import Any

from pipeline.core.http import HttpClient
from pipeline.core import warehouse as wh

log = logging.getLogger(__name__)

BASE = "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company"
HEADERS = {
    "Accept": "application/json",
    "Referer": "https://iq.vietcap.com.vn/",
}
# Quarters to request per ticker. 40 reaches back to 2018 and matches what the
# service actually holds.
PERIODS = 40

# Ratio fields worth storing, mapped to the metric names used in the warehouse.
# Bank-only fields are included; they are simply absent for other companies.
FIELDS = {
    "pe": "pe",
    "pb": "pb",
    "ps": "ps",
    "evToEbitda": "ev_ebitda",
    "priceToCashFlow": "p_cf",
    "dividendYield": "dividend_yield",
    "marketCap": "market_cap",
    "roe": "roe",
    "roa": "roa",
    "roic": "roic",
    "grossMargin": "gross_margin",
    "ebitMargin": "ebit_margin",
    "afterTaxProfitMargin": "net_margin",
    "debtToEquity": "debt_equity",
    "currentRatio": "current_ratio",
    "quickRatio": "quick_ratio",
    "cashRatio": "cash_ratio",
    "financialLeverage": "leverage",
    "assetTurnover": "asset_turnover",
    "cashCycle": "cash_cycle",
    "ownersEquity": "owners_equity",
    "ebitda": "ebitda",
    # Banks
    "netInterestMargin": "nim",
    "npl": "npl",
    "car": "car",
    "casaRatio": "casa",
    "ldrLoanDepositRatio": "ldr",
    "cir": "cir",
    "loansGrowth": "loans_growth",
    "depositGrowth": "deposit_growth",
}


def fetch_ticker(client: HttpClient, symbol: str) -> list[dict[str, Any]]:
    """Fetch one ticker's quarterly ratio history."""
    payload = client.get_json(
        f"{BASE}/{symbol}/statistics-financial",
        params={"period": "Q", "size": PERIODS},
        headers=HEADERS,
    )
    data = payload.get("data")
    return data if isinstance(data, list) else []


def to_rows(symbol: str, records: list[dict[str, Any]], fetched_at: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in records:
        year, quarter = rec.get("year"), rec.get("quarter")
        if not year or not quarter:
            continue
        period = f"{int(year)}-Q{int(quarter)}"
        for field, metric in FIELDS.items():
            value = rec.get(field)
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            rows.append({
                "symbol": symbol,
                "period": period,
                "metric": metric,
                "value": value,
                "fetched_at": fetched_at,
            })
    return rows


def collect(
    symbols: list[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    delay: float = 0.3,
    skip_existing: bool = False,
    batch_size: int = 40,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    started = wh.utcnow()
    fetched_at = started
    client = HttpClient(delay=delay, retries=2)

    con = None if dry_run else wh.connect()
    try:
        if not symbols:
            probe = con or (wh.connect(read_only=True) if wh.DB_PATH.exists() else None)
            if probe is None:
                raise SystemExit(
                    "No warehouse yet. Run `python -m pipeline.sources.vn_equity "
                    "--what board` first so the ticker list exists."
                )
            symbols = [r[0] for r in probe.execute(
                "SELECT DISTINCT symbol FROM eq_listing ORDER BY symbol"
            ).fetchall()]
            if probe is not con:
                probe.close()

        if skip_existing and con is not None:
            required_period = wh.latest_completed_quarter(fetched_at)
            have = {r[0] for r in con.execute(
                "SELECT DISTINCT symbol FROM eq_fundamental WHERE period = ?",
                [required_period],
            ).fetchall()}
            if have:
                symbols = [s for s in symbols if s not in have]
                log.info("skipping %d tickers already collected for %s", len(have), required_period)

        targets = symbols[:limit] if limit else symbols
        stats: dict[str, Any] = {
            "targets": len(targets), "ok": 0, "failed": 0, "rows": 0, "written": 0,
        }

        pending: list[dict[str, Any]] = []
        for i, symbol in enumerate(targets, 1):
            try:
                records = fetch_ticker(client, symbol)
            except Exception as exc:
                log.debug("%s failed: %s", symbol, exc)
                stats["failed"] += 1
                continue

            rows = to_rows(symbol, records, fetched_at)
            if rows:
                stats["ok"] += 1
                stats["rows"] += len(rows)
                pending.extend(rows)

            # Flush periodically so a killed run keeps what it already fetched.
            if con is not None and len(pending) >= batch_size * 200:
                stats["written"] += wh.append(con, "eq_fundamental", pending)
                pending = []

            if i % 100 == 0:
                log.info(
                    "%d/%d tickers (%d ok, %d rows)",
                    i, len(targets), stats["ok"], stats["rows"],
                )

        if con is not None and pending:
            stats["written"] += wh.append(con, "eq_fundamental", pending)

        stats["elapsed_sec"] = round((wh.utcnow() - started).total_seconds(), 1)
        if stats["elapsed_sec"] > 0:
            stats["req_per_min"] = round(len(targets) / stats["elapsed_sec"] * 60, 1)

        if con is not None:
            wh.log_run(
                con, run_id, "vci_direct", started, "ok",
                rows_in=stats["rows"], rows_new=stats["written"], detail=stats,
            )
        return stats
    finally:
        if con is not None:
            con.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect VN quarterly fundamentals directly from Vietcap"
    )
    parser.add_argument("--symbols", help="Comma-separated tickers (default: all in warehouse)")
    parser.add_argument("--limit", type=int, help="Only the first N tickers")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but write nothing")
    parser.add_argument("--delay", type=float, default=0.3, help="Seconds between requests")
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Resume: skip tickers that already cover the latest completed quarter",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    stats = collect(symbols, args.limit, args.dry_run, args.delay, args.skip_existing)
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
