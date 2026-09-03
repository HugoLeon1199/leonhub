"""Build flows.json — cross-market capital flows.

This is the layer the reference product does not have. Price tells you what
happened; flows tell you who was on the other side, which is the part that
carries forward.

Series are emitted as compact parallel arrays (dates, values) rather than a
list of objects, because a two-year daily series repeated per issuer is the
largest thing this file carries.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from pipeline.core import warehouse as wh
from pipeline.publish.emit import write_json

log = logging.getLogger(__name__)

# Trailing window published per series. Two years covers every regime the ETFs
# have traded through without shipping the full history to every visitor.
DAYS = 730

SERIES_SQL = """
WITH observed AS (
    SELECT DISTINCT ON (series, as_of)
        series, as_of, value
    FROM metric_ts
    WHERE series LIKE ?
    ORDER BY series, as_of, fetched_at DESC
)
SELECT series, as_of, value
FROM observed
WHERE as_of >= (SELECT max(as_of) FROM observed) - INTERVAL (?) DAY
ORDER BY series, as_of
"""


def _series(con, pattern: str) -> dict[str, dict[str, list[Any]]]:
    out: dict[str, dict[str, list[Any]]] = {}
    for name, as_of, value in con.execute(SERIES_SQL, [pattern, DAYS]).fetchall():
        bucket = out.setdefault(name, {"d": [], "v": []})
        bucket["d"].append(as_of.date().isoformat())
        bucket["v"].append(round(value, 2))
    return out


def _cumulative(values: list[float]) -> list[float]:
    total = 0.0
    out = []
    for v in values:
        total += v
        out.append(round(total, 1))
    return out


def build(dry_run: bool = False) -> dict[str, Any]:
    con = wh.connect_reader()
    try:
        etf = _series(con, "etf.%.net_flow")
    finally:
        con.close()

    payload: dict[str, Any] = {"etf": {}}
    stats: dict[str, Any] = {}

    for name, series in etf.items():
        asset = name.split(".")[1]          # etf.btc.net_flow -> btc
        values = series["v"]
        payload["etf"][asset] = {
            "d": series["d"],
            "v": values,
            "cum": _cumulative(values),
        }
        # Rolling sums are what a reader actually reasons with: one day's flow
        # is noise, a month of it is a position.
        stats[asset] = {
            "days": len(values),
            "latest": series["d"][-1] if series["d"] else None,
            "latest_musd": values[-1] if values else None,
            "sum_5d": round(sum(values[-5:]), 1),
            "sum_20d": round(sum(values[-20:]), 1),
        }

    payload["stats"] = stats

    if not dry_run:
        path = write_json("flows.json", payload)
        stats["path"] = str(path)
        stats["size_kb"] = round(path.stat().st_size / 1024, 1)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build flows.json from the warehouse")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(build(args.dry_run), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
