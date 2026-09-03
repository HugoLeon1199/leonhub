"""SSI iBoard collector — live board depth and foreign room.

What this adds over the Vietcap board already in `vn_equity.py`: three levels of
bid/ask depth, foreign buy/sell split in both quantity and value, and
`remainForeignQtty` — the foreign ownership room left in each ticker. Room is
the constraint that decides whether foreign demand can actually be expressed,
and no free VN screener publishes it.

Two constraints found by probing:

- **CORS is restricted to iboard.ssi.com.vn.** The endpoint answers a plain
  request fine but sets `Access-Control-Allow-Origin` to SSI's own board, so a
  browser on our origin is refused. This has to run pipeline-side.
- **It rate-limits hard.** Three exchange calls back to back return 403; spaced
  a few seconds apart they all return 200. The delay below is not politeness
  theatre, it is the difference between data and nothing.

Prices arrive in VND (FPT 72200), matching the existing eq_quote convention.
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from datetime import datetime, date
from typing import Any

from pipeline.core.http import HttpClient
from pipeline.core import warehouse as wh

log = logging.getLogger(__name__)

BASE = "https://iboard-query.ssi.com.vn/stock/exchange"
EXCHANGES = ("hose", "hnx", "upcom")
HEADERS = {
    "Accept": "application/json",
    "Referer": "https://iboard.ssi.com.vn/",
}
# Back-to-back calls are refused with 403; a few seconds apart they all succeed.
DELAY = 6.0


def _num(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # drop NaN


def fetch_exchange(client: HttpClient, exchange: str) -> list[dict[str, Any]]:
    payload = client.get_json(f"{BASE}/{exchange}", headers=HEADERS)
    data = payload.get("data")
    return data if isinstance(data, list) else []


def to_quote(rec: dict[str, Any], fetched_at: datetime) -> dict[str, Any] | None:
    """Map an iBoard row onto the eq_quote schema."""
    symbol = rec.get("stockSymbol")
    if not isinstance(symbol, str) or not symbol.strip():
        return None

    trading_date = rec.get("tradingDate")
    try:  # arrives as 20260903
        as_of = datetime.strptime(str(trading_date), "%Y%m%d").date()
    except (TypeError, ValueError):
        as_of = date.today()

    buy_qty = _num(rec.get("buyForeignQtty"))
    sell_qty = _num(rec.get("sellForeignQtty"))

    return {
        "symbol": symbol.strip(),
        "as_of": as_of,
        "fetched_at": fetched_at,
        "exchange": rec.get("exchange"),
        "price": _num(rec.get("matchedPrice")),
        "ref_price": _num(rec.get("refPrice")),
        "open_price": _num(rec.get("openPrice")),
        "high": _num(rec.get("highest")),
        "low": _num(rec.get("lowest")),
        "volume": _num(rec.get("nmTotalTradedQty")),
        "value": _num(rec.get("nmTotalTradedValue")),
        "listed_share": None,   # not on this endpoint; Vietcap supplies it
        "foreign_buy_value": _num(rec.get("buyForeignValue")),
        "foreign_sell_value": _num(rec.get("sellForeignValue")),
        "foreign_buy_vol": buy_qty,
        "foreign_sell_vol": sell_qty,
    }


def to_metrics(rec: dict[str, Any], as_of: datetime, fetched_at: datetime) -> list[dict[str, Any]]:
    """Depth and foreign room, kept in the generic series table.

    These do not belong in eq_quote: they are point-in-time microstructure, not
    the daily bar, and adding six columns to a table read by every build would
    cost more than it earns.
    """
    symbol = rec.get("stockSymbol")
    rows = []
    fields = {
        "bid1": rec.get("best1Bid"),
        "bid1_vol": rec.get("best1BidVol"),
        "ask1": rec.get("best1Offer"),
        "ask1_vol": rec.get("best1OfferVol"),
        "foreign_room": rec.get("remainForeignQtty"),
    }
    for name, raw in fields.items():
        value = _num(raw)
        if value is None:
            continue
        rows.append({
            "series": f"vn.board.{symbol}.{name}",
            "as_of": as_of,
            "value": value,
            "source": "ssi_iboard",
            "fetched_at": fetched_at,
            "meta": json.dumps({"symbol": symbol, "field": name}),
        })
    return rows


def collect(
    exchanges: tuple[str, ...] = EXCHANGES,
    dry_run: bool = False,
    with_depth: bool = False,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    started = wh.utcnow()
    client = HttpClient(delay=DELAY, retries=3)

    quotes: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    stats: dict[str, Any] = {"exchanges": {}}

    for exchange in exchanges:
        try:
            records = fetch_exchange(client, exchange)
        except Exception as exc:
            log.warning("%s failed: %s", exchange, exc)
            stats["exchanges"][exchange] = {"error": str(exc)[:120]}
            continue

        priced = 0
        for rec in records:
            quote = to_quote(rec, started)
            if not quote:
                continue
            quotes.append(quote)
            if quote["price"]:
                priced += 1
            if with_depth:
                metrics.extend(to_metrics(rec, started, started))

        stats["exchanges"][exchange] = {"rows": len(records), "priced": priced}
        log.info("%s: %d rows, %d priced", exchange, len(records), priced)

    stats["quotes"] = len(quotes)
    stats["metrics"] = len(metrics)
    net = sum(
        (q.get("foreign_buy_value") or 0) - (q.get("foreign_sell_value") or 0)
        for q in quotes
    )
    stats["foreign_net_value"] = round(net, 0)
    stats["elapsed_sec"] = round((wh.utcnow() - started).total_seconds(), 1)

    if dry_run:
        return stats

    con = wh.connect()
    try:
        stats["quotes_new"] = wh.append(con, "eq_quote", quotes)
        if metrics:
            stats["metrics_new"] = wh.append(con, "metric_ts", metrics)
        wh.log_run(
            con, run_id, "ssi_board", started, "ok",
            rows_in=len(quotes) + len(metrics),
            rows_new=stats.get("quotes_new", 0) + stats.get("metrics_new", 0),
            detail=stats,
        )
    finally:
        con.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect the SSI iBoard price board")
    parser.add_argument("--exchanges", default=",".join(EXCHANGES))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--with-depth", action="store_true",
        help="Also store bid/ask level 1 and remaining foreign room",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    exchanges = tuple(e.strip().lower() for e in args.exchanges.split(",") if e.strip())
    print(json.dumps(collect(exchanges, args.dry_run, args.with_depth),
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
