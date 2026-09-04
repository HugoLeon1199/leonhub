"""Publish compact per-ticker history and dossiers for the detail route.

The screener stays small; a reader pays for history only after opening a ticker.
This build publishes warehouse ratio history plus the decision-useful subset of
company, governance, event and statement data. Ratios are split into quarterly
and annual (Vietcap's ``Q5``) observations; the warehouse retains the full raw
statement field set for later guarded valuation work.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from collections import defaultdict
from typing import Any

from pipeline.core import warehouse as wh
from pipeline.publish.emit import read_json, write_json

log = logging.getLogger(__name__)

# Public key -> warehouse metric. These cover the common and industry-specific
# ratios that have broad, current coverage. Statement lines have a separate
# contract so the browser never mistakes a ratio for a currency amount.
METRICS = {
    "pe": "pe", "pb": "pb", "ps": "ps", "ev": "ev_ebitda",
    "pcf": "p_cf", "dy": "dividend_yield",
    "roe": "roe", "roa": "roa", "roic": "roic",
    "gpm": "gross_margin", "ebitm": "ebit_margin", "npm": "net_margin",
    "de": "debt_equity", "cr": "current_ratio", "qr": "quick_ratio",
    "lev": "leverage", "at": "asset_turnover", "cash": "cash_ratio",
    "cycle": "cash_cycle", "mc": "market_cap", "ebitda": "ebitda",
    "nim": "nim", "npl": "npl", "car": "car", "casa": "casa",
    "ldr": "ldr", "cir": "cir", "dg": "deposit_growth", "lg": "loans_growth",
}
SOURCE_TO_KEY = {source: key for key, source in METRICS.items()}
PERCENT_KEYS = {"dy", "roe", "roa", "roic", "gpm", "ebitm", "npm",
                "nim", "npl", "car", "casa", "ldr", "cir", "dg", "lg"}
AMOUNT_BILLION_KEYS = {"mc", "ebitda"}
ZERO_FILLED_KEYS = {"roic", "gpm", "nim", "npl", "car", "casa", "ldr", "cir", "dg", "lg"}
MAX_QUARTERS = 36
MAX_YEARS = 9

# A concise, comparable statement set for the static payload. The warehouse
# retains every VCI line; the browser only receives decision-useful headline
# fields so a market-wide build does not add hundreds of megabytes to git.
CORE_STATEMENT_FIELDS = {
    "isa1", "isa3", "isa5", "isa11", "isa20", "isb27", "isb40", "isb41",
    "iss42", "bsa2", "bsa6", "bsa15", "bsa53", "bsa54", "bsa78",
    "bsb103", "bsb113", "cfa18", "cfa26", "cfa34", "cfa35", "cfa38",
}

HISTORY_SQL = """
WITH observed AS (
    SELECT DISTINCT ON (symbol, period, metric)
        symbol, period, metric, value, fetched_at
    FROM eq_fundamental
    WHERE metric IN (SELECT * FROM unnest(?))
      AND value IS NOT NULL
    ORDER BY symbol, period, metric, fetched_at DESC
)
SELECT symbol, period, metric, value, fetched_at
FROM observed
ORDER BY symbol, period, metric
"""

LISTING_SQL = """
SELECT DISTINCT ON (symbol) symbol, organ_name, exchange, industry
FROM eq_listing
WHERE symbol IS NOT NULL
ORDER BY symbol, fetched_at DESC
"""

COMPANY_SQL = """
SELECT DISTINCT ON (symbol)
    symbol, short_name, profile, sector, company_type, listing_date,
    state_percent, foreign_percent, rating, target_price, rating_as_of, source
FROM eq_company
ORDER BY symbol, fetched_at DESC
"""

OWNER_SQL = """
SELECT DISTINCT ON (symbol, owner_name, position_name)
    symbol, owner_name, position_name, owner_type, quantity, percentage, update_date
FROM eq_owner
ORDER BY symbol, owner_name, position_name, fetched_at DESC
"""

RELATIONSHIP_SQL = """
SELECT DISTINCT ON (symbol, related_name, relation_type)
    symbol, related_code, related_name, relation_type, ownership_percent
FROM eq_relationship
ORDER BY symbol, related_name, relation_type, fetched_at DESC
"""

EVENT_SQL = """
SELECT DISTINCT ON (symbol, event_id)
    symbol, event_code, title, public_date, record_date, exright_date
FROM eq_event
ORDER BY symbol, event_id, fetched_at DESC
"""

STATEMENT_SQL = """
SELECT DISTINCT ON (symbol, period, statement, field)
    symbol, period, period_type, statement, field, label_vi, value, public_date
FROM eq_statement
WHERE field IN (SELECT * FROM unnest(?))
ORDER BY symbol, period, statement, field, fetched_at DESC
"""


def _has_table(con: Any, name: str) -> bool:
    return bool(con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()[0])


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def _value(key: str, raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    if key in PERCENT_KEYS:
        value *= 100
        # Vietcap derives bank CIR from negative operating-cost statement
        # lines, so the raw ratio carries the accounting sign. Readers expect
        # the conventional positive cost/income percentage.
        if key == "cir":
            value = abs(value)
        if not -500 <= value <= 500:
            return None
    elif key in AMOUNT_BILLION_KEYS:
        value /= 1e9
    elif not -100_000 <= value <= 100_000:
        return None
    return round(value, 3)


def build(dry_run: bool = False, symbols: set[str] | None = None) -> dict[str, Any]:
    con = wh.connect_reader()
    try:
        listing_rows = con.execute(LISTING_SQL).fetchall()
        listings = {
            row[0]: {"n": row[1], "e": row[2], "i": row[3]}
            for row in listing_rows if not symbols or row[0] in symbols
        }
        rows = con.execute(HISTORY_SQL, [list(SOURCE_TO_KEY)]).fetchall()
        companies = con.execute(COMPANY_SQL).fetchall() if _has_table(con, "eq_company") else []
        owners = con.execute(OWNER_SQL).fetchall() if _has_table(con, "eq_owner") else []
        relationships = con.execute(RELATIONSHIP_SQL).fetchall() if _has_table(con, "eq_relationship") else []
        events = con.execute(EVENT_SQL).fetchall() if _has_table(con, "eq_event") else []
        statements = con.execute(
            STATEMENT_SQL, [list(CORE_STATEMENT_FIELDS)]
        ).fetchall() if _has_table(con, "eq_statement") else []
    finally:
        con.close()

    series: dict[str, dict[str, dict[str, list[list[Any]]]]] = defaultdict(
        lambda: defaultdict(lambda: {"q": [], "y": []})
    )
    fetched: dict[str, str] = {}
    for symbol, period, metric, raw, fetched_at in rows:
        if symbol not in listings:
            continue
        key = SOURCE_TO_KEY.get(metric)
        value = _value(key, raw) if key else None
        if value is None:
            continue
        bucket = "y" if str(period).endswith("-Q5") else "q"
        label = str(period)[:4] if bucket == "y" else str(period)
        series[symbol][key][bucket].append([label, value])
        stamp = fetched_at.isoformat() if hasattr(fetched_at, "isoformat") else str(fetched_at)
        if stamp > fetched.get(symbol, ""):
            fetched[symbol] = stamp

    company_map = {
        row[0]: {
            "short": row[1], "profile": row[2], "sector": row[3], "type": row[4],
            "listed": _iso(row[5]), "state": row[6], "foreign": row[7],
            "rating": row[8], "target": row[9], "rating_as_of": _iso(row[10]),
            "source": row[11],
        }
        for row in companies if (not symbols or row[0] in symbols)
    }
    owner_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sym, name, position, owner_type, quantity, percentage, update_date in owners:
        if symbols and sym not in symbols:
            continue
        owner_map[sym].append({
            "n": name, "p": position or None, "t": owner_type,
            "q": quantity, "pct": round(percentage * 100, 3) if percentage is not None else None,
            "d": _iso(update_date),
        })
    for values in owner_map.values():
        values.sort(key=lambda item: item.get("pct") or -1, reverse=True)

    relationship_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sym, code, name, relation_type, percentage in relationships:
        if symbols and sym not in symbols:
            continue
        relationship_map[sym].append({
            "c": code, "n": name, "t": relation_type,
            "pct": round(percentage * 100, 3) if percentage is not None else None,
        })
    for values in relationship_map.values():
        values.sort(key=lambda item: item.get("pct") or -1, reverse=True)

    event_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sym, code, title, public_date, record_date, exright_date in events:
        if symbols and sym not in symbols:
            continue
        event_map[sym].append({
            "c": code, "t": title, "d": _iso(public_date),
            "record": _iso(record_date), "ex": _iso(exright_date),
        })
    for values in event_map.values():
        values.sort(key=lambda item: item.get("d") or "", reverse=True)

    statement_map: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for sym, period, period_type, statement, field, label, raw, public_date in statements:
        if symbols and sym not in symbols:
            continue
        try:
            value = float(raw) / 1e9
        except (TypeError, ValueError):
            continue
        item = statement_map[sym].setdefault(field, {
            "label": label, "section": statement, "q": [], "y": [],
        })
        bucket = "y" if period_type == "year" else "q"
        item[bucket].append([str(period), round(value, 3)])
        if public_date:
            item["published"] = _iso(public_date)
    for metrics in statement_map.values():
        for item in metrics.values():
            item["q"] = item["q"][-MAX_QUARTERS:]
            item["y"] = item["y"][-MAX_YEARS:]
            if not item["q"]:
                item.pop("q")
            if not item["y"]:
                item.pop("y")

    payloads: dict[str, dict[str, Any]] = {}
    for symbol, meta in listings.items():
        metrics: dict[str, dict[str, list[list[Any]]]] = {}
        for key, groups in series.get(symbol, {}).items():
            q = groups["q"][-MAX_QUARTERS:]
            y = groups["y"][-MAX_YEARS:]
            # Some industry forms publish an entire non-applicable cadence as
            # literal zero (for example annual D/E for banks) while quarterly
            # observations are populated. Do not draw that as a real zero
            # series; preserve isolated zero points inside a populated series.
            if q and not any(point[1] for point in q):
                q = []
            if y and not any(point[1] for point in y):
                y = []
            values = [point[1] for point in q + y]
            if not values or (key in ZERO_FILLED_KEYS and not any(values)):
                continue
            item: dict[str, list[list[Any]]] = {}
            if q:
                item["q"] = q
            if y:
                item["y"] = y
            metrics[key] = item
        if not metrics:
            continue
        payload: dict[str, Any] = {
            "s": symbol, **meta, "src": "Vietcap",
            "as_of": fetched.get(symbol), "m": metrics,
        }
        if symbol in company_map:
            payload["co"] = company_map[symbol]
        if symbol in owner_map:
            payload["owners"] = owner_map[symbol][:40]
        if symbol in relationship_map:
            payload["rel"] = relationship_map[symbol][:80]
            payload["rel_count"] = len(relationship_map[symbol])
        if symbol in event_map:
            payload["events"] = event_map[symbol][:30]
        if symbol in statement_map:
            payload["st"] = statement_map[symbol]
        payloads[symbol] = payload

    if not dry_run:
        # Per-symbol files keep the initial screener payload small and are easy
        # for static hosting/CDN caching. No price candles are committed here.
        emit_log = logging.getLogger("pipeline.publish.emit")
        old_level = emit_log.level
        emit_log.setLevel(logging.WARNING)
        try:
            for symbol, payload in payloads.items():
                # A daily build should not create a 1,700-file Git diff merely
                # because write_json stamps the wall clock. Preserve the old
                # stamp when the actual contract is byte-for-byte equivalent;
                # changed ratios/dossier facts still receive a fresh stamp.
                previous = read_json(f"ticker/{symbol}.json")
                if isinstance(previous, dict):
                    old_body = {k: v for k, v in previous.items() if k != "updated_at"}
                    if old_body == payload and previous.get("updated_at"):
                        payload["updated_at"] = previous["updated_at"]
                write_json(f"ticker/{symbol}.json", payload)
        finally:
            emit_log.setLevel(old_level)
        write_json("ticker/manifest.json", {
            "count": len(payloads),
            "symbols": sorted(payloads),
            "source": "Vietcap ratios, company dossiers and statements",
            "dossiers": sum(1 for p in payloads.values() if "co" in p),
            "statements": sum(1 for p in payloads.values() if "st" in p),
        })

    metric_points = sum(
        len(points)
        for payload in payloads.values()
        for groups in payload["m"].values()
        for points in groups.values()
    )
    sample = payloads.get("VIC") or next(iter(payloads.values()), None)
    return {
        "tickers": len(payloads), "metric_points": metric_points,
        "sample": None if not sample else {
            "s": sample["s"], "metrics": sorted(sample["m"]),
            "points": sum(
                len(points) for groups in sample["m"].values()
                for points in groups.values()
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build data/ticker/*.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--symbols", help="Comma-separated symbols for a local probe")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    symbols = {s.strip().upper() for s in (args.symbols or "").split(",") if s.strip()} or None
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(json.dumps(build(args.dry_run, symbols), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
