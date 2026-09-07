"""Publish per-district listing shards for the property browse view.

This is a second publisher over `re_listing`, deliberately separate from
`bds_aggregate.py` rather than an extension of it, because the two answer
different questions and one of the aggregate's guards is actively wrong here.

`bds_aggregate` withholds any cell with fewer than 20 observations. That is
correct for a median -- a district median from eight listings is noise -- but
wrong for browsing, because a rural district with eight plots is exactly where
a buyer wants to look. The statistical guards stay on the statistics; this file
publishes what is actually listed and lets the reader see the sample size.

What does carry over is the honesty framing. These are asking prices from
public listings, not transaction prices, and each row keeps its own `n`-less
provenance: the source, the observation time, and a link back to the original
ad rather than a wholesale copy of it.

Sharding follows the `data/ticker/*.json` precedent: one small file per
district, fetched lazily when a district is opened. A single national file
would be 12-20 MB downloaded on every page load, and -- worse for a repo with a
~1GB soft cap -- would be rewritten wholesale every day, defeating the
unchanged-file check in emit.py.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

from pipeline.core import warehouse as wh
from pipeline.publish.emit import read_json, write_json
from pipeline.transform.bds_aggregate import (
    CATEGORY_SLUGS,
    PRICE_MAX,
    PRICE_MIN,
    current_province,
    slugify,
)

log = logging.getLogger(__name__)

# Per district. A hot district in HCM has thousands of live ads; publishing all
# of them would push a single shard past what a browser should download to show
# a first page. Newest first, because listing age is itself information.
MAX_PER_DISTRICT = 400

# Districts below this are still published -- that is the point of this file --
# but the UI is told how thin the sample is so it can say so.
THIN_SAMPLE = 20

LISTING_SQL = f"""
WITH latest AS (
    -- One row per listing: its most recent observation. Append-only storage
    -- means a listing seen on ten days has ten rows; the newest is the one
    -- whose price and status are current.
    SELECT DISTINCT ON (list_id)
        list_id, source, region, district, ward, ward_v3, region_v3,
        category, house_type, price, size_m2, price_per_m2, rooms, toilets,
        direction, floors, latitude, longitude, street_name, subject,
        legal_doc, is_agent, account_name, image_count, thumbnail,
        status, price_string, as_of, orig_list_time, fetched_at
    FROM re_listing
    WHERE price_per_m2 IS NOT NULL
      AND size_m2 > 0
      AND price > 0
    ORDER BY list_id, fetched_at DESC
)
SELECT *
FROM latest
WHERE source = 'chotot:s'
  -- Same sanity band as the aggregate. A listing outside it is a rental that
  -- leaked into the sale lane or a typo'd price, and either would mislead a
  -- reader more than its absence does.
  AND price_per_m2 BETWEEN {PRICE_MIN} AND {PRICE_MAX}
  -- Sold and expired ads are history, not inventory. Older rows predate the
  -- status column entirely, so NULL has to pass.
  AND (status IS NULL OR status = 'active')
  -- The three published numbers must agree. Chotot computes
  -- price_million_per_m2 from fields the poster typed, so a mistyped area
  -- ("Bán đất", 24 billion, size 1.4 -- hectares entered as m2) yields a
  -- price/m2 that passes the sane band while the total price does not match it
  -- at all. Publishing that would show a plot at 1.7 million/m2 that is really
  -- 17,000. Drop the row rather than guess which of the two fields is wrong.
  AND abs((price / 1e6 / size_m2) / price_per_m2 - 1) <= 0.05
ORDER BY region, district, as_of DESC
"""


def _shard_key(region: str, district: str) -> str:
    return f"{slugify(region)}__{slugify(district)}"


def _listing_row(row: dict[str, Any]) -> dict[str, Any]:
    """One published listing. Short keys, per the emit.py convention.

    Only fields a browser view actually renders are published. The ad body is
    deliberately NOT included: it is collected so the warehouse holds it, but
    republishing thousands of full ad texts is wholesale redistribution rather
    than the aggregate-plus-link this project publishes. `u` links back to the
    source so the reader reads the ad where it was written.
    """
    out: dict[str, Any] = {
        "id": row["list_id"],
        "t": row.get("subject"),
        # Total price in billions and price per m2 in millions -- the two units
        # VN property is actually quoted in.
        "tp": round(row["price"] / 1e9, 3),
        "p": round(row["price_per_m2"], 1),
        "sz": round(row["size_m2"], 1),
        "w": row.get("ward_v3") or row.get("ward"),
        "st": row.get("street_name"),
        "c": CATEGORY_SLUGS.get(row.get("category") or "", "khac"),
        "d": row["as_of"].date().isoformat() if row.get("as_of") else None,
        # Chotot's canonical ad URL. Built from list_id rather than stored,
        # because the gateway does not return one.
        "u": f"https://www.nhatot.com/{row['list_id']}.htm",
    }
    # Optional fields are omitted rather than published as null: absent by
    # category is the normal case (land has no toilets) and every omitted key
    # is bytes off every page load.
    for key, column in (("r", "rooms"), ("wc", "toilets"), ("fl", "floors")):
        value = row.get(column)
        if value:
            out[key] = int(value)
    if row.get("direction"):
        out["dir"] = row["direction"]
    if row.get("legal_doc") is not None:
        # Raw code, still untranslated -- see the column note in warehouse.py.
        out["lg"] = int(row["legal_doc"])
    # company_ad marks a brokerage posting and is simply absent for private
    # sellers -- the source never sends false. So absence carries meaning, but
    # only on rows collected after the field existed: an older observation is
    # missing it because we did not ask, which is a different thing. account_name
    # is present on every modern row and so distinguishes the two cases.
    if row.get("is_agent"):
        out["ag"] = 1
    elif row.get("account_name"):
        out["ag"] = 0
    if row.get("image_count"):
        out["im"] = int(row["image_count"])
    if row.get("latitude") and row.get("longitude"):
        out["lat"] = round(row["latitude"], 5)
        out["lon"] = round(row["longitude"], 5)
    # A listing whose first publish predates this observation has been reposted.
    # Days-since-first-listed is the honest measure of how long it has sat.
    if row.get("orig_list_time") and row.get("as_of"):
        age = (row["as_of"] - row["orig_list_time"]).days
        if age > 0:
            out["age"] = age
    return out


def build(dry_run: bool = False) -> dict[str, Any]:
    con = wh.connect_reader()
    try:
        cursor = con.execute(LISTING_SQL)
        columns = [c[0] for c in cursor.description]
        rows = [dict(zip(columns, record)) for record in cursor.fetchall()]
    finally:
        con.close()

    shards: dict[str, dict[str, Any]] = {}
    for row in rows:
        region, district = row.get("region"), row.get("district")
        if not region or not district:
            continue
        key = _shard_key(region, district)
        shard = shards.setdefault(key, {
            "d": district,
            "r": region,
            "rs": current_province(region),
            "rows": [],
        })
        if len(shard["rows"]) < MAX_PER_DISTRICT:
            shard["rows"].append(_listing_row(row))

    index = []
    written = 0
    for key, shard in sorted(shards.items()):
        shard["n"] = len(shard["rows"])
        shard["thin"] = 1 if shard["n"] < THIN_SAMPLE else 0
        shard["source"] = "chotot"
        # Asking prices, stated in the artifact rather than only in the UI, so
        # the label cannot be lost by a page that forgets to add it.
        shard["note"] = "Giá rao từ tin đăng công khai — tham khảo, không phải giá giao dịch."
        index.append({
            "k": key, "d": shard["d"], "r": shard["r"], "rs": shard["rs"],
            "n": shard["n"], "thin": shard["thin"],
        })
        if not dry_run:
            # Preserve the previous timestamp when nothing else changed, so an
            # unchanged district does not churn git history. Same reasoning as
            # ticker_details_build.
            previous = read_json(f"bds/listings/{key}.json")
            if previous and {k: v for k, v in previous.items() if k != "updated_at"} == shard:
                continue
            write_json(f"bds/listings/{key}.json", shard)
            written += 1

    summary = {
        "districts": len(shards),
        "listings": sum(len(s["rows"]) for s in shards.values()),
        "thin_districts": sum(1 for s in shards.values() if s["thin"]),
        "shards_written": written,
        "observations_in": len(rows),
    }
    if not dry_run:
        write_json("bds/listings/index.json", {
            "rows": sorted(index, key=lambda r: (-r["n"], r["k"])),
            "districts": len(index),
            "listings": summary["listings"],
            "max_per_district": MAX_PER_DISTRICT,
            "thin_below": THIN_SAMPLE,
            "source": "chotot",
        })
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Build data/bds/listings/*.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    import json
    print(json.dumps(build(dry_run=args.dry_run), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
