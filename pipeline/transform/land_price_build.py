"""Publish state land prices, and the ratio of asking price to state price.

The state table is the only officially published price in Vietnamese real
estate. Everything else this repo holds is what a seller hopes to get. Putting
the two side by side is the point of collecting it.

**How to read the ratio, which is the part that misleads if unexplained.**
Vietnamese state land prices sit far below market by design -- they are a fee
base, not a valuation. A district asking three to five times its state price is
ordinary and says nothing about value. What carries information is the
*dispersion*: which districts sit unusually far from the level typical of their
own province. So `rr` is published beside the province median ratio `rm` and a
relative position, never as a standalone "overvalued" verdict.

Three things are deliberately withheld:

- **No cell below MIN_STREETS.** A ratio resting on four matched streets is an
  anecdote, the same rule the asking-price aggregate applies at n=20.
- **No verbatim republication.** The warehouse holds ~4,000 HCMC street prices;
  what is published is a district summary. Copying the state's table wholesale
  is not what this repo is for.
- **No claim to be the current table.** The source mirror lags the gazette --
  its HCMC page still carries the 2025 decision while the 2026 one is in force,
  and Da Nang's is from 2021. `doc` travels with every row so the page can say
  which table a number came from, and `validate` warns when it disagrees with
  the primary citation in province_profiles.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
from typing import Any

from pipeline.core import warehouse as wh
from pipeline.transform.bds_aggregate import current_province, slugify

log = logging.getLogger(__name__)

# Matched streets a district needs before its ratio is published.
MIN_STREETS = 8
# Ratios outside this band are a unit error or a mis-joined street, not a market
# observation: state prices are below market, so a ratio under 1 means the
# asking price is below the fee base, and above 100 means a decimal moved.
RATIO_MIN, RATIO_MAX = 0.5, 100.0

# Latest observation per street, with the asking-price side joined on the same
# diacritic-free key the collector wrote.
JOIN_SQL = f"""
WITH lp AS (
    SELECT DISTINCT ON (province, street_key, segment)
        province, street_key, segment, district, street,
        price_residential, source_doc, fetched_at
    FROM re_land_price
    WHERE price_residential > 0
    ORDER BY province, street_key, segment, fetched_at DESC
),
-- One state price per street: a street split into segments gets the median of
-- its segments rather than an arbitrary one.
lp_street AS (
    SELECT province, district, street_key,
           median(price_residential)       AS state_vnd_m2,
           arg_max(source_doc, fetched_at) AS doc
    FROM lp GROUP BY 1, 2, 3
),
-- Asking side: newest observation per listing, sale lane only, with a join key
-- built the same way the collector builds it.
ask AS (
    SELECT DISTINCT ON (list_id)
        region, district, street_name, price_per_m2, fetched_at
    FROM re_listing
    WHERE source = 'chotot:s'
      AND street_name IS NOT NULL
      AND price_per_m2 BETWEEN 1.0 AND 3000.0
    ORDER BY list_id, fetched_at DESC
),
ask_keyed AS (
    SELECT region, district, price_per_m2,
           trim(regexp_replace(
             regexp_replace(lower(strip_accents(street_name)),
                            '\\b(duong|pho)\\b', ' ', 'g'),
             '[^a-z0-9 ]', ' ', 'g')) AS street_key
    FROM ask
),
ask_street AS (
    SELECT region, district, street_key,
           median(price_per_m2) * 1e6 AS ask_vnd_m2,
           count(*)                   AS ask_n
    FROM ask_keyed
    WHERE length(street_key) > 4
    GROUP BY 1, 2, 3
)
SELECT a.region, a.district, a.street_key,
       a.ask_vnd_m2, a.ask_n,
       s.state_vnd_m2, s.doc, s.province
FROM ask_street a
-- District must match, not just the street name. 202 of HCMC's 3,098 streets
-- exist in more than one district, and joining on the name alone paired the
-- Gò Vấp "Lê Lợi" (asking 1.6 million/m2) with the District 1 one (687
-- million/m2) -- a 400x error that looks like a market signal.
JOIN lp_street s
  ON  s.street_key = a.street_key
  AND s.district   = a.district
"""


def build(dry_run: bool = False) -> dict[str, Any]:
    con = wh.connect_reader()
    try:
        rows = con.execute(JOIN_SQL).fetchall()
        cols = [d[0] for d in con.description]
        coverage = con.execute("""
            SELECT province, count(DISTINCT street_key), max(source_doc)
            FROM re_land_price GROUP BY 1 ORDER BY 2 DESC
        """).fetchall()
    finally:
        con.close()

    # Bucket the matched streets by the district the asking side reports -- the
    # state table's own district labels are pre-merger and do not always agree.
    cells: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        rec = dict(zip(cols, row))
        ratio = rec["ask_vnd_m2"] / rec["state_vnd_m2"] if rec["state_vnd_m2"] else None
        if ratio is None or not RATIO_MIN <= ratio <= RATIO_MAX:
            continue
        rec["ratio"] = ratio
        cells.setdefault((rec["region"], rec["district"]), []).append(rec)

    out: list[dict[str, Any]] = []
    for (region, district), members in cells.items():
        if len(members) < MIN_STREETS:
            continue
        ratios = sorted(m["ratio"] for m in members)
        out.append({
            "slug": f"{slugify(region)}__{slugify(district)}",
            "d": district,
            "r": region,
            "rs": current_province(region),
            "rr": round(statistics.median(ratios), 2),
            "n": len(members),
            "ask": round(statistics.median([m["ask_vnd_m2"] for m in members]) / 1e6, 1),
            "st": round(statistics.median([m["state_vnd_m2"] for m in members]) / 1e6, 1),
            "doc": members[0]["doc"],
        })

    # Where a district sits against its own province's typical ratio. This is
    # the only comparison the level supports; an absolute ratio says nothing.
    by_province: dict[str, list[float]] = {}
    for item in out:
        by_province.setdefault(item["rs"], []).append(item["rr"])
    medians = {k: statistics.median(v) for k, v in by_province.items()}
    for item in out:
        peer = medians.get(item["rs"])
        item["rm"] = round(peer, 2) if peer else None
        item["rp"] = round(item["rr"] / peer, 2) if peer else None

    out.sort(key=lambda i: i["slug"])
    stats = {
        "districts": len(out),
        "matched_streets": sum(i["n"] for i in out),
        "provinces_collected": len(coverage),
        "min_streets": MIN_STREETS,
    }

    if not dry_run:
        from pipeline.publish.emit import write_json
        stats["path"] = str(write_json("land_price.json", {
            "rows": out,
            "coverage": [
                {"p": p, "streets": n, "doc": doc} for p, n, doc in coverage
            ],
            "districts": stats["districts"],
            "matched_streets": stats["matched_streets"],
            "min_streets": MIN_STREETS,
            "source": {
                "name": "Bảng giá đất do HĐND/UBND cấp tỉnh ban hành",
                "kind": "Giá nhà nước — nền tính phí, không phải giá thị trường",
                "via": "thuviennhadat.vn",
            },
        }))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish state land prices and asking/state ratios"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(json.dumps(build(dry_run=args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
