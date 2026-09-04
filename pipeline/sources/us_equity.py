"""Delayed US market snapshot for the static analysis app.

Yahoo's chart endpoint supplies delayed daily OHLCV. It sends no CORS header,
so the browser cannot call it directly and must read our artifact instead.
This is a curated liquid universe, not a claim of whole-market coverage.

Daily bars land in `eq_quote` like every other quote source, so the US series
accumulates the same point-in-time history as VN equities and stays queryable
from the warehouse. The published JSON is a view over that table, not a
parallel store: it carries the trailing window the chart needs, nothing more.
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

# Trailing sessions kept in the published artifact. A full year of daily closes
# per symbol is what makes the file large; the warehouse holds the complete
# history, so the artifact needs only enough for the chart the app draws.
PUBLISH_SESSIONS = 260

UNIVERSE = {
    "SPY": "S&P 500 ETF", "QQQ": "Nasdaq 100 ETF", "DIA": "Dow Jones ETF", "IWM": "Russell 2000 ETF",
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "AMZN": "Amazon", "GOOGL": "Alphabet",
    "META": "Meta Platforms", "TSLA": "Tesla", "AVGO": "Broadcom", "BRK-B": "Berkshire Hathaway",
    "JPM": "JPMorgan Chase", "V": "Visa", "MA": "Mastercard", "LLY": "Eli Lilly", "WMT": "Walmart",
    "XOM": "Exxon Mobil", "COST": "Costco", "NFLX": "Netflix", "ORCL": "Oracle", "AMD": "AMD",
    "CRM": "Salesforce", "KO": "Coca-Cola", "PEP": "PepsiCo", "BAC": "Bank of America",
    "JNJ": "Johnson & Johnson", "HD": "Home Depot", "PG": "Procter & Gamble", "ABBV": "AbbVie",
    "CSCO": "Cisco", "IBM": "IBM", "INTC": "Intel", "DIS": "Disney", "UBER": "Uber",
    "PLTR": "Palantir", "COIN": "Coinbase", "GLD": "Gold ETF", "TLT": "20Y Treasury ETF",
}


def _sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def _rsi(values: list[float], n: int = 14) -> float | None:
    if len(values) <= n:
        return None
    changes = [values[i] - values[i - 1] for i in range(len(values) - n, len(values))]
    up = sum(max(change, 0) for change in changes) / n
    down = sum(max(-change, 0) for change in changes) / n
    return 100.0 if down == 0 else 100 - 100 / (1 + up / down)


def _momentum(values: list[float], sessions: int) -> float | None:
    if len(values) <= sessions or not values[-sessions - 1]:
        return None
    return (values[-1] / values[-sessions - 1] - 1) * 100


def fetch(client: HttpClient, symbol: str, fallback_name: str) -> dict[str, Any] | None:
    """Return the parsed chart block: metadata plus one entry per session."""
    block = client.get_json(
        URL.format(symbol=symbol),
        params={"range": "1y", "interval": "1d", "events": "div,splits"},
        headers={"User-Agent": "Mozilla/5.0 (compatible; LEON-Hub/0.1)"},
    ).get("chart", {}).get("result")
    if not block:
        return None
    result = block[0]
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]

    def series(key: str) -> list[Any]:
        return quote.get(key) or []

    closes, opens = series("close"), series("open")
    highs, lows, volumes = series("high"), series("low"), series("volume")

    bars: list[dict[str, Any]] = []
    for i, stamp in enumerate(timestamps):
        close = closes[i] if i < len(closes) else None
        if close is None:
            continue

        def value(column: list[Any]) -> float | None:
            raw = column[i] if i < len(column) else None
            return float(raw) if raw is not None else None

        volume = value(volumes)
        bars.append({
            "as_of": datetime.fromtimestamp(stamp, timezone.utc).date(),
            "close": float(close), "open": value(opens),
            "high": value(highs), "low": value(lows),
            "volume": int(volume) if volume is not None else None,
        })
    if len(bars) < 20:
        return None
    return {"meta": meta, "bars": bars, "name": meta.get("longName") or fallback_name}


def _quote_rows(symbol: str, parsed: dict[str, Any], fetched_at: datetime) -> list[dict[str, Any]]:
    """Map parsed bars onto eq_quote. One row per session, append-only."""
    meta = parsed["meta"]
    exchange = meta.get("fullExchangeName") or meta.get("exchangeName")
    return [{
        "symbol": symbol, "as_of": bar["as_of"], "fetched_at": fetched_at,
        "exchange": exchange, "price": bar["close"], "open_price": bar["open"],
        "high": bar["high"], "low": bar["low"], "volume": bar["volume"],
    } for bar in parsed["bars"]]


def _published_row(symbol: str, parsed: dict[str, Any]) -> dict[str, Any]:
    """Compact view for the browser: trailing window plus derived state."""
    bars = parsed["bars"]
    values = [bar["close"] for bar in bars]
    meta = parsed["meta"]
    price = float(meta.get("regularMarketPrice") or values[-1])
    previous = values[-2] if len(values) > 1 else None
    sma50, sma200 = _sma(values, 50), _sma(values, 200)
    rsi = _rsi(values)
    m1, m3, m6 = _momentum(values, 21), _momentum(values, 63), _momentum(values, 126)
    stamp = meta.get("regularMarketTime")
    as_of = (
        datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp
        else datetime.combine(bars[-1]["as_of"], datetime.min.time(), timezone.utc).isoformat()
    )
    return {
        "s": symbol, "n": parsed["name"],
        "e": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "p": round(price, 3),
        "ch": round(price - previous, 3) if previous else None,
        "chp": round((price / previous - 1) * 100, 2) if previous else None,
        "h52": meta.get("fiftyTwoWeekHigh") or max(values),
        "l52": meta.get("fiftyTwoWeekLow") or min(values),
        "v": meta.get("regularMarketVolume"),
        # Null when the window is too short to compute it. A zero here would
        # read as a real reading of 0 rather than an absent one.
        "rsi": round(rsi, 1) if rsi is not None else None,
        "m1": round(m1, 2) if m1 is not None else None,
        "m3": round(m3, 2) if m3 is not None else None,
        "m6": round(m6, 2) if m6 is not None else None,
        "d": "up" if sma50 and price >= sma50 else "down",
        "w": "up" if sma200 and price >= sma200 else "down",
        "hist": [
            [bar["as_of"].isoformat(), round(bar["close"], 3)]
            for bar in bars[-PUBLISH_SESSIONS:]
        ],
        "asof": as_of,
    }


def collect(dry_run: bool = False, limit: int | None = None, delay: float = 0.2) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    started = wh.utcnow()
    client = HttpClient(delay=delay, retries=2, timeout=20)

    rows: list[dict[str, Any]] = []
    quote_rows: list[dict[str, Any]] = []
    failed: list[str] = []
    for symbol, name in list(UNIVERSE.items())[:limit]:
        try:
            parsed = fetch(client, symbol, name)
            if not parsed:
                failed.append(symbol)
                continue
            quote_rows.extend(_quote_rows(symbol, parsed, started))
            rows.append(_published_row(symbol, parsed))
        except Exception as exc:  # a partial snapshot is published, coverage reported
            log.warning("%s failed: %s", symbol, exc)
            failed.append(symbol)
        time.sleep(0.02)

    stats: dict[str, Any] = {
        "rows": len(rows), "target": len(UNIVERSE),
        "quote_rows_in": len(quote_rows), "failed": failed,
    }
    if dry_run:
        return stats

    con = wh.connect()
    try:
        stats["quote_rows_new"] = wh.append(con, "eq_quote", quote_rows)
        wh.log_run(
            con, run_id, "us_equity", started, "ok",
            rows_in=len(quote_rows), rows_new=stats["quote_rows_new"], detail=stats,
        )
    finally:
        con.close()

    stats["path"] = str(write_json("us.json", {
        "rows": rows,
        "universe": len(UNIVERSE),
        "sessions": PUBLISH_SESSIONS,
        "source": {
            "name": "Yahoo Finance chart", "kind": "Delayed OHLCV",
            "url": "https://finance.yahoo.com/",
        },
    }))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect delayed US OHLCV")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(collect(args.dry_run, args.limit, args.delay), indent=2, default=str))


if __name__ == "__main__":
    main()
