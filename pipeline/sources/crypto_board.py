"""Market-wide crypto board: the screener the stocks tab has, for coins.

The chart tab shows one instrument at a time and the GEX tab covers two. Neither
answers "what is moving across the whole market right now", which is the
question the equity screener exists to answer and the reason this collector is
its counterpart rather than a third chart.

One unauthenticated request returns every symbol Binance lists, so the cost is
a single call regardless of universe size. Only USDT quote pairs above a
turnover floor are kept: below it the 24h percentage is dominated by a handful
of trades and ranks noise to the top of a sorted table.

Binance geo-blocks datacenter IPs, so this runs from the workstation rather
than Actions -- the same constraint documented for the browser-side chart, with
the opposite resolution because a collector has no user's browser to borrow.
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from typing import Any

from pipeline.core import warehouse as wh
from pipeline.core.http import HttpClient
from pipeline.publish.emit import write_json

log = logging.getLogger(__name__)

TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"

# 24h quote turnover in USD. Below this the percentage move is a few trades
# rather than a market, and a screener sorted by change would surface only the
# illiquid names nobody can actually transact in.
MIN_TURNOVER_USD = 1_000_000

# Stablecoins quoted against USDT price a peg, not an asset: a 0.01% move is
# noise on a chart whose whole range is 0.2%, and they otherwise occupy the top
# of every turnover ranking without carrying information.
# Any instrument that tracks a peg rather than an asset. RLUSD and EUR were
# missed on the first pass and immediately dominated the large-order view with
# ~0% moves, which is the tell: a peg trades in size precisely because it is not
# supposed to move. Fiat tickers belong here for the same reason.
PEGGED = {
    "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EURI", "AEUR", "USD1",
    "RLUSD", "PYUSD", "USDE", "USDS", "EUR", "GBP", "TRY", "BRL", "ARS", "JPY",
}


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # NaN check


def fetch(client: HttpClient) -> list[dict[str, Any]]:
    rows = client.get_json(TICKER_URL)
    if not isinstance(rows, list):
        raise RuntimeError("Binance 24h ticker did not return a list")
    return rows


def _published_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    symbol = raw.get("symbol") or ""
    if not symbol.endswith("USDT"):
        return None
    base = symbol[:-4]
    if not base or base in PEGGED:
        return None

    turnover = _num(raw.get("quoteVolume"))
    price = _num(raw.get("lastPrice"))
    if not turnover or not price or turnover < MIN_TURNOVER_USD:
        return None

    high, low = _num(raw.get("highPrice")), _num(raw.get("lowPrice"))
    trades = raw.get("count")
    # Average trade size in USD. Two coins can print the same turnover with
    # wildly different participation -- a handful of large tickets versus a
    # crowd of small ones -- and that difference is the whole point of showing
    # it beside the volume rather than only the volume.
    avg_ticket = turnover / trades if trades else None

    # Where the last price sits inside the day's range. Near 100 the market
    # closed on its high, near 0 on its low; it says something the percentage
    # change alone does not, because a coin can be up on the day and still
    # be selling off from its peak.
    pos = None
    if high is not None and low is not None and high > low:
        pos = round((price - low) / (high - low) * 100)

    return {
        "s": base,
        "p": price,
        "chp": _num(raw.get("priceChangePercent")),
        # Millions of USD -- the unit a desk quotes crypto turnover in.
        "v": round(turnover / 1e6, 2),
        "h": high,
        "l": low,
        "pos": pos,
        "t": int(trades) if trades else None,
        "avg": round(avg_ticket) if avg_ticket else None,
    }


def collect(dry_run: bool = False, delay: float = 0.2) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    started = wh.utcnow()
    # Binance geo-blocks datacenter IPs, so HttpClient refuses it by default.
    # The flag is safe to set here: __post_init__ forces it back off whenever
    # GITHUB_ACTIONS is set, so a CI run fails loudly instead of silently
    # collecting nothing.
    client = HttpClient(delay=delay, retries=2, timeout=30, allow_browser_only=True)

    raw_rows = fetch(client)
    rows = [row for row in (_published_row(r) for r in raw_rows) if row]
    rows.sort(key=lambda r: -(r["v"] or 0))

    # One observation per coin per run, so the board accumulates the same
    # point-in-time history every other source keeps rather than only ever
    # describing this instant.
    metrics = [
        {
            "series": f"crypto.{row['s']}.turnover_musd",
            "as_of": started,
            "value": row["v"],
            "source": "binance_24hr",
            "fetched_at": started,
        }
        for row in rows
    ]

    stats: dict[str, Any] = {
        "symbols_seen": len(raw_rows),
        "rows": len(rows),
        "min_turnover_usd": MIN_TURNOVER_USD,
    }
    if dry_run:
        stats["sample"] = rows[:3]
        return stats

    con = wh.connect()
    try:
        stats["metric_rows_new"] = wh.append(con, "metric_ts", metrics)
        wh.log_run(
            con, run_id, "crypto_board", started, "ok",
            rows_in=len(metrics), rows_new=stats["metric_rows_new"], detail=stats,
        )
    finally:
        con.close()

    stats["path"] = str(write_json("crypto.json", {
        "rows": rows,
        "min_turnover_usd": MIN_TURNOVER_USD,
        "source": {
            "name": "Binance", "kind": "Bảng giá 24 giờ",
            "url": "https://www.binance.com/",
        },
    }))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect the Binance 24h board")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(collect(args.dry_run, args.delay), indent=2, default=str))


if __name__ == "__main__":
    main()
