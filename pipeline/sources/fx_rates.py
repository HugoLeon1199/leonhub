"""FX and macro reference rates, from the same delayed Yahoo chart endpoint.

Why this sits in the pipeline rather than the browser: the endpoint sends no
CORS header, so a page cannot call it directly -- the same constraint that
already governs `us_equity`. It is a delayed daily feed, not a dealing rate:
label it as such and never present it as a price anyone can trade on.

The set is chosen for what it explains about the VN market rather than for
completeness. USD/VND is the exchange rate every foreign flow is converted
through, and the dollar index is the single number that moves emerging-market
flows most reliably. The majors are context for both.

Rows land in `metric_ts` rather than `eq_quote`: these are reference series,
not equity quotes, and forcing them into a table keyed by ticker would put
non-equities in every screener query that reads it.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from pipeline.core import warehouse as wh
from pipeline.core.http import HttpClient
from pipeline.publish.emit import write_json

log = logging.getLogger(__name__)
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# Trailing sessions kept in the published artifact; the warehouse keeps all of
# it. One year of daily closes is what the page draws.
PUBLISH_SESSIONS = 260

# Yahoo symbol -> (series key, display name, decimal places). The series key
# becomes `fx.<key>` in metric_ts, so it must stay stable once published.
UNIVERSE: dict[str, tuple[str, str, int]] = {
    "USDVND=X": ("usdvnd", "USD/VND", 0),
    "DX-Y.NYB": ("dxy", "Chỉ số Dollar (DXY)", 2),
    "EURUSD=X": ("eurusd", "EUR/USD", 4),
    "GBPUSD=X": ("gbpusd", "GBP/USD", 4),
    "USDJPY=X": ("usdjpy", "USD/JPY", 2),
    "USDCNY=X": ("usdcny", "USD/CNY", 4),
    "AUDUSD=X": ("audusd", "AUD/USD", 4),
    "GC=F": ("gold", "Vàng (COMEX)", 1),
}


def _pct(values: list[float], sessions: int) -> float | None:
    if len(values) <= sessions or not values[-sessions - 1]:
        return None
    return (values[-1] / values[-sessions - 1] - 1) * 100


def fetch(client: HttpClient, symbol: str) -> dict[str, Any] | None:
    """Return metadata plus one closing observation per session."""
    block = client.get_json(
        URL.format(symbol=symbol),
        params={"range": "1y", "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0 (compatible; LEON-Hub/0.1)"},
    ).get("chart", {}).get("result")
    if not block:
        return None
    result = block[0]
    meta = result.get("meta") or {}
    stamps = result.get("timestamp") or []
    closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []

    bars = [
        {
            "as_of": datetime.fromtimestamp(stamp, timezone.utc).date(),
            "close": float(close),
        }
        for stamp, close in zip(stamps, closes)
        if close is not None
    ]
    if len(bars) < 20:
        return None
    return {"meta": meta, "bars": bars}


def _metric_rows(key: str, parsed: dict[str, Any], fetched_at: datetime) -> list[dict[str, Any]]:
    return [
        {
            "series": f"fx.{key}",
            "as_of": bar["as_of"],
            "value": bar["close"],
            "source": "yahoo_chart",
            "fetched_at": fetched_at,
        }
        for bar in parsed["bars"]
    ]


def _published_row(symbol: str, key: str, name: str, digits: int, parsed: dict[str, Any]) -> dict[str, Any]:
    bars = parsed["bars"]
    values = [bar["close"] for bar in bars]
    meta = parsed["meta"]
    price = float(meta.get("regularMarketPrice") or values[-1])
    previous = values[-2] if len(values) > 1 else None
    m1, m3, m6 = _pct(values, 21), _pct(values, 63), _pct(values, 126)
    return {
        "s": key, "y": symbol, "n": name,
        "p": round(price, digits),
        "chp": round((price / previous - 1) * 100, 2) if previous else None,
        # Null where the window is too short. A zero would read as a measured
        # flat move rather than as an absent one.
        "m1": round(m1, 2) if m1 is not None else None,
        "m3": round(m3, 2) if m3 is not None else None,
        "m6": round(m6, 2) if m6 is not None else None,
        "h52": round(max(values), digits),
        "l52": round(min(values), digits),
        "hist": [
            [bar["as_of"].isoformat(), round(bar["close"], digits)]
            for bar in bars[-PUBLISH_SESSIONS:]
        ],
        "asof": bars[-1]["as_of"].isoformat(),
    }


def collect(dry_run: bool = False, limit: int | None = None, delay: float = 0.3) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    started = wh.utcnow()
    client = HttpClient(delay=delay, retries=2, timeout=20)

    rows: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    failed: list[str] = []
    for symbol, (key, name, digits) in list(UNIVERSE.items())[:limit]:
        try:
            parsed = fetch(client, symbol)
            if not parsed:
                failed.append(symbol)
                continue
            metrics.extend(_metric_rows(key, parsed, started))
            rows.append(_published_row(symbol, key, name, digits, parsed))
        except Exception as exc:  # a partial snapshot is published, coverage reported
            log.warning("%s failed: %s", symbol, exc)
            failed.append(symbol)
        time.sleep(0.02)

    stats: dict[str, Any] = {
        "rows": len(rows), "target": len(UNIVERSE),
        "metric_rows_in": len(metrics), "failed": failed,
    }
    if dry_run:
        return stats

    con = wh.connect()
    try:
        stats["metric_rows_new"] = wh.append(con, "metric_ts", metrics)
        wh.log_run(
            con, run_id, "fx_rates", started, "ok",
            rows_in=len(metrics), rows_new=stats["metric_rows_new"], detail=stats,
        )
    finally:
        con.close()

    stats["path"] = str(write_json("fx.json", {
        "rows": rows,
        "universe": len(UNIVERSE),
        "sessions": PUBLISH_SESSIONS,
        "source": {
            "name": "Yahoo Finance chart", "kind": "Tỷ giá tham chiếu, có độ trễ",
            "url": "https://finance.yahoo.com/",
        },
    }))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect delayed FX and macro rates")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay", type=float, default=0.3)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(collect(args.dry_run, args.limit, args.delay), indent=2, default=str))


if __name__ == "__main__":
    main()
