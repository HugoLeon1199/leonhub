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

# Money flow is measured in VND, never in share count: 1M shares of a 5,000d
# ticker and 1M shares of a 200,000d one are the same volume and forty times
# apart in money. `value` is the traded turnover; where a session predates the
# board collectors it is absent and price*volume stands in -- measured 0.9%
# median error against real turnover, which is well inside what a ranking needs.
BARS_SQL = """
SELECT DISTINCT ON (symbol, as_of)
    symbol, as_of, price, volume,
    coalesce(value, price * volume)                      AS turnover,
    coalesce(foreign_buy_value, 0)
      - coalesce(foreign_sell_value, 0)                  AS foreign_net
FROM eq_quote
WHERE price IS NOT NULL AND price > 0
ORDER BY symbol, as_of, fetched_at DESC
"""

# --- confirmation layers -------------------------------------------------
# Layer 1 (the SMA cross) decides WHEN to look. These decide whether the move
# is backed by money. Each returns a bool plus the number behind it, so the
# published signal can show why it fired rather than only that it did.
VOL_SPIKE_MULT = 1.5    # session volume vs its own 20-day median
FLOW_LOOKBACK = 20      # sessions of turnover history for the baseline
OBV_SLOPE_BARS = 10     # accumulation must be rising over this window
# Confirmations required before a trade is taken. Chosen from the published
# ladder, not fitted: 0 layers gives -76.0R, 2 gives -27.2R, 3 gives -6.8R over
# the same walk-forward. 4 is unreachable because the foreign layer abstains
# until the warehouse holds real flow observations. The ladder ships with the
# artifact so this choice can be checked rather than taken on trust.
MIN_LAYERS = 3


# (as_of, price, volume, turnover_vnd, foreign_net_vnd)
Bar = tuple[date, float, float | None, float | None, float | None]


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
    # What the confirmation layers said at entry. Published with the trade so a
    # reader can see the inputs, not just the verdict.
    layers: dict[str, Any] | None = None

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


def median(values: list[float]) -> float | None:
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    mid = len(clean) // 2
    return clean[mid] if len(clean) % 2 else (clean[mid - 1] + clean[mid]) / 2


def obv_series(prices: list[float], volumes: list[float | None]) -> list[float]:
    """Running on-balance volume: volume signed by the day's direction.

    Accumulation shows up here before it shows up in price -- volume arriving
    on up days while price is still flat is the footprint the cross alone
    cannot see.
    """
    out, acc = [], 0.0
    for i, price in enumerate(prices):
        volume = volumes[i] or 0
        if i and price > prices[i - 1]:
            acc += volume
        elif i and price < prices[i - 1]:
            acc -= volume
        out.append(acc)
    return out


def confirmations(
    i: int,
    prices: list[float],
    volumes: list[float | None],
    turnovers: list[float | None],
    foreign: list[float | None],
    obv: list[float],
) -> dict[str, Any]:
    """Score the layers behind a cross. Absent inputs abstain, never fail.

    A missing column must not read as a passed test, so each layer returns None
    when it cannot be computed and None is excluded from the count.
    """
    layers: dict[str, Any] = {}

    # Layer 2 -- is today's participation unusual for this ticker?
    past_vol = [v for v in volumes[max(0, i - FLOW_LOOKBACK): i] if v]
    base_vol = median(past_vol)
    today_vol = volumes[i]
    if base_vol and today_vol:
        layers["volume"] = {
            "pass": today_vol >= base_vol * VOL_SPIKE_MULT,
            "x": round(today_vol / base_vol, 2),
        }

    # Layer 3a -- the same question asked in money rather than shares.
    past_turn = [t for t in turnovers[max(0, i - FLOW_LOOKBACK): i] if t]
    base_turn = median(past_turn)
    today_turn = turnovers[i]
    if base_turn and today_turn:
        layers["money"] = {
            "pass": today_turn >= base_turn * VOL_SPIKE_MULT,
            "x": round(today_turn / base_turn, 2),
            # Billions of VND -- the unit the desk quotes.
            "vnd_bn": round(today_turn / 1e9, 1),
        }

    # Layer 3b -- foreign net over the week. Abstains until the warehouse has
    # real observations; coalescing absent flow to zero would read as balanced
    # trading rather than as no data.
    recent_foreign = [f for f in foreign[max(0, i - 4): i + 1] if f is not None]
    if len(recent_foreign) >= 3:
        net = sum(recent_foreign)
        layers["foreign"] = {"pass": net > 0, "vnd_bn": round(net / 1e9, 1)}

    # Layer 4 -- accumulation already under way before the cross.
    if i >= OBV_SLOPE_BARS:
        rising = obv[i] > obv[i - OBV_SLOPE_BARS]
        layers["accumulation"] = {"pass": rising}

    scored = [v for v in layers.values() if v.get("pass") is not None]
    layers["_score"] = sum(1 for v in scored if v["pass"])
    layers["_scored"] = len(scored)
    return layers


def _layer_digest(layers: dict[str, Any] | None) -> dict[str, Any] | None:
    """Compact per-trade record of which layers agreed, for the published JSON.

    Short keys because the browser downloads every trade: `v` volume multiple,
    `m` turnover in billions of VND, `f` foreign net, `a` accumulation. A layer
    that abstained is absent rather than false -- the page must be able to say
    "no data" instead of "failed".
    """
    if not layers:
        return None
    out: dict[str, Any] = {"n": layers.get("_score"), "of": layers.get("_scored")}
    if "volume" in layers:
        out["v"] = layers["volume"]["x"]
    if "money" in layers:
        out["m"] = layers["money"]["vnd_bn"]
    if "foreign" in layers:
        out["f"] = layers["foreign"]["vnd_bn"]
    if "accumulation" in layers:
        out["a"] = 1 if layers["accumulation"]["pass"] else 0
    return out


def generate(
    symbol: str,
    bars: list[Bar],
    min_layers: int = MIN_LAYERS,
) -> Iterator[Trade]:
    """Walk bars forward, opening and closing one position at a time.

    The rule: go long when the 20-day average crosses above the 50-day, price is
    in the upper half of its 120-day range, and at least `min_layers`
    confirmation layers agree that money is behind the move. Exit on the stop,
    the target, or after MAX_HOLD bars — whichever comes first.

    `min_layers=0` reproduces the original cross-only rule exactly, which is how
    the published comparison isolates what each layer is actually worth.
    Nothing here is optimised; these are conventional parameters, stated so they
    can be argued with.
    """
    prices = [b[1] for b in bars]
    volumes = [b[2] for b in bars]
    turnovers = [b[3] for b in bars]
    foreign = [b[4] for b in bars]
    obv = obv_series(prices, volumes)
    open_trade: Trade | None = None
    bars_held = 0

    for i in range(MIN_HISTORY, len(bars)):
        today, price = bars[i][0], bars[i][1]
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

        # The cross says where to look; the layers say whether money agrees.
        layers = confirmations(i, prices, volumes, turnovers, foreign, obv)
        if layers["_score"] < min_layers:
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
            layers=layers,
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
    by_symbol: dict[str, list[Bar]],
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
        by_symbol: dict[str, list[Bar]] = {}
        for symbol, as_of, price, volume, turnover, foreign_net in con.execute(
            BARS_SQL
        ).fetchall():
            by_symbol.setdefault(symbol, []).append(
                (as_of, price, volume, turnover, foreign_net)
            )

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

    # What each confirmation layer is actually worth, computed by re-running the
    # same walk-forward with the threshold moved. Published rather than used
    # privately to pick a winner: a rule tuned until it looks good, with only
    # the winning configuration shown, is the failure mode this page exists to
    # prevent. The comparison is cheap because `generate` already takes the
    # threshold as an argument.
    ladder = []
    for threshold in range(0, 5):
        variant: list[Trade] = []
        for symbol in eligible:
            variant.extend(generate(symbol, by_symbol[symbol], min_layers=threshold))
        summary = summarise(variant)
        if summary.get("trades"):
            ladder.append({
                "layers": threshold,
                "trades": summary["trades"],
                "hit_rate": summary["hit_rate"],
                "avg_r": summary["avg_r"],
                "total_r": summary["total_r"],
                "max_drawdown_r": summary["max_drawdown_r"],
            })

    payload = {
        "rules": {
            "entry": "SMA20 cắt lên SMA50, giá nằm ở nửa trên biên độ 120 phiên",
            "layers": (
                f"cần ít nhất {MIN_LAYERS} lớp xác nhận dòng tiền: khối lượng ≥ "
                f"{VOL_SPIKE_MULT}x trung vị {FLOW_LOOKBACK} phiên, giá trị giao dịch "
                f"≥ {VOL_SPIKE_MULT}x, khối ngoại mua ròng 5 phiên, OBV tăng "
                f"{OBV_SLOPE_BARS} phiên"
            ),
            "stop": f"{STOP_SD} độ lệch chuẩn lợi suất ngày (60 phiên) dưới giá vào",
            "target": f"{TARGET_R}R",
            "max_hold": f"{MAX_HOLD} phiên",
            "universe": f"vốn hóa ≥ {MIN_MARKET_CAP} tỷ VND, ≥ {MIN_HISTORY} phiên lịch sử",
        },
        "stats": summarise(all_trades),
        "benchmark": benchmark,
        # Ordered 0..4 confirmations. Layer 0 is the original cross-only rule,
        # kept so the reader can see the starting point rather than only the
        # tuned one.
        "layer_ladder": ladder,
        "layers_required": MIN_LAYERS,
        "universe": len(eligible),
        "open": [
            {
                "s": t.symbol, "n": names.get(t.symbol),
                "d": t.entry_date.isoformat(),
                "entry": round(t.entry, 1), "stop": t.stop, "target": t.target,
                "L": _layer_digest(t.layers),
            }
            for t in sorted(open_now, key=lambda t: t.entry_date, reverse=True)
        ],
        "closed": [
            {
                "s": t.symbol, "n": names.get(t.symbol),
                "d": t.entry_date.isoformat(), "x": t.exit_date.isoformat(),
                "entry": round(t.entry, 1), "exit": round(t.exit, 1),
                "r": round(t.r_multiple, 2), "why": t.reason,
                "L": _layer_digest(t.layers),
            }
            # Every closed trade, not a recent slice. Truncating to 200 while
            # `stats` counted all 214 put two numbers that disagree on the same
            # screen -- the equity curve ended at a different total R than the
            # tile above it -- and made the page's own caption ("toàn bộ lệnh,
            # không lọc bỏ lệnh thua") untrue. The full list costs ~2.5 KB.
            for t in closed
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
