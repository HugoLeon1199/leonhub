"""Collect company dossiers, governance, events and statements from Vietcap.

This calls the same public REST services used by vnstock, but bypasses its
client-side quota. Collection is sequential, append-only and resumable. A full
market pass is intentionally separate from the daily quote job because it is
thousands of requests and the underlying facts change much more slowly.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import math
import re
import sys
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from pipeline.core import warehouse as wh
from pipeline.core.http import HttpClient

log = logging.getLogger(__name__)

BASE = "https://iq.vietcap.com.vn/api/iq-insight-service"
HEADERS = {"Accept": "application/json", "Referer": "https://iq.vietcap.com.vn/"}
EVENT_CODES = "DIV,ISS,DDIND,DDINS,DDRP,AGME,AGMR,EGME,AIS,MA,MOVE,NLIS,OTHE,RETU,SUSP"
SECTIONS = {
    "INCOME_STATEMENT": "income",
    "BALANCE_SHEET": "balance",
    "CASH_FLOW": "cashflow",
}


def _data(client: HttpClient, path: str, params: dict[str, Any] | None = None) -> Any:
    payload = client.get_json(f"{BASE}{path}", params=params, headers=HEADERS)
    return payload.get("data") if isinstance(payload, dict) else None


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000).date()
        except (ValueError, OSError):
            return None
    text = str(value).strip()
    for fmt in (None, "%d-%b-%y", "%Y%m%d"):
        try:
            return (datetime.fromisoformat(text.replace("Z", "+00:00")) if fmt is None
                    else datetime.strptime(text, fmt)).date()
        except ValueError:
            pass
    return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    clean = html.unescape(re.sub(r"<[^>]*>", " ", value))
    return " ".join(clean.split()) or None


def profile_rows(symbol: str, raw: Any, fetched_at: datetime) -> list[dict[str, Any]]:
    if not isinstance(raw, dict) or not raw:
        return []
    return [{
        "symbol": symbol,
        "organ_name": raw.get("viOrganName"),
        "short_name": raw.get("viOrganShortName"),
        "profile": _text(raw.get("profile")),
        "sector": raw.get("sectorVn") or raw.get("sector"),
        "company_type": raw.get("comTypeCode"),
        "listing_date": _date(raw.get("listingDate")),
        "state_percent": _number(raw.get("statePercentage")),
        "foreign_percent": _number(raw.get("foreignerPercentage")),
        "rating": raw.get("rating"),
        "target_price": _number(raw.get("targetPrice")),
        "rating_as_of": _date(raw.get("ratingAsOf")),
        "source": "Vietcap",
        "fetched_at": fetched_at,
        "meta": json.dumps({
            key: raw.get(key) for key in (
                "analyst", "averageMatchValue1Month", "averageMatchVolume1Month",
                "highestPrice1Year", "lowestPrice1Year", "maximumForeignPercentage",
                "freeFloat", "freeFloatPercentage", "icbCodeLv2", "icbCodeLv4",
            ) if raw.get(key) is not None
        }, ensure_ascii=False),
    }]


def owner_rows(symbol: str, raw: Any, fetched_at: datetime) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    rows = []
    for item in raw:
        name = _text(item.get("ownerName")) if isinstance(item, dict) else None
        if not name:
            continue
        quantity = _number(item.get("quantity"))
        rows.append({
            "symbol": symbol, "owner_name": name,
            "position_name": _text(item.get("positionName")) or "",
            "owner_type": item.get("ownerType"),
            "quantity": int(quantity) if quantity is not None else None,
            "percentage": _number(item.get("percentage")),
            "update_date": _date(item.get("updateDate")),
            "fetched_at": fetched_at,
        })
    return rows


def relationship_rows(symbol: str, raw: Any, fetched_at: datetime) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    rows = []
    for source_key, relation_type in (("subsidiaries", "subsidiary"), ("affiliates", "affiliate")):
        for item in raw.get(source_key) or []:
            name = _text(item.get("rightOrganNameVi") or item.get("rightOrganNameEn"))
            if not name:
                continue
            rows.append({
                "symbol": symbol, "related_code": item.get("rightTicker") or item.get("rightOrganCode"),
                "related_name": name, "relation_type": relation_type,
                "ownership_percent": _number(item.get("ownedPercentage")),
                "fetched_at": fetched_at,
            })
    return rows


def event_rows(symbol: str, raw: Any, fetched_at: datetime) -> list[dict[str, Any]]:
    content = raw.get("content") if isinstance(raw, dict) else None
    if not isinstance(content, list):
        return []
    rows = []
    for item in content:
        event_id = str(item.get("id") or "").strip()
        if not event_id:
            continue
        rows.append({
            "symbol": symbol, "event_id": event_id,
            "event_code": item.get("eventCode"),
            "title": _text(item.get("eventTitleVi") or item.get("eventNameVi")),
            "description": _text(item.get("eventDescVi") or item.get("descriptionVi")),
            "public_date": _date(item.get("publicDate") or item.get("displayDate1")),
            "record_date": _date(item.get("recordDate")),
            "exright_date": _date(item.get("exrightDate")),
            "fetched_at": fetched_at,
            "meta": json.dumps({
                key: item.get(key) for key in (
                    "eventNameVi", "exerciseRatio", "cashDividendPercentage",
                    "stockDividendPercentage", "issuePrice", "category",
                ) if item.get(key) is not None
            }, ensure_ascii=False),
        })
    return rows


def statement_rows(
    symbol: str, section: str, raw: Any, metrics: Any, fetched_at: datetime,
) -> list[dict[str, Any]]:
    if not isinstance(raw, dict) or not isinstance(metrics, dict):
        return []
    labels = {
        item.get("field"): item for item in (metrics.get(section) or [])
        if isinstance(item, dict) and item.get("field")
    }
    rows = []
    for bucket, period_type in (("years", "year"), ("quarters", "quarter")):
        for record in raw.get(bucket) or []:
            try:
                year = int(record.get("yearReport"))
                length = int(record.get("lengthReport") or 0)
            except (TypeError, ValueError):
                continue
            period = str(year) if period_type == "year" else f"{year}-Q{length}"
            if period_type == "quarter" and length not in (1, 2, 3, 4):
                continue
            for field, meta in labels.items():
                value = _number(record.get(field))
                # VCI fills every non-applicable industry field with zero. A
                # literal zero is kept only when the field is part of the
                # company's metadata list; that is the source's declaration
                # that this line belongs to the form.
                if value is None:
                    continue
                rows.append({
                    "symbol": symbol, "period": period,
                    "period_type": period_type, "statement": SECTIONS[section],
                    "field": field, "label_vi": meta.get("titleVi") or meta.get("fullTitleVi"),
                    "label_en": meta.get("titleEn") or meta.get("fullTitleEn"),
                    "level": meta.get("level"), "value": value,
                    "public_date": _date(record.get("publicDate")),
                    "fetched_at": fetched_at,
                })
    return rows


def fetch_symbol(client: HttpClient, symbol: str, scopes: set[str], fetched_at: datetime) -> dict[str, list[dict[str, Any]]]:
    result = {"eq_company": [], "eq_owner": [], "eq_relationship": [], "eq_event": [], "eq_statement": []}
    if "profile" in scopes:
        result["eq_company"] = profile_rows(symbol, _data(client, "/v1/company/details", {"ticker": symbol}), fetched_at)
    if "governance" in scopes:
        result["eq_owner"] = owner_rows(symbol, _data(client, f"/v1/company/{symbol}/shareholder"), fetched_at)
        result["eq_relationship"] = relationship_rows(symbol, _data(client, f"/v1/company/{symbol}/relationship"), fetched_at)
        today = fetched_at.date()
        events = _data(client, "/v1/events", {
            "ticker": symbol, "fromDate": (today - timedelta(days=3650)).strftime("%Y%m%d"),
            "toDate": today.strftime("%Y%m%d"), "eventCode": EVENT_CODES,
            "page": 0, "size": 100,
        })
        result["eq_event"] = event_rows(symbol, events, fetched_at)
    if "statements" in scopes:
        metrics = _data(client, f"/v1/company/{symbol}/financial-statement/metrics")
        for section in SECTIONS:
            raw = _data(client, f"/v1/company/{symbol}/financial-statement", {"section": section})
            result["eq_statement"].extend(statement_rows(symbol, section, raw, metrics, fetched_at))
    return result


def collect(
    symbols: list[str] | None = None, limit: int | None = None,
    dry_run: bool = False, delay: float = 0.2,
    scopes: set[str] | None = None, skip_existing: bool = False,
) -> dict[str, Any]:
    scopes = scopes or {"profile", "governance", "statements"}
    unknown = scopes - {"profile", "governance", "statements"}
    if unknown:
        raise ValueError(f"Unknown scopes: {', '.join(sorted(unknown))}")
    started = wh.utcnow(); fetched_at = started; run_id = uuid.uuid4().hex[:12]
    con = None if dry_run else wh.connect()
    probe = con
    if not symbols:
        probe = probe or wh.connect_reader()
        symbols = [row[0] for row in probe.execute(
            "SELECT DISTINCT symbol FROM eq_listing ORDER BY symbol"
        ).fetchall()]
        if probe is not con:
            probe.close()
    targets = symbols[:limit] if limit else symbols
    if skip_existing and con is not None:
        completed = wh.latest_completed_quarter(fetched_at)
        have_profile = {r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM eq_company WHERE fetched_at >= now() - INTERVAL 30 DAY"
        ).fetchall()} if "profile" in scopes else set(targets)
        have_statements = {r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM eq_statement WHERE period = ?", [completed]
        ).fetchall()} if "statements" in scopes else set(targets)
        # Governance remains refreshable; skip only when all requested durable
        # datasets are current and governance was not explicitly requested.
        if "governance" not in scopes:
            targets = [s for s in targets if s not in (have_profile & have_statements)]

    client = HttpClient(delay=delay, retries=2)
    stats: dict[str, Any] = {"targets": len(targets), "ok": 0, "failed": 0, "rows": {}, "written": {}}
    try:
        for index, symbol in enumerate(targets, 1):
            try:
                batches = fetch_symbol(client, symbol, scopes, fetched_at)
            except Exception as exc:
                stats["failed"] += 1
                log.warning("%s failed: %s", symbol, exc)
                continue
            if any(batches.values()):
                stats["ok"] += 1
            for table, rows in batches.items():
                stats["rows"][table] = stats["rows"].get(table, 0) + len(rows)
                if con is not None and rows:
                    stats["written"][table] = stats["written"].get(table, 0) + wh.append(con, table, rows)
            if index % 25 == 0:
                log.info("%d/%d dossiers (%d ok, %d failed)", index, len(targets), stats["ok"], stats["failed"])
        stats["elapsed_sec"] = round((wh.utcnow() - started).total_seconds(), 1)
        if con is not None:
            wh.log_run(con, run_id, "vci_company", started, "partial" if stats["failed"] else "ok",
                       rows_in=sum(stats["rows"].values()), rows_new=sum(stats["written"].values()), detail=stats)
        return stats
    finally:
        if con is not None:
            con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Vietcap company dossiers and statements")
    parser.add_argument("--symbols", help="Comma-separated tickers (default: all listings)")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--scope", default="profile,governance,statements",
                        help="Comma-separated: profile,governance,statements")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()] if args.symbols else None
    print(json.dumps(collect(symbols, args.limit, args.dry_run, args.delay,
                             {x.strip() for x in args.scope.split(",") if x.strip()}, args.skip_existing),
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
