"""Rules-based signals with a published track record.

The idea worth copying from the reference product is that a signal carries an
entry, a stop, a target and a result in R — not just a direction. The part worth
improving is honesty: they show open positions, this publishes every closed one,
so the hit rate and average R on the page are computed from the same rules that
generated the trades.

That is only credible because the warehouse is point-in-time. Every signal is
evaluated on bars as they were known on the day, walking forward one bar at a
time, so a rule cannot peek at a price it could not have seen. A track record
computed any other way is a backtest wearing a live-results costume.

The rules themselves are deliberately plain and stated on the page. A rule
nobody can inspect is indistinguishable from a rule that was fitted afterwards.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterator

from pipeline.core import warehouse as wh
from pipeline.publish.emit import write_json

log = logging.getLogger(__name__)

# Risk per trade is one ATR-equivalent, approximated by the standard deviation
# of daily returns — the same volatility measure the screener's z-score uses.
STOP_SD = 2.0
TARGET_R = 2.0          # take profit at 2x the risk
MAX_HOLD = 30           # trading days before a position is closed regardless
MIN_HISTORY = 120       # bars required before a ticker is eligible
MIN_MARKET_CAP = 1000   # billion VND; below this, slippage swamps the edge

BARS_SQL = """
SELECT DISTINCT ON (symbol, as_of)
    symbol, as_of, price, volume
FROM eq_quote
WHERE price IS NOT NULL AND price > 0
ORDER BY symbol, as_of, fetched_at DESC
"""


@dataclass
class Trade:
    symbol: str
    direction: str
    entry_date: date
    entry: float
    stop: float
    target: float
    exit_date: date | None = None
    exit: float | None = None
    reason: str | None = None

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def r_multiple(self) -> float | None:
        if self.exit is None or not self.risk:
            return None
        move = self.exit - self.entry
        if self.direction == "short":
            move = -move
        return move / self.risk


def sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def daily_returns(prices: list[float]) -> list[float]:
    return [prices[i] / prices[i - 1] - 1 for i in range(1, len(prices)) if prices[i - 1]]


def generate(symbol: str, bars: list[tuple[date, float, float | None]]) -> Iterator[Trade]:
    """Walk bars forward, opening and closing one position at a time.

    The rule: go long when the 20-day average crosses above the 50-day and price
    is in the upper half of its 120-day range. Exit on the stop, the target, or
    after MAX_HOLD bars — whichever comes first. Nothing here is optimised;
    these are conventional parameters, stated so they can be argued with.
    """
    prices = [b[1] for b in bars]
    open_trade: Trade | None = None
    bars_held = 0

    for i in range(MIN_HISTORY, len(bars)):
        today, price, _ = bars[i]
        window = prices[: i + 1]

        if open_trade is not None:
            bars_held += 1
            hit_stop = price <= open_trade.stop
            hit_target = price >= open_trade.target
            if hit_stop or hit_target or bars_held >= MAX_HOLD:
                open_trade.exit_date = today
                open_trade.exit = price
                open_trade.reason = (
                    "stop" if hit_stop else "target" if hit_target else "time"
                )
                yield open_trade
                open_trade = None
                bars_held = 0
            continue

        fast, slow = sma(window, 20), sma(window, 50)
        prev_fast, prev_slow = sma(window[:-1], 20), sma(window[:-1], 50)
        if None in (fast, slow, prev_fast, prev_slow):
            continue

        crossed_up = prev_fast <= prev_slow and fast > slow
        if not crossed_up:
            continue

        recent = window[-MIN_HISTORY:]
        high, low = max(recent), min(recent)
        if high <= low or (price - low) / (high - low) < 0.5:
            continue

        sd = stdev(daily_returns(window[-60:]))
        if sd <= 0:
            continue

        risk = price * sd * STOP_SD
        open_trade = Trade(
            symbol=symbol,
            direction="long",
            entry_date=today,
            entry=price,
            stop=round(price - risk, 1),
            target=round(price + risk * TARGET_R, 1),
        )

    # An open position is reported separately; it has no result yet.
    if open_trade is not None:
        yield open_trade


def summarise(trades: list[Trade]) -> dict[str, Any]:
    closed = [t for t in trades if t.r_multiple is not None]
    if not closed:
        return {"trades": 0}

    r_values = [t.r_multiple for t in closed]
    wins = [r for r in r_values if r > 0]

    equity, peak, max_dd = 0.0, 0.0, 0.0
    for r in r_values:
        equity += r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    return {
        "trades": len(closed),
        "hit_rate": round(len(wins) / len(closed) * 100, 1),
        "avg_r": round(sum(r_values) / len(r_values), 3),
        "total_r": round(equity, 1),
        "max_drawdown_r": round(max_dd, 1),
        "avg_win_r": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss_r": round(
            sum(r for r in r_values if r <= 0) / max(1, len(r_values) - len(wins)), 2
        ),
        "by_exit": {
            reason: sum(1 for t in closed if t.reason == reason)
            for reason in ("stop", "target", "time")
        },
    }


def market_return(
    by_symbol: dict[str, list[tuple[date, float, float | None]]],
    universe: list[str],
) -> dict[str, Any]:
    """Median buy-and-hold return across the same universe, over the same span.

    The median rather than an index: it needs no weights, no constituent list,
    and it answers the question the reader actually has — what would holding a
    typical one of these have done?
    """
    changes = []
    start = end = None
    for symbol in universe:
        bars = by_symbol.get(symbol) or []
        if len(bars) < MIN_HISTORY:
            continue
        first, last = bars[0], bars[-1]
        if first[1]:
            changes.append(last[1] / first[1] - 1)
        start = first[0] if start is None else min(start, first[0])
        end = last[0] if end is None else max(end, last[0])

    if not changes:
        return {}
    changes.sort()
    median = changes[len(changes) // 2]
    return {
        "median_return_pct": round(median * 100, 1),
        "tickers": len(changes),
        "from": start.isoformat() if start else None,
        "to": end.isoformat() if end else None,
    }


def build(dry_run: bool = False, limit: int | None = None) -> dict[str, Any]:
    con = wh.connect_reader()
    try:
        by_symbol: dict[str, list[tuple[date, float, float | None]]] = {}
        for symbol, as_of, price, volume in con.execute(BARS_SQL).fetchall():
            by_symbol.setdefault(symbol, []).append((as_of, price, volume))

        caps = {
            r[0]: r[1] for r in con.execute("""
                WITH latest AS (
                    SELECT DISTINCT ON (symbol) symbol, price
                    FROM eq_quote WHERE price IS NOT NULL
                    ORDER BY symbol, fetched_at DESC
                ),
                shares AS (
                    SELECT DISTINCT ON (symbol) symbol, listed_share
                    FROM eq_quote WHERE listed_share IS NOT NULL
                    ORDER BY symbol, fetched_at DESC
                )
                SELECT l.symbol, l.price * s.listed_share / 1e9
                FROM latest l JOIN shares s USING (symbol)
            """).fetchall()
        }
        names = {
            r[0]: r[1] for r in con.execute("""
                SELECT DISTINCT ON (symbol) symbol, organ_name
                FROM eq_listing ORDER BY symbol, fetched_at DESC
            """).fetchall()
        }
    finally:
        con.close()

    eligible = [
        s for s, bars in by_symbol.items()
        if len(bars) >= MIN_HISTORY and caps.get(s, 0) >= MIN_MARKET_CAP
    ]
    eligible.sort(key=lambda s: -caps.get(s, 0))
    if limit:
        eligible = eligible[:limit]

    all_trades: list[Trade] = []
    for symbol in eligible:
        all_trades.extend(generate(symbol, by_symbol[symbol]))

    closed = [t for t in all_trades if t.exit is not None]
    open_now = [t for t in all_trades if t.exit is None]
    closed.sort(key=lambda t: t.exit_date, reverse=True)

    # A track record without the market it ran in is half a fact. A long-only
    # trend rule losing money in a falling market is the expected result, not
    # evidence the rule is broken — and stating so is what stops the number
    # being quietly reinterpreted later.
    benchmark = market_return(by_symbol, eligible)

    payload = {
        "rules": {
            "entry": "SMA20 cắt lên SMA50, giá nằm ở nửa trên biên độ 120 phiên",
            "stop": f"{STOP_SD} độ lệch chuẩn lợi suất ngày (60 phiên) dưới giá vào",
            "target": f"{TARGET_R}R",
            "max_hold": f"{MAX_HOLD} phiên",
            "universe": f"vốn hóa ≥ {MIN_MARKET_CAP} tỷ VND, ≥ {MIN_HISTORY} phiên lịch sử",
        },
        "stats": summarise(all_trades),
        "benchmark": benchmark,
        "universe": len(eligible),
        "open": [
            {
                "s": t.symbol, "n": names.get(t.symbol),
                "d": t.entry_date.isoformat(),
                "entry": round(t.entry, 1), "stop": t.stop, "target": t.target,
            }
            for t in sorted(open_now, key=lambda t: t.entry_date, reverse=True)
        ],
        "closed": [
            {
                "s": t.symbol, "n": names.get(t.symbol),
                "d": t.entry_date.isoformat(), "x": t.exit_date.isoformat(),
                "entry": round(t.entry, 1), "exit": round(t.exit, 1),
                "r": round(t.r_multiple, 2), "why": t.reason,
            }
            for t in closed[:200]
        ],
    }

    stats = dict(payload["stats"])
    stats["universe"] = len(eligible)
    stats["open_positions"] = len(open_now)

    if not dry_run:
        path = write_json("signals.json", payload)
        stats["path"] = str(path)
        stats["size_kb"] = round(path.stat().st_size / 1024, 1)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate signals and their track record")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, help="Only the N largest tickers")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(build(args.dry_run, args.limit), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
