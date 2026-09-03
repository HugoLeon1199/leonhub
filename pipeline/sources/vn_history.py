"""Daily OHLC backfill for VN equities.

Why this exists: the daily board collector captures one snapshot per run, so a
freshly built warehouse holds a single trading date. Every rolling window the
site wants — momentum, a 52-week position, an unusual-move z-score — needs a
price history that does not exist yet, and waiting a month for it to accumulate
is not a plan.

**Source: VPS.** Vietcap's charting endpoint was the first choice and worked
until a sustained backfill run, after which it stopped answering entirely —
not a 429 or a 403, just silence, while Vietcap's *fundamentals* endpoint on a
different host kept serving normally. That failure mode is why this module
times out fast and stops after a run of failures instead of queueing 90-second
stalls. VPS was then compared against the live board and is the better source
regardless: it carries the current session's close (72,200 for FPT on the day
this was written) where DNSE lagged a day and disagreed on the prior close.

Note the separation of concerns: this fills in prices only. Foreign flow has no
free historical source anywhere, so those columns still accumulate forward from
the daily collector — the site is honest about which windows it can fill.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from pipeline.core.http import HttpClient
from pipeline.core import warehouse as wh

log = logging.getLogger(__name__)

URL = "https://histdatafeed.vps.com.vn/tradingview/history"
HEADERS = {
    "Accept": "application/json",
    # A browser-shaped agent: this is a TradingView-compatible feed and a
    # library-looking agent gets less patience from it.
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0",
}
# Trading days to request per ticker. 260 is about one calendar year.
DEFAULT_BARS = 260
# Seconds per trading day, padded for weekends and holidays, used to turn a bar
# count into the `from` timestamp the feed expects.
SECONDS_PER_TRADING_DAY = 86400 * 1.5
# The feed quotes in thousands of VND (72.2 for a 72,200 VND close) while the
# board collectors write plain VND. Mixing the two in eq_quote would put a
# thousand-fold step in every ticker's history exactly where the backfill meets
# the live rows, wrecking momentum, z-scores and the 52-week range at once.
PRICE_SCALE = 1000.0


def fetch_history(client: HttpClient, symbol: str, bars: int) -> list[dict[str, Any]]:
    """Fetch one ticker's daily bars.

    TradingView's UDF shape: parallel arrays of timestamps, opens, highs, lows,
    closes and volumes, with `s` reporting status ("ok" or "no_data").
    """
    now = int(time.time())
    block = client.get_json(
        URL,
        params={
            "symbol": symbol,
            "resolution": "D",
            "from": now - int(bars * SECONDS_PER_TRADING_DAY),
            "to": now,
        },
        headers=HEADERS,
    )
    if not isinstance(block, dict) or block.get("s") == "no_data":
        return []

    times = block.get("t") or []
    rows = []
    for i, ts in enumerate(times):
        def at(key: str) -> float | None:
            values = block.get(key) or []
            if i >= len(values):
                return None
            try:  # every numeric field arrives as a string
                return float(values[i])
            except (TypeError, ValueError):
                return None

        close = at("c")
        if close is None:
            continue
        # Timestamps arrive as strings ("1756944000"), not integers.
        try:
            seconds = int(float(ts))
        except (TypeError, ValueError):
            continue
        scale = lambda v: None if v is None else v * PRICE_SCALE
        rows.append({
            "t": datetime.fromtimestamp(seconds, tz=timezone.utc).date(),
            "o": scale(at("o")), "h": scale(at("h")), "l": scale(at("l")),
            "c": scale(close),
            # The feed carries volume but not traded value; leaving it null is
            # correct, since deriving it from close x volume would understate
            # any session with meaningful intraday range.
            "v": at("v"), "val": None,
        })
    return rows


def to_quotes(symbol: str, bars: list[dict[str, Any]], fetched_at: datetime) -> list[dict[str, Any]]:
    """Map bars onto eq_quote.

    `fetched_at` is set to the bar's own date, not now: this is historical data
    and stamping it with the current time would tell a point-in-time query that
    we knew a year of prices at this instant, which is exactly the look-ahead
    the warehouse exists to prevent. Backfilled rows carry no foreign flow —
    that column has no free historical source and stays null rather than zero.
    """
    rows = []
    for bar in bars:
        as_of = bar["t"]
        stamp = datetime(as_of.year, as_of.month, as_of.day, 8, tzinfo=timezone.utc)
        rows.append({
            "symbol": symbol,
            "as_of": as_of,
            "fetched_at": stamp,
            "exchange": None,
            "price": bar["c"],
            "ref_price": None,
            "open_price": bar["o"],
            "high": bar["h"],
            "low": bar["l"],
            "volume": bar["v"],
            "value": bar["val"],
            "listed_share": None,
            "foreign_buy_value": None,
            "foreign_sell_value": None,
            "foreign_buy_vol": None,
            "foreign_sell_vol": None,
        })
    return rows


def collect(
    symbols: list[str] | None = None,
    bars: int = DEFAULT_BARS,
    limit: int | None = None,
    dry_run: bool = False,
    delay: float = 0.35,
    skip_existing: bool = True,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    started = wh.utcnow()
    # Short timeout on purpose: the endpoint does not refuse a sustained run
    # with an error, it simply stops answering. At the default 30s x 3 retries
    # a single stalled ticker costs 90 seconds, and the whole job silently
    # becomes a queue of timeouts.
    client = HttpClient(delay=delay, retries=1, timeout=12.0)
    consecutive_failures = 0

    con = None if dry_run else wh.connect()
    try:
        if not symbols:
            probe = con or (wh.connect_reader() if wh.DB_PATH.exists() else None)
            if probe is None:
                raise SystemExit(
                    "No warehouse yet. Run `python -m pipeline.sources.vn_equity "
                    "--what board` first so the ticker list exists."
                )
            # Only tickers that actually trade: the full listing includes funds
            # and long-suspended shells that return empty histories.
            symbols = [r[0] for r in probe.execute("""
                SELECT DISTINCT symbol FROM eq_quote
                WHERE price IS NOT NULL AND price > 0
                ORDER BY symbol
            """).fetchall()]
            if probe is not con:
                probe.close()

        if skip_existing and con is not None:
            # "Already has history" means more than a handful of dates: the
            # daily collector alone leaves one per ticker, so the threshold has
            # to sit above that but below a full backfill, or a resumed run
            # either redoes everything or skips partially-filled tickers.
            have = {r[0] for r in con.execute("""
                SELECT symbol FROM eq_quote
                GROUP BY symbol HAVING count(DISTINCT as_of) >= 20
            """).fetchall()}
            if have:
                symbols = [s for s in symbols if s not in have]
                log.info("skipping %d tickers that already have history", len(have))

        targets = symbols[:limit] if limit else symbols
        stats: dict[str, Any] = {"targets": len(targets), "ok": 0, "empty": 0,
                                 "failed": 0, "rows": 0, "written": 0}
        pending: list[dict[str, Any]] = []

        for i, symbol in enumerate(targets, 1):
            try:
                history = fetch_history(client, symbol, bars)
            except Exception as exc:
                log.debug("%s failed: %s", symbol, exc)
                stats["failed"] += 1
                consecutive_failures += 1
                # A run of failures means the source has stopped answering, not
                # that these particular tickers are bad. Stop and keep what we
                # have; --skip-existing resumes from here on the next run.
                if consecutive_failures >= 15:
                    log.warning(
                        "stopping after %d consecutive failures at %s — the "
                        "source appears to be throttling. Re-run later to resume.",
                        consecutive_failures, symbol,
                    )
                    stats["stopped_early"] = True
                    break
                continue

            consecutive_failures = 0
            if not history:
                stats["empty"] += 1
                continue

            rows = to_quotes(symbol, history, started)
            stats["ok"] += 1
            stats["rows"] += len(rows)
            pending.extend(rows)

            # Flushed every few hundred tickers, not every 20k rows. The source
            # starts refusing after a sustained run, and a job that dies before
            # its first flush loses everything — which is exactly what happened
            # with the larger threshold.
            if con is not None and len(pending) >= 5_000:
                stats["written"] += wh.append(con, "eq_quote", pending)
                pending = []

            if i % 100 == 0:
                log.info("%d/%d tickers (%d ok, %d rows)",
                         i, len(targets), stats["ok"], stats["rows"])

        if con is not None and pending:
            stats["written"] += wh.append(con, "eq_quote", pending)

        stats["elapsed_sec"] = round((wh.utcnow() - started).total_seconds(), 1)
        if con is not None:
            wh.log_run(con, run_id, "vn_history", started, "ok",
                       rows_in=stats["rows"], rows_new=stats["written"], detail=stats)
        return stats
    finally:
        if con is not None:
            con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill daily OHLC history")
    parser.add_argument("--symbols", help="Comma-separated tickers (default: all traded)")
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS, help="Trading days per ticker")
    parser.add_argument("--limit", type=int, help="Only the first N tickers")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--no-skip", action="store_true", help="Refetch tickers that already have history")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    stats = collect(symbols, args.bars, args.limit, args.dry_run, args.delay, not args.no_skip)
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
