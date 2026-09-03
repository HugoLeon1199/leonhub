"""US spot Bitcoin and Ethereum ETF daily flows, from Farside.

Why this matters more than its size suggests: ETF creations and redemptions are
the cleanest free read on whether regulated US capital is entering or leaving
crypto. Retail sentiment gauges are noisy and derivatives positioning is
reflexive; a creation basket is money that actually moved.

The source publishes an HTML table, one row per day and one column per issuer,
with negatives in accounting parentheses and em-dashes for "no data". All three
conventions silently produce wrong numbers if parsed naively.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from pipeline.core.http import HttpClient
from pipeline.core import warehouse as wh

log = logging.getLogger(__name__)

SOURCES = {
    "btc": "https://farside.co.uk/bitcoin-etf-flow-all-data/",
    "eth": "https://farside.co.uk/ethereum-etf-flow-all-data/",
}

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")


def _text(html: str) -> str:
    return TAG_RE.sub("", html).replace("&nbsp;", " ").strip()


def _to_float(cell: str) -> float | None:
    """Parse one flow cell into millions of USD.

    Farside writes negatives as `(95.1)` rather than `-95.1`, and uses `-` or an
    empty cell for days an issuer did not report. Reading `(95.1)` as positive
    would flip the sign on every outflow in the series.
    """
    cell = cell.replace(",", "").strip()
    if not cell or cell in {"-", "–", "—"}:
        return None
    negative = cell.startswith("(") and cell.endswith(")")
    if negative:
        cell = cell[1:-1]
    try:
        value = float(cell)
    except ValueError:
        return None
    return -value if negative else value


def _looks_like_tickers(cells: list[str]) -> bool:
    """True when a row is the ETF ticker header (ETHA, FETH, ETHW…)."""
    codes = [c for c in cells if c]
    if len(codes) < 3:
        return False
    return all(2 <= len(c) <= 5 and c.isupper() and c.isalpha() for c in codes)


def _to_date(cell: str) -> datetime | None:
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(cell.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse(html: str, asset: str, fetched_at: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    issuers: list[str] = []

    for raw_row in ROW_RE.findall(html):
        cells = [_text(c) for c in CELL_RE.findall(raw_row)]
        if not cells:
            continue

        # Header shape differs per asset. The BTC table has a single header row
        # beginning "Date"; the ETH table spreads its header over two rows —
        # issuer names, then tickers — with an empty first cell, followed by
        # "Fee" and "Seed" rows. Keying on the ticker row (all-caps short codes)
        # handles both without hardcoding either layout.
        first = cells[0].lower()
        if first == "date":
            issuers = cells[1:]
            continue
        if not first and _looks_like_tickers(cells[1:]):
            issuers = cells[1:]
            continue
        if not issuers or first in {"fee", "seed", "total", "average", "minimum", "maximum"}:
            continue

        as_of = _to_date(cells[0])
        if as_of is None:
            continue  # any remaining summary or spacer row

        total = 0.0
        seen_any = False
        for issuer, cell in zip(issuers, cells[1:]):
            value = _to_float(cell)
            if value is None:
                continue
            seen_any = True
            if issuer.lower() in {"total"}:
                continue
            total += value
            rows.append({
                "series": f"etf.{asset}.flow.{issuer.lower()}",
                "as_of": as_of,
                "value": value,
                "source": "farside",
                "fetched_at": fetched_at,
                "meta": json.dumps({"unit": "musd", "issuer": issuer}),
            })

        if seen_any:
            rows.append({
                "series": f"etf.{asset}.net_flow",
                "as_of": as_of,
                "value": round(total, 2),
                "source": "farside",
                "fetched_at": fetched_at,
                "meta": json.dumps({"unit": "musd", "issuers": len(issuers)}),
            })

    return rows


def collect(assets: list[str] | None = None, dry_run: bool = False) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    started = wh.utcnow()
    client = HttpClient(delay=2.0)
    stats: dict[str, Any] = {}
    all_rows: list[dict[str, Any]] = []

    for asset in assets or list(SOURCES):
        url = SOURCES.get(asset)
        if not url:
            log.warning("unknown asset %s", asset)
            continue
        html = client.get_text(url)
        rows = parse(html, asset, started)
        all_rows.extend(rows)

        net = [r for r in rows if r["series"].endswith("net_flow")]
        stats[asset] = {
            "rows": len(rows),
            "days": len(net),
            "latest": net[-1]["as_of"].date().isoformat() if net else None,
            "latest_net_musd": net[-1]["value"] if net else None,
        }

    stats["rows_in"] = len(all_rows)
    if dry_run:
        return stats

    con = wh.connect()
    try:
        stats["rows_new"] = wh.append(con, "metric_ts", all_rows)
        wh.log_run(
            con, run_id, "etf_flows", started, "ok",
            rows_in=len(all_rows), rows_new=stats["rows_new"], detail=stats,
        )
    finally:
        con.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect spot ETF flows")
    parser.add_argument("--assets", default="btc,eth", help="btc, eth, or both")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    assets = [a.strip().lower() for a in args.assets.split(",") if a.strip()]
    print(json.dumps(collect(assets, args.dry_run), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
