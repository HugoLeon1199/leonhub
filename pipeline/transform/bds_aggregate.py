"""Aggregate raw listings into per-district price statistics.

Asking-price data is noisy in specific, correctable ways, and each guard here
exists because the raw numbers lie without it:

- **Sale and rent must never mix.** A rental ad in Quan 1 reports 0.24
  million/m2 against 208 for a sale. Mixing them does not add noise, it moves
  the median by three orders of magnitude. The collector tags the lane in
  `source`; this module keeps them apart and uses rent only for yield.
- **Median, not mean.** A single mispriced villa drags a mean past every real
  listing in the district.
- **Clip p5-p95 before aggregating**, because fat-finger prices (a house listed
  at 1 billion/m2) and placeholder prices (0) both occur.
- **Reposts inflate the sample.** The same property is relisted for weeks; a
  naive count makes a thin district look well-sampled. Deduplication keeps the
  latest observation per `list_id`, then collapses near-identical listings.
- **Hide thin cells.** Below `MIN_SAMPLES` the median is not a statistic, it is
  an anecdote. Turtle uses the same threshold; we publish `n` so the reader can
  judge, and withhold the cell entirely when it is too thin.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from pipeline.core import warehouse as wh

log = logging.getLogger(__name__)

MIN_SAMPLES = 20          # below this a district/type cell is not published
CLIP_LOW, CLIP_HIGH = 0.05, 0.95
# Sanity bounds for VN asking prices, million VND per m2.
PRICE_MIN, PRICE_MAX = 1.0, 3000.0
# Rental yield needs monthly rent per m2 in a believable band.
RENT_MIN, RENT_MAX = 0.02, 5.0

AGGREGATE_SQL = f"""
WITH latest AS (
    -- One row per listing: its most recent observation.
    SELECT DISTINCT ON (list_id)
        list_id, source, region, district, ward, category,
        price, size_m2, price_per_m2, latitude, longitude, as_of, fetched_at
    FROM re_listing
    WHERE price_per_m2 IS NOT NULL
      AND size_m2 > 0
    ORDER BY list_id, fetched_at DESC
),
deduped AS (
    -- Collapse reposts: same district, category, rounded size and price is
    -- almost certainly the same property listed again.
    SELECT DISTINCT ON (region, district, category, source,
                        round(size_m2), round(price / 1e6))
        *
    FROM latest
    ORDER BY region, district, category, source,
             round(size_m2), round(price / 1e6), fetched_at DESC
),
sale AS (
    SELECT * FROM deduped
    WHERE source = 'chotot:s'
      AND price_per_m2 BETWEEN {PRICE_MIN} AND {PRICE_MAX}
),
bounds AS (
    SELECT region, district, category,
           quantile_cont(price_per_m2, {CLIP_LOW})  AS lo,
           quantile_cont(price_per_m2, {CLIP_HIGH}) AS hi
    FROM sale GROUP BY 1, 2, 3
),
clipped AS (
    SELECT s.*
    FROM sale s JOIN bounds b USING (region, district, category)
    WHERE s.price_per_m2 BETWEEN b.lo AND b.hi
)
SELECT
    region,
    district,
    category,
    count(*)                                        AS n,
    median(price_per_m2)                            AS median_ppm2,
    quantile_cont(price_per_m2, 0.25)               AS p25_ppm2,
    quantile_cont(price_per_m2, 0.75)               AS p75_ppm2,
    median(size_m2)                                 AS median_size,
    median(price)                                   AS median_price,
    avg(latitude)                                   AS lat,
    avg(longitude)                                  AS lon,
    max(fetched_at)                                 AS updated_at
FROM clipped
GROUP BY 1, 2, 3
HAVING count(*) >= {MIN_SAMPLES}
ORDER BY region, district, category
"""

# Rental yield: median monthly rent per m2 x 12 / median sale price per m2.
YIELD_SQL = f"""
WITH latest AS (
    SELECT DISTINCT ON (list_id)
        list_id, source, region, district, category, price, size_m2, price_per_m2, fetched_at
    FROM re_listing
    WHERE price_per_m2 IS NOT NULL AND size_m2 > 0
    ORDER BY list_id, fetched_at DESC
),
rent AS (
    SELECT region, district, category,
           median(price_per_m2) AS rent_ppm2,
           count(*) AS rent_n
    FROM latest
    WHERE source = 'chotot:u'
      AND price_per_m2 BETWEEN {RENT_MIN} AND {RENT_MAX}
    GROUP BY 1, 2, 3
    HAVING count(*) >= {MIN_SAMPLES}
)
SELECT * FROM rent
"""


def slugify(text: str) -> str:
    """ASCII slug for Vietnamese place names, used as a stable row key."""
    import unicodedata
    import re

    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text


def build(dry_run: bool = False) -> dict[str, Any]:
    con = wh.connect_reader()
    try:
        rows = con.execute(AGGREGATE_SQL).fetchall()
        cols = [d[0] for d in con.description]
        yields = {
            (r[0], r[1], r[2]): (r[3], r[4])
            for r in con.execute(YIELD_SQL).fetchall()
        }
    finally:
        con.close()

    out = []
    for row in rows:
        rec = dict(zip(cols, row))
        region, district, category = rec["region"], rec["district"], rec["category"]
        rent = yields.get((region, district, category))

        item: dict[str, Any] = {
            "slug": f"{slugify(region)}__{slugify(district)}",
            "d": district,
            "r": region,
            "i": category,
            "p": round(rec["median_ppm2"], 1),
            "p25": round(rec["p25_ppm2"], 1),
            "p75": round(rec["p75_ppm2"], 1),
            "sz": round(rec["median_size"], 0),
            "n": rec["n"],
            "u": rec["updated_at"].isoformat() if rec["updated_at"] else None,
        }
        if rec["lat"] and rec["lon"]:
            item["lat"] = round(rec["lat"], 5)
            item["lon"] = round(rec["lon"], 5)
        if rent:
            rent_ppm2, rent_n = rent
            item["y"] = round(rent_ppm2 * 12 / rec["median_ppm2"] * 100, 2)
            item["yn"] = rent_n
        out.append(item)

    stats = {
        "rows": len(out),
        "districts": len({(o["r"], o["d"]) for o in out}),
        "with_yield": sum(1 for o in out if "y" in o),
        "min_samples": MIN_SAMPLES,
        # Sample count behind the whole file, so the page can say what the
        # medians rest on rather than only the per-cell n.
        "listings": sum(o.get("n", 0) for o in out),
    }

    if not dry_run:
        from pipeline.publish.emit import write_json
        # Wrapped so the page can state how old the crawl is; a bare array
        # leaves a stale file looking identical to a fresh one.
        stats["path"] = str(write_json("bds.json", {
            "rows": out,
            "listings": stats.get("listings"),
        }))

    stats["sample"] = out[:3]
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate listings into bds.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(build(args.dry_run), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
