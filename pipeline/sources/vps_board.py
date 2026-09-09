"""VN board depth, foreign flow and ownership room from the VPS feed.

This replaces `ssi_board.py` as the primary source for the same fields, for one
reason: SSI's iBoard endpoint is not an API we are entitled to. It carries no
key, and the collector reaches it by sending `Referer: iboard.ssi.com.vn` to
impersonate their own web client. Measured 2026-09-08, it refuses the third
back-to-back call with 403 and then blocks the IP outright -- spacing requests
6, 8 or 10 seconds apart did not recover it, and it was still refusing eight
minutes later. A daily job resting on that is one burst away from silence.

SSI does publish a real API (FastConnect Data, `fc-data.ssi.com.vn`), but it
requires an SSI trading account and in-branch registration, so it cannot be
adopted unilaterally. `ssi_board.py` is kept as a fallback for whoever has those
credentials, and for cross-checking.

VPS carries the same fields and more, tolerates batching, and is already the
source behind the chart:

    350 symbols in 2.6s in one request; six consecutive calls all 200.

Two conventions to respect, both of which silently corrupt data if missed:

- **Prices are in thousands of VND** (249.6 for a 249,600 close), matching
  `PRICE_SCALE` in `vn_history.py`. `closePrice` is the exception and arrives in
  plain VND.
- **Depth arrives as a packed string**, `"249.6|5360|i"` -- price, volume, and a
  flag. Bids are g1..g3, asks g4..g6.
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from datetime import date, datetime
from typing import Any

from pipeline.core import warehouse as wh
from pipeline.core.http import HttpClient

log = logging.getLogger(__name__)

BASE = "https://bgapidatafeed.vps.com.vn/getliststockdata"

# 400 requested returned 350 rows in 2.6s; 200 returned 174. The endpoint
# truncates rather than erroring, so the batch is kept well inside that and the
# caller checks what came back rather than assuming.
BATCH = 150
DELAY = 1.0

# Board prices are quoted in thousands of VND, the same convention VPS uses for
# chart history. Everything written to eq_quote is plain VND.
PRICE_SCALE = 1000.0

# Foreign buy/sell VALUE is scaled down by 100 -- see the note in `to_quote`.
# Measured, not documented, so `_audit_foreign` re-checks it on every run.
FOREIGN_VALUE_SCALE = 100.0


def _num(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _price(value: Any) -> float | None:
    raw = _num(value)
    return raw * PRICE_SCALE if raw is not None else None


def _scaled(value: Any, factor: float) -> float | None:
    raw = _num(value)
    return raw * factor if raw is not None else None


def _level(raw: Any) -> tuple[float | None, float | None]:
    """Unpack one depth level: "249.6|5360|i" -> (249600.0, 5360.0).

    A zero price is how the feed says "no order at this level"; it must not be
    stored as a real bid at 0.
    """
    if not isinstance(raw, str) or "|" not in raw:
        return None, None
    parts = raw.split("|")
    price, volume = _num(parts[0]), _num(parts[1]) if len(parts) > 1 else None
    if not price or price <= 0:
        return None, None
    return price * PRICE_SCALE, volume


def to_quote(rec: dict[str, Any], as_of: date, fetched_at: datetime) -> dict[str, Any] | None:
    symbol = rec.get("sym")
    if not isinstance(symbol, str) or not symbol.strip():
        return None
    return {
        "symbol": symbol.strip(),
        "as_of": as_of,
        "fetched_at": fetched_at,
        "exchange": None,          # the feed returns a numeric market code only
        "price": _price(rec.get("lastPrice")),
        "ref_price": _price(rec.get("r")),
        "open_price": _price(rec.get("openPrice")),
        "high": _price(rec.get("highPrice")),
        "low": _price(rec.get("lowPrice")),
        "volume": _num(rec.get("lot")),
        # Foreign value arrives in plain VND already, unlike the price fields.
        "value": None,             # not published per symbol on this endpoint
        "listed_share": None,      # Vietcap supplies it
        # fBValue/fSValue arrive scaled down by 100. Dividing the reported
        # value by the reported volume yields 2,534 for VIC where the traded
        # price is 251,400 VND; the same factor holds across every symbol
        # checked (93.6x-100.4x on eight names, the spread being session
        # average against last price). Left uncorrected, market-wide foreign
        # flow reads as tens of millions of dong instead of billions -- small
        # enough to look like a rounding artifact rather than an error.
        "foreign_buy_value": _scaled(rec.get("fBValue"), FOREIGN_VALUE_SCALE),
        "foreign_sell_value": _scaled(rec.get("fSValue"), FOREIGN_VALUE_SCALE),
        "foreign_buy_vol": _num(rec.get("fBVol")),
        "foreign_sell_vol": _num(rec.get("fSVolume")),
    }


def to_metrics(rec: dict[str, Any], as_of: date, fetched_at: datetime) -> list[dict[str, Any]]:
    """Depth, room and put-through, in the generic series table.

    Same shape `ssi_board.py` writes, so the transform and ticker page read
    either source without a special case.
    """
    symbol = rec.get("sym")
    if not isinstance(symbol, str) or not symbol.strip():
        return []

    # fRoom arrives scaled down by ten against the real remaining share count.
    # Verified against listed shares and the published room percentage: VIC is
    # 44.8% of 7,762,186,429 = 3.48bn shares, SSI reports 3,480,819,285, VPS
    # reports 348,204,472. Publishing the raw figure would understate available
    # foreign room by 90% -- a plausible-looking number that is simply wrong.
    room = _num(rec.get("fRoom"))
    fields: dict[str, Any] = {"foreign_room": room * 10 if room is not None else None}
    # g1-g3 bids, g4-g6 asks. Named by level rather than by slot so a feed that
    # publishes fewer levels simply contributes fewer rows.
    for level in (1, 2, 3):
        bid_price, bid_vol = _level(rec.get(f"g{level}"))
        ask_price, ask_vol = _level(rec.get(f"g{level + 3}"))
        fields.update({
            f"bid{level}": bid_price, f"bid{level}_vol": bid_vol,
            f"ask{level}": ask_price, f"ask{level}_vol": ask_vol,
        })
    # Put-through volume: institutional blocks settle outside the order book,
    # and SSI's endpoint does not publish it at all.
    pt = _num(rec.get("ptVol"))
    if pt:
        fields["put_through_vol"] = pt

    # Same `series` naming as ssi_board writes, so the transform and the ticker
    # page read either source without knowing which produced the row. `source`
    # is what distinguishes them when the two are compared.
    name = symbol.strip()
    return [{
        "series": f"vn.board.{name}.{field}",
        "as_of": as_of,
        "value": value,
        "source": "vps_board",
        "fetched_at": fetched_at,
        "meta": json.dumps({"symbol": name, "field": field}),
    } for field, value in fields.items() if value is not None]


def _audit_foreign(quotes: list[dict[str, Any]]) -> dict[str, Any]:
    """Check corrected foreign value against volume x price.

    Value divided by volume must land near the traded price. If the feed drops
    or adds a factor of 100 the ratio moves by two orders of magnitude, which
    this catches; a genuine gap of a few percent (session average against last
    price) does not.
    """
    checked, off = 0, []
    for q in quotes:
        price = q.get("price")
        vol, value = q.get("foreign_buy_vol"), q.get("foreign_buy_value")
        if not price or not vol or not value or vol <= 0:
            continue
        checked += 1
        implied = value / vol
        if not (0.5 < implied / price < 2):
            off.append(f"{q['symbol']}={implied:,.0f}v{price:,.0f}")
    return {"checked": checked, "off_vs_price": len(off), "sample": off[:5]}


def _audit_room(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """Sanity-check corrected foreign room against listed shares.

    Room is a share count that cannot exceed the shares in issue, and for most
    tickers should land near the published room percentage. Both directions
    matter: too small means the x10 correction stopped being needed, too large
    means it is now double-counting.
    """
    from pipeline.publish.emit import read_json
    board = {r["s"]: r for r in ((read_json("stocks.json") or {}).get("rows") or []) if r.get("s")}
    over, mismatched, checked = [], [], 0
    for row in metrics:
        if not row["series"].endswith(".foreign_room"):
            continue
        symbol = row["series"].split(".")[2]
        ref = board.get(symbol) or {}
        shares, pct = ref.get("sh"), ref.get("fr")
        if not shares:
            continue
        checked += 1
        if row["value"] > shares:
            over.append(symbol)
        elif pct is not None and pct > 1:
            implied = shares * pct / 100
            if implied > 0 and not (0.5 < row["value"] / implied < 2):
                mismatched.append(symbol)
    return {
        "checked": checked,
        "exceeds_listed": len(over),
        "off_vs_published_pct": len(mismatched),
        "sample": (over or mismatched)[:5],
    }


def universe() -> list[str]:
    """Symbols to poll: whatever the published board already covers."""
    from pipeline.publish.emit import read_json
    payload = read_json("stocks.json") or {}
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    return [r["s"] for r in (rows or []) if r.get("s")]


def collect(symbols: list[str] | None = None, dry_run: bool = False,
            delay: float = DELAY) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    started = wh.utcnow()
    as_of = started.date()
    client = HttpClient(delay=delay, retries=2, timeout=25)
    targets = symbols or universe()
    if not targets:
        return {"run_id": run_id, "error": "no symbols; build stocks.json first"}

    quotes: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    missing: list[str] = []

    for start in range(0, len(targets), BATCH):
        chunk = targets[start:start + BATCH]
        try:
            rows = client.get_json(f"{BASE}/{','.join(chunk)}")
        except Exception as exc:  # noqa: BLE001
            log.warning("batch at %d failed: %s", start, exc)
            missing.extend(chunk)
            continue
        if not isinstance(rows, list):
            missing.extend(chunk)
            continue
        # The endpoint truncates a long list silently, so record what it left
        # out rather than assuming the batch came back whole.
        returned = {r.get("sym") for r in rows if isinstance(r, dict)}
        missing.extend([s for s in chunk if s not in returned])
        for rec in rows:
            if not isinstance(rec, dict):
                continue
            quote = to_quote(rec, as_of, started)
            if quote and quote.get("price"):
                quotes.append(quote)
            metrics.extend(to_metrics(rec, as_of, started))

    # The x10 room correction is a measured constant, not a documented one, so
    # it is re-checked on every run rather than trusted. Remaining foreign room
    # cannot exceed the listed share count; if it does, the feed changed scale
    # and the correction is now wrong in the other direction.
    room_check = _audit_room(metrics)
    # Same reasoning as the room audit: the x100 correction is measured rather
    # than documented, and a feed that changes scale would otherwise publish a
    # wrong number that still looks like money.
    foreign_check = _audit_foreign(quotes)

    summary = {
        "run_id": run_id,
        "targets": len(targets),
        "quotes": len(quotes),
        "metrics": len(metrics),
        "missing": len(missing),
        "missing_sample": missing[:8],
        "room_check": room_check,
        "foreign_check": foreign_check,
    }
    if dry_run:
        return summary

    con = wh.connect()
    try:
        summary["quotes_new"] = wh.append(con, "eq_quote", quotes)
        summary["metrics_new"] = wh.append(con, "metric_ts", metrics)
        wh.log_run(
            con, run_id, "vps_board", started,
            # Missing symbols are the normal case -- delisted and untraded
            # UPCOM names are simply absent from the feed -- so the run is only
            # partial when a whole batch failed, which shows up as no quotes.
            "ok" if quotes else "partial",
            rows_in=len(quotes) + len(metrics),
            rows_new=summary.get("quotes_new", 0) + summary.get("metrics_new", 0),
            detail=summary,
        )
    finally:
        con.close()
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="VN board depth and foreign flow from VPS")
    parser.add_argument("--symbols", help="Comma-separated; defaults to stocks.json")
    parser.add_argument("--delay", type=float, default=DELAY)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    import json
    print(json.dumps(collect(
        symbols=[s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None,
        dry_run=args.dry_run,
        delay=args.delay,
    ), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
