"""Vietnamese equity collector built on vnstock (VCI data source).

Why VCI and not TCBS: TCBS's public REST endpoints answer 403 from this
network, so building on them would leave the pipeline dark without warning.
VCI serves the full market board for ~1,750 tickers in about 9.5 seconds.

What this collects that a price snapshot cannot give you:
- `foreign_buy_value` / `foreign_sell_value` per ticker, which makes daily and
  rolling foreign net flow a first-class column rather than a separate product.
- `Finance.ratio()`, a quarterly fundamentals panel reaching back to 2018 (54
  metrics), which is what turns a screener into factor ranking and lets a
  valuation be expressed as a percentile of a ticker's own history.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import uuid
import warnings
from datetime import date, datetime
from typing import Any

from pipeline.core import warehouse as wh

log = logging.getLogger(__name__)

# vnstock prints promotional banners on import and warns liberally; neither is
# useful in CI logs.
os.environ.setdefault("ACCEPT_TC", "tôi đồng ý")
warnings.filterwarnings("ignore")

BOARD_BATCH = 300  # tickers per price_board call
SOURCE = "VCI"
# vnstock rate limits by account tier, and the ceiling that matters is the one
# for an unauthenticated caller: **20 requests/minute as Guest**, not the 60
# its marketing copy quotes (60 requires a free registered API key). Tripping it
# blocks for a full 60 seconds, so a fast pass that dies partway is strictly
# worse than a paced one. 3.1s/ticker keeps us under the Guest ceiling and puts
# a full-market fundamentals pass at roughly 90 minutes.
#
# Set VNSTOCK_RPM if the run is authenticated for a higher tier.
_RPM = int(os.environ.get("VNSTOCK_RPM", "20"))
RATE_LIMIT_SLEEP = 60.0 / _RPM + 0.1
RATE_LIMIT_BACKOFF = 65.0  # the limiter's own stated cooldown


def _flatten(columns: Any) -> list[str]:
    """price_board returns a MultiIndex; flatten to 'group.field' names."""
    flat = []
    for col in columns:
        if isinstance(col, tuple):
            flat.append(".".join(str(p) for p in col if p not in (None, "")))
        else:
            flat.append(str(col))
    return flat


def collect_listing(fetched_at: datetime) -> list[dict[str, Any]]:
    from vnstock import Listing

    listing = Listing(source=SOURCE)

    # all_symbols() carries only symbol and name. Exchange and ICB industry come
    # from two separate calls, and the screener is far less useful without them
    # (no sector facet, no peer comparison).
    exchanges: dict[str, str] = {}
    try:
        for rec in listing.symbols_by_exchange().to_dict("records"):
            symbol = rec.get("symbol")
            if isinstance(symbol, str):
                exchanges[symbol.strip()] = rec.get("exchange")
    except Exception as exc:
        log.warning("symbols_by_exchange failed: %s", exc)

    # symbols_by_industries returns one row per ICB level; the deepest level
    # present is the most specific industry name for that ticker.
    industries: dict[str, tuple[int, str]] = {}
    try:
        for rec in listing.symbols_by_industries().to_dict("records"):
            symbol, name = rec.get("symbol"), rec.get("icb_name")
            if not isinstance(symbol, str) or not name:
                continue
            try:
                level = int(rec.get("icb_level") or 0)
            except (TypeError, ValueError):
                level = 0
            symbol = symbol.strip()
            if symbol not in industries or level > industries[symbol][0]:
                industries[symbol] = (level, str(name))
    except Exception as exc:
        log.warning("symbols_by_industries failed: %s", exc)

    rows = []
    for rec in listing.all_symbols().to_dict("records"):
        symbol = rec.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        symbol = symbol.strip()
        rows.append({
            "symbol": symbol,
            "organ_name": rec.get("organ_name"),
            "exchange": exchanges.get(symbol),
            "industry": (industries.get(symbol) or (0, None))[1],
            "fetched_at": fetched_at,
        })
    return rows


def collect_board(symbols: list[str], fetched_at: datetime) -> list[dict[str, Any]]:
    """Fetch the full market board in batches, including foreign flow."""
    from vnstock import Trading

    trading = Trading(source=SOURCE)
    rows: list[dict[str, Any]] = []
    today = date.today()

    for start in range(0, len(symbols), BOARD_BATCH):
        batch = symbols[start:start + BOARD_BATCH]
        df = trading.price_board(batch)
        df.columns = _flatten(df.columns)

        def pick(rec: dict[str, Any], *names: str) -> Any:
            for name in names:
                value = rec.get(name)
                # pandas fills absent cells with NaN, which is not None and is
                # not falsy in a way that survives into DuckDB.
                if value is None or (isinstance(value, float) and value != value):
                    continue
                return value
            return None

        for rec in df.to_dict("records"):
            symbol = pick(rec, "listing.symbol", "symbol")
            # The board returns a placeholder row for tickers it cannot price
            # (delisted, suspended). They carry no symbol and must be dropped
            # rather than written as a NULL-keyed observation.
            if not symbol:
                continue
            rows.append({
                "symbol": symbol,
                "as_of": today,
                "fetched_at": fetched_at,
                "exchange": pick(rec, "listing.exchange"),
                "price": pick(rec, "match.match_price", "match.avg_match_price"),
                "ref_price": pick(rec, "listing.ref_price"),
                "open_price": pick(rec, "match.open_price"),
                "high": pick(rec, "match.highest"),
                "low": pick(rec, "match.lowest"),
                "volume": pick(rec, "match.accumulated_volume"),
                "value": pick(rec, "match.accumulated_value"),
                "listed_share": pick(rec, "listing.listed_share"),
                "foreign_buy_value": pick(rec, "match.foreign_buy_value"),
                "foreign_sell_value": pick(rec, "match.foreign_sell_value"),
                "foreign_buy_vol": pick(rec, "match.foreign_buy_volume"),
                "foreign_sell_vol": pick(rec, "match.foreign_sell_volume"),
            })
        log.info("board %d/%d tickers", min(start + BOARD_BATCH, len(symbols)), len(symbols))
    return rows


def _already_collected(con: Any = None) -> set[str]:
    """Tickers whose fundamentals the warehouse already holds.

    Reuses the caller's connection when given one: DuckDB permits a single
    writer, so opening a second handle while a run holds the write connection
    fails outright.
    """
    if con is not None:
        return {r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM eq_fundamental"
        ).fetchall()}

    if not wh.DB_PATH.exists():
        return set()

    probe = wh.connect(read_only=True)
    try:
        return {r[0] for r in probe.execute(
            "SELECT DISTINCT symbol FROM eq_fundamental"
        ).fetchall()}
    finally:
        probe.close()


def collect_fundamentals(
    symbols: list[str],
    fetched_at: datetime,
    limit: int | None = None,
    skip_existing: bool = True,
    on_batch: Any = None,
    batch_size: int = 25,
    con: Any = None,
) -> dict[str, Any]:
    """Pull the quarterly ratio panel per ticker; return counts, not rows.

    One HTTP call per ticker against a source that allows 60 requests/minute on
    the free tier, so a full-market pass takes roughly half an hour and must be
    scheduled apart from the daily board.

    Rows are handed to `on_batch` every `batch_size` tickers and dropped, so
    memory stays flat and a job killed mid-run keeps everything collected up to
    that point. `skip_existing` then lets the next run resume where this one
    stopped -- both matter because Actions cron can cut a long job short.
    """
    from vnstock import Finance

    stats: dict[str, Any] = {"rows": 0, "symbols": 0, "failed": 0}
    rows: list[dict[str, Any]] = []
    targets = symbols[:limit] if limit else symbols

    if skip_existing:
        have = _already_collected(con)
        if have:
            targets = [s for s in targets if s not in have]
            log.info("skipping %d tickers already collected", len(have))

    consecutive_failures = 0

    for i, symbol in enumerate(targets, 1):
        time.sleep(RATE_LIMIT_SLEEP)
        try:
            df = Finance(symbol=symbol, source=SOURCE).ratio(period="quarter", lang="en")
            consecutive_failures = 0
        except SystemExit as exc:
            # vnstock signals a rate-limit breach by raising SystemExit, not a
            # normal exception. `except Exception` does not catch it (SystemExit
            # derives from BaseException), which is why an uncaught breach kills
            # the whole run silently and loses everything not yet flushed.
            # Waiting out the limiter's own cooldown and retrying once is enough.
            log.warning("rate limited at %s (%s) — waiting %.0fs",
                        symbol, exc, RATE_LIMIT_BACKOFF)
            time.sleep(RATE_LIMIT_BACKOFF)
            try:
                df = Finance(symbol=symbol, source=SOURCE).ratio(
                    period="quarter", lang="en"
                )
            except BaseException:
                stats["rate_limited"] = stats.get("rate_limited", 0) + 1
                stats["failed"] += 1
                continue
        except Exception as exc:
            # A single failure here is usually a delisted or thinly covered
            # ticker; a run of them means something broader is wrong.
            log.debug("ratio failed for %s: %s", symbol, exc)
            stats["failed"] += 1
            consecutive_failures += 1
            if consecutive_failures >= 5:
                log.warning(
                    "%d consecutive failures at %s — backing off %.0fs",
                    consecutive_failures, symbol, RATE_LIMIT_BACKOFF,
                )
                time.sleep(RATE_LIMIT_BACKOFF)
                consecutive_failures = 0
            continue

        # Layout: one row per metric, one column per period.
        period_cols = [c for c in df.columns if isinstance(c, str) and "-Q" in c]
        for rec in df.to_dict("records"):
            metric = rec.get("item_en") or rec.get("item")
            if not metric:
                continue
            for period in period_cols:
                value = rec.get(period)
                if value is None:
                    continue
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
                rows.append({
                    "symbol": symbol,
                    "period": period,
                    "metric": str(metric).strip(),
                    "value": value,
                    "fetched_at": fetched_at,
                })
        if i % batch_size == 0:
            stats["rows"] += len(rows)
            if on_batch:
                on_batch(rows)
                rows = []
        if i % 50 == 0:
            log.info(
                "fundamentals %d/%d tickers (%d rows)", i, len(targets), stats["rows"]
            )

    stats["rows"] += len(rows)
    stats["symbols"] = len(targets)
    if on_batch:
        on_batch(rows)
    else:
        stats["batch"] = rows
    return stats


def collect(
    what: str = "board",
    dry_run: bool = False,
    fundamentals_limit: int | None = None,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    started = wh.utcnow()
    fetched_at = started
    stats: dict[str, Any] = {"what": what}

    listing = collect_listing(fetched_at)
    symbols = [r["symbol"] for r in listing if r["symbol"]]
    stats["symbols"] = len(symbols)

    board: list[dict[str, Any]] = []
    fundamental_stats: dict[str, Any] = {}

    if what in ("board", "all"):
        board = collect_board(symbols, fetched_at)
        stats["board_rows"] = len(board)
        priced = [r for r in board if r.get("price")]
        stats["board_priced"] = len(priced)
        net = sum(
            (r.get("foreign_buy_value") or 0) - (r.get("foreign_sell_value") or 0)
            for r in board
        )
        stats["foreign_net_value"] = round(net, 0)

    if dry_run:
        if what in ("fundamentals", "all"):
            fundamental_stats = collect_fundamentals(
                symbols, fetched_at, fundamentals_limit
            )
            fundamental_stats.pop("batch", None)
            stats["fundamentals"] = fundamental_stats
        stats["elapsed_sec"] = round((wh.utcnow() - started).total_seconds(), 1)
        return stats

    # DuckDB is single-writer, so one connection is held for the whole run
    # rather than reopened per batch -- a second connection here is exactly how
    # a long fundamentals pass ends up locked out by its own writes.
    con = wh.connect()
    try:
        stats["listing_new"] = wh.append(con, "eq_listing", listing)
        if board:
            stats["board_new"] = wh.append(con, "eq_quote", board)

        if what in ("fundamentals", "all"):
            written = 0

            def flush(batch: list[dict[str, Any]]) -> None:
                nonlocal written
                if batch:
                    written += wh.append(con, "eq_fundamental", batch)

            fundamental_stats = collect_fundamentals(
                symbols, fetched_at, fundamentals_limit, on_batch=flush, con=con
            )
            stats["fundamental_rows"] = fundamental_stats["rows"]
            stats["fundamental_symbols"] = fundamental_stats["symbols"]
            stats["fundamental_failed"] = fundamental_stats["failed"]
            stats["fundamental_new"] = written

        stats["elapsed_sec"] = round((wh.utcnow() - started).total_seconds(), 1)
        wh.log_run(
            con, run_id, f"vn_equity:{what}", started, "ok",
            rows_in=len(board) + stats.get("fundamental_rows", 0),
            rows_new=stats.get("board_new", 0) + stats.get("fundamental_new", 0),
            detail=stats,
        )
    finally:
        con.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect VN equity data via vnstock")
    parser.add_argument(
        "--what", choices=["board", "fundamentals", "all"], default="board",
        help="board = daily prices + foreign flow; fundamentals = quarterly panel",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report counts, write nothing")
    parser.add_argument(
        "--fundamentals-limit", type=int,
        help="Only fetch fundamentals for the first N tickers (testing)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stats = collect(args.what, args.dry_run, args.fundamentals_limit)
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
