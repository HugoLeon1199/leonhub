"""Market structure for VN equities: breadth, not gamma.

The crypto structure page is built on options — dealer gamma says how hedging
flow will behave, and the whole surface comes from one Deribit request. Vietnam
has no listed single-stock options, so that method has nothing to stand on here
and copying its vocabulary would be a costume rather than an analysis.

What a market without options still tells you is how broadly it is moving.
An index up 1% on a quarter of its members rising is a different market from
one up 1% with three quarters rising, and the difference is exactly what a
cap-weighted index hides. Every measure below reads from `eq_quote`, which
already holds 265 sessions across 1,729 tickers.

Breadth is computed on equal weight by construction: each ticker counts once.
That is the point -- it is the check on the index, not a second version of it.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from typing import Any

from pipeline.core import warehouse as wh
from pipeline.publish.emit import write_json

log = logging.getLogger(__name__)

# A ticker needs this many sessions before its moving averages mean anything.
MIN_HISTORY = 60
# Sessions of breadth history to publish. A year of daily points is enough to
# see a divergence build and small enough to ship with the page.
PUBLISH_SESSIONS = 260
# A session must cover this share of the median session's universe before its
# breadth is published. Coverage swings from 136 to 1,729 tickers depending on
# which collector last ran, and a percentage computed on a tenth of the market
# is not a smaller sample of the same thing -- it is a different market. A
# partial day plotted beside full ones reads as a breadth collapse that never
# happened.
MIN_COVERAGE = 0.6

BARS_SQL = """
SELECT DISTINCT ON (symbol, as_of)
    symbol, as_of, price, volume,
    coalesce(value, price * volume)                      AS turnover,
    coalesce(foreign_buy_value, 0)
      - coalesce(foreign_sell_value, 0)                  AS foreign_net,
    exchange
FROM eq_quote
WHERE price IS NOT NULL AND price > 0
ORDER BY symbol, as_of, fetched_at DESC
"""


def sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def _session_stats(
    day: date,
    rows: list[tuple[str, float, float | None, float | None, float | None]],
    history: dict[str, list[float]],
) -> dict[str, Any]:
    """One day of breadth. `history` holds closes up to and including today."""
    advancing = declining = unchanged = 0
    above50 = above200 = 0
    counted50 = counted200 = 0
    new_high = new_low = 0
    up_turnover = down_turnover = 0.0
    foreign_net = 0.0

    for symbol, price, _volume, turnover, fnet in rows:
        closes = history.get(symbol) or []
        if fnet:
            foreign_net += fnet

        if len(closes) >= 2:
            previous = closes[-2]
            if previous:
                if price > previous:
                    advancing += 1
                    up_turnover += turnover or 0
                elif price < previous:
                    declining += 1
                    down_turnover += turnover or 0
                else:
                    unchanged += 1

        ma50 = sma(closes, 50)
        if ma50 is not None:
            counted50 += 1
            if price >= ma50:
                above50 += 1
        ma200 = sma(closes, 200)
        if ma200 is not None:
            counted200 += 1
            if price >= ma200:
                above200 += 1

        # 52-week extremes, or the whole history where it is shorter. Stated as
        # a count rather than a list: which names are at highs is a screener
        # question, how many are is a structure question.
        window = closes[-252:]
        if len(window) >= MIN_HISTORY:
            if price >= max(window):
                new_high += 1
            elif price <= min(window):
                new_low += 1

    breadth = advancing + declining + unchanged
    flow_total = up_turnover + down_turnover
    return {
        "d": day.isoformat(),
        "n": breadth,
        "adv": advancing,
        "dec": declining,
        # Advance/decline ratio, capped so a day with two decliners does not
        # produce a spike that flattens every other point on the chart.
        "ad": round(min(advancing / declining, 10), 2) if declining else None,
        "a50": round(above50 / counted50 * 100, 1) if counted50 else None,
        "a200": round(above200 / counted200 * 100, 1) if counted200 else None,
        "nh": new_high,
        "nl": new_low,
        # Share of the day's money that went into rising names. The pure count
        # treats a 1bn ticker and a 1,000bn ticker alike; this does not.
        "uv": round(up_turnover / flow_total * 100, 1) if flow_total else None,
        "fn": round(foreign_net / 1e9, 1) if foreign_net else None,
    }


def _regime(latest: dict[str, Any]) -> dict[str, Any]:
    """Name the state and show the inputs that produced it.

    The rule is deliberately blunt and published beside its own reading: a
    regime label whose thresholds are hidden is an opinion wearing a number's
    clothes.
    """
    a50, a200 = latest.get("a50"), latest.get("a200")
    if a50 is None or a200 is None:
        return {"label": None, "why": "Chưa đủ lịch sử để tính đường trung bình"}

    if a200 >= 60 and a50 >= 55:
        label, why = "Lan rộng", "Đa số mã trên cả MA50 và MA200"
    elif a200 <= 35 and a50 <= 40:
        label, why = "Suy yếu diện rộng", "Đa số mã dưới cả MA50 và MA200"
    elif a50 >= 55 > a200:
        label, why = "Hồi phục sớm", "Ngắn hạn cải thiện nhưng xu hướng dài chưa xác nhận"
    elif a50 <= 40 < a200:
        label, why = "Điều chỉnh trong xu hướng tăng", "Ngắn hạn yếu, nền dài hạn còn giữ"
    else:
        label, why = "Phân hóa", "Không nhóm nào chiếm ưu thế rõ"

    return {
        "label": label,
        "why": why,
        "a50": a50,
        "a200": a200,
        "rule": "Ngưỡng: trên 60/55 là lan rộng, dưới 35/40 là suy yếu",
    }


def build(dry_run: bool = False) -> dict[str, Any]:
    con = wh.connect_reader()
    try:
        by_day: dict[date, list[tuple[str, float, float | None, float | None, float | None]]] = {}
        history: dict[str, list[float]] = {}
        order: list[date] = []
        for symbol, as_of, price, volume, turnover, fnet, _exchange in con.execute(
            BARS_SQL
        ).fetchall():
            by_day.setdefault(as_of, []).append((symbol, price, volume, turnover, fnet))
    finally:
        con.close()

    # The floor is relative to what this warehouse actually holds, so it stays
    # correct as the universe grows rather than being a number to revisit.
    counts = sorted(len(rows) for rows in by_day.values())
    median_universe = counts[len(counts) // 2] if counts else 0
    floor = median_universe * MIN_COVERAGE

    series: list[dict[str, Any]] = []
    partial: list[str] = []
    for day in sorted(by_day):
        rows = by_day[day]
        # Append today's close before measuring, so a moving average includes
        # the session being described rather than lagging it by one bar.
        for symbol, price, *_ in rows:
            history.setdefault(symbol, []).append(price)
        stats = _session_stats(day, rows, history)
        # History still advances for a thin session -- the closes are real and a
        # later moving average needs them -- but the day itself is not plotted.
        if len(rows) >= floor and stats["n"] >= 20:
            series.append(stats)
        elif len(rows) < floor:
            partial.append(day.isoformat())
        order.append(day)

    series = series[-PUBLISH_SESSIONS:]
    latest = series[-1] if series else {}

    payload = {
        "series": series,
        "latest": latest,
        "regime": _regime(latest) if latest else {},
        "sessions": len(series),
        "universe": len(history),
        "median_universe": median_universe,
        # Named so the page can say why the newest session may be absent rather
        # than looking simply out of date.
        "skipped_partial": partial[-5:],
        "source": {
            "name": "LEON warehouse (VCI, SSI, VPS)",
            "kind": "Độ rộng thị trường từ giá đóng cửa",
        },
        "note": (
            "Việt Nam không có quyền chọn niêm yết trên từng cổ phiếu, nên không "
            "tính được gamma như thị trường crypto. Cấu trúc ở đây đo bằng độ "
            "rộng: mỗi mã tính một phiếu, không theo vốn hóa."
        ),
    }

    stats = {
        "sessions": len(series),
        "universe": len(history),
        "latest": latest.get("d"),
        "regime": payload["regime"].get("label"),
        "skipped_partial": len(partial),
        "median_universe": median_universe,
    }
    if not dry_run:
        stats["path"] = str(write_json("breadth.json", payload))
    else:
        stats["sample"] = series[-2:]
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build VN market breadth")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(build(args.dry_run), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
