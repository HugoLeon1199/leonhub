"""Listing-level panel metrics: price cuts, time on market, disappearance.

Every other consumer of `re_listing` opens with `DISTINCT ON (list_id) ORDER BY
fetched_at DESC` and throws the history away. This module is the one that reads
it, and it is what the append-only primary key `(list_id, fetched_at)` was for:
the same advertisement observed across days is a panel, and a panel answers
questions a price snapshot cannot. Is the seller cutting? How long has this been
sitting? Is inventory clearing or accumulating?

None of it needs a new request -- the observations are already in the warehouse.

Three traps, each of which produces a confident wrong number if ignored:

1. **A listing "disappearing" means nothing unless we crawled its cell that
   day.** On 2026-09-07 the local run covered two provinces, so 90% of listings
   "vanished" for reasons that have nothing to do with the market. Every
   disappearance figure is conditioned on the crawl history of its own cell.

2. **Days-on-market is left-truncated by our own start date.** A listing first
   seen on day one may have been live for a year. The measure is therefore
   published as a *lower bound* with the truncated share (`dmc`) beside it, and
   the UI reads "≥ N days". As the warehouse ages, `dmc` falls on its own and
   the bound converges on the real figure -- no code change, only a label that
   stops needing the qualifier.

3. **A vanished ad is not a sale.** It may have sold, expired, been withdrawn,
   or been deleted and reposted under a fresh `list_id` -- and Chotot reposts
   constantly. `gr` is therefore labelled "listing disappeared", never "sold",
   and `grx` subtracts the disappearances that reappear nearby at a similar
   price within REPOST_WINDOW_DAYS.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from pipeline.core import warehouse as wh
from pipeline.transform.bds_aggregate import (
    CATEGORY_SLUGS,
    MIN_SAMPLES,
    PRICE_MAX,
    PRICE_MIN,
    slugify,
)

log = logging.getLogger(__name__)

# A price is only "cut" if it moved more than this. Chotot re-emits the same ad
# with small numeric jitter across reposts, and a 1% wobble is not a decision by
# a seller.
CUT_THRESHOLD = 0.02

# A listing whose area changed by more than this between observations is not the
# same property any more. Sellers edit a live ad rather than repost it -- one
# observed listing went 6,500 -> 650 million as its area went 144 -> 44 m2 --
# and reading that as a 90% discount would be badly wrong. Measured on the local
# warehouse these are 9 of 93 apparent cuts, so the guard is worth ~10% of the
# published rate.
SIZE_DRIFT_TOLERANCE = 0.02

# A listing absent from this many of its cell's most recent crawls is treated as
# gone. One missed crawl is noise -- pagination is not perfectly stable between
# runs -- so a listing has to miss two consecutive looks.
GONE_AFTER_CRAWLS = 2

# Below this many distinct crawl days for a cell, a disappearance rate is not
# measurable and is published as null rather than as a number.
MIN_CRAWLS_FOR_GONE = 3

# A vanished listing that reappears within this window at a comparable price is
# a repost, not a sale.
REPOST_WINDOW_DAYS = 7


# The panel itself. One row per listing, carrying its whole observed life.
PANEL_SQL = f"""
WITH obs AS (
    SELECT list_id, region, district, category, source,
           price, size_m2, price_per_m2, fetched_at
    FROM re_listing
    WHERE price > 0 AND size_m2 > 0
      AND price_per_m2 IS NOT NULL
      AND source = 'chotot:s'
      AND price_per_m2 BETWEEN {PRICE_MIN} AND {PRICE_MAX}
),
panel AS (
    SELECT
        list_id,
        -- Not any_value(): a poster can edit the address of a live ad, and one
        -- listing really did move from Quận 10 to Quận 8 between crawls. With
        -- any_value() the cell it lands in is chosen arbitrarily and the build
        -- stops being reproducible -- two runs over an unchanged warehouse
        -- disagreed on two districts. The newest observation is the answer.
        arg_max(region, fetched_at)           AS region,
        arg_max(district, fetched_at)         AS district,
        arg_max(category, fetched_at)         AS category,
        min(fetched_at)                       AS first_seen,
        max(fetched_at)                       AS last_seen,
        count(DISTINCT fetched_at::DATE)      AS days_seen,
        arg_min(price, fetched_at)            AS price_first,
        arg_max(price, fetched_at)            AS price_last,
        arg_min(size_m2, fetched_at)          AS size_first,
        arg_max(size_m2, fetched_at)          AS size_last
    FROM obs
    GROUP BY list_id
),
-- Distinct crawl days per cell, newest first, so "absent from the last two
-- crawls" can be expressed without assuming a daily cadence.
cell_days AS (
    SELECT region, category, fetched_at::DATE AS day,
           row_number() OVER (PARTITION BY region, category
                              ORDER BY fetched_at::DATE DESC) AS recency
    FROM obs
    GROUP BY region, category, fetched_at::DATE
),
cell_stats AS (
    SELECT region, category,
           max(recency)                                        AS crawl_days,
           min(day) FILTER (WHERE recency <= {GONE_AFTER_CRAWLS}) AS gone_cutoff,
           min(day)                                            AS first_crawl_day
    FROM cell_days GROUP BY region, category
)
SELECT
    p.region, p.district, p.category,
    p.list_id, p.first_seen, p.last_seen, p.days_seen,
    p.price_first, p.price_last, p.size_first, p.size_last,
    c.crawl_days, c.gone_cutoff, c.first_crawl_day
FROM panel p
JOIN cell_stats c ON c.region = p.region AND c.category = p.category
"""

# Listings that vanished but look like they came back: same district and
# category, size and price within a couple of percent, first seen shortly after
# the original was last seen. These are reposts, and counting them as
# disappearances would overstate how much inventory is clearing.
REPOST_SQL = f"""
WITH obs AS (
    SELECT list_id, region, district, category,
           price, size_m2, fetched_at
    FROM re_listing
    WHERE price > 0 AND size_m2 > 0 AND source = 'chotot:s'
),
panel AS (
    SELECT list_id,
           arg_max(region, fetched_at) AS region,
           arg_max(district, fetched_at) AS district,
           arg_max(category, fetched_at) AS category,
           min(fetched_at) AS first_seen, max(fetched_at) AS last_seen,
           arg_max(price, fetched_at) AS price_last,
           arg_max(size_m2, fetched_at) AS size_last
    FROM obs GROUP BY list_id
)
SELECT DISTINCT a.list_id
FROM panel a JOIN panel b
  ON  b.region = a.region AND b.district = a.district
  AND b.category = a.category
  AND b.list_id <> a.list_id
  AND b.first_seen > a.last_seen
  AND date_diff('day', a.last_seen, b.first_seen) <= {REPOST_WINDOW_DAYS}
  AND abs(b.size_last - a.size_last) <= 0.02 * a.size_last
  AND abs(b.price_last - a.price_last) <= 0.02 * a.price_last
"""


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def build(dry_run: bool = False) -> dict[str, Any]:
    con = wh.connect_reader()
    try:
        cols = None
        rows = con.execute(PANEL_SQL).fetchall()
        cols = [d[0] for d in con.description]
        reposted = {r[0] for r in con.execute(REPOST_SQL).fetchall()}
    finally:
        con.close()

    # Bucket the panel by the same cell the aggregate publishes.
    cells: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        rec = dict(zip(cols, row))
        cells.setdefault((rec["region"], rec["district"], rec["category"]), []).append(rec)

    out: dict[str, dict[str, Any]] = {}
    for (region, district, category), members in cells.items():
        if len(members) < MIN_SAMPLES:
            continue
        slug = f"{slugify(region)}__{slugify(district)}"
        key = f"{slug}__{CATEGORY_SLUGS.get(category, slugify(category))}"

        # --- Price cuts. Directly observed, no coverage conditioning needed.
        # Only listings seen more than once can show a change at all, so the
        # denominator is that cohort rather than every listing in the cell.
        # An ad whose area was edited describes a different property, so its
        # price change is not a concession and it leaves the cohort entirely --
        # it cannot count as a cut, and it must not pad the denominator either.
        repeat = [
            m for m in members
            if m["days_seen"] > 1
            and abs(m["size_last"] - m["size_first"])
            <= SIZE_DRIFT_TOLERANCE * m["size_first"]
        ]
        cuts = [
            m for m in repeat
            if m["price_last"] < m["price_first"] * (1 - CUT_THRESHOLD)
        ]
        item: dict[str, Any] = {"k": key, "pcn": len(repeat)}
        item["pc"] = _pct(len(cuts), len(repeat))
        if cuts:
            deltas = sorted(
                (m["price_last"] / m["price_first"] - 1) * 100 for m in cuts
            )
            mid = len(deltas) // 2
            median_cut = (
                deltas[mid] if len(deltas) % 2
                else (deltas[mid - 1] + deltas[mid]) / 2
            )
            item["pcm"] = round(median_cut, 1)

        # --- Days on market, as a lower bound. `dmc` is the share of the cell
        # whose first observation is our own first crawl of that cell: for those
        # the true start date is unknown and earlier than what we can see.
        spans = sorted(
            (m["last_seen"] - m["first_seen"]).days for m in members
        )
        mid = len(spans) // 2
        item["dm"] = (
            spans[mid] if len(spans) % 2 else (spans[mid - 1] + spans[mid]) // 2
        )
        truncated = sum(
            1 for m in members
            if m["first_crawl_day"] and m["first_seen"].date() <= m["first_crawl_day"]
        )
        item["dmc"] = _pct(truncated, len(members))

        # --- Disappearance. Null unless the cell has been crawled enough times
        # for absence to mean anything.
        crawl_days = members[0]["crawl_days"] or 0
        if crawl_days >= MIN_CRAWLS_FOR_GONE:
            cutoff = members[0]["gone_cutoff"]
            gone = [m for m in members if cutoff and m["last_seen"].date() < cutoff]
            item["gr"] = _pct(len(gone), len(members))
            item["grn"] = len(members)
            still_gone = [m for m in gone if m["list_id"] not in reposted]
            item["grx"] = _pct(len(still_gone), len(members))
        out[key] = item

    stats = {
        "cells": len(out),
        "listings_in_panel": len(rows),
        "repeat_observed": sum(i.get("pcn", 0) for i in out.values()),
        "reposts_detected": len(reposted),
        "cells_with_gone_rate": sum(1 for i in out.values() if i.get("gr") is not None),
        "cut_threshold": CUT_THRESHOLD,
        "gone_after_crawls": GONE_AFTER_CRAWLS,
        "repost_window_days": REPOST_WINDOW_DAYS,
    }

    if not dry_run:
        from pipeline.publish.emit import write_json
        stats["path"] = str(write_json("bds_panel.json", {
            # Sorted by key, not left in dict order: the SQL returns rows in
            # whatever order it likes, so an unsorted list makes two builds over
            # an unchanged warehouse differ only by ordering -- which defeats
            # emit.py's unchanged-file check and rewrites the file nightly for
            # nothing.
            "cells": sorted(out.values(), key=lambda c: c["k"]),
            # Counters are named apart from `cells` so neither shadows the
            # other: the page reads `cells` as the data and these as provenance.
            "cell_count": stats["cells"],
            "listings_in_panel": stats["listings_in_panel"],
            "repeat_observed": stats["repeat_observed"],
            "reposts_detected": stats["reposts_detected"],
            "cells_with_gone_rate": stats["cells_with_gone_rate"],
            "cut_threshold": CUT_THRESHOLD,
            "size_drift_tolerance": SIZE_DRIFT_TOLERANCE,
            "gone_after_crawls": GONE_AFTER_CRAWLS,
            "min_crawls_for_gone": MIN_CRAWLS_FOR_GONE,
            "repost_window_days": REPOST_WINDOW_DAYS,
            "source": {
                "name": "Chợ Tốt/Nhà Tốt",
                "kind": "Panel tin rao do LEON tự tích lũy",
                "url": "https://www.chotot.com/mua-ban-bat-dong-san",
            },
        }))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build listing-panel metrics from the observation history"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(json.dumps(build(dry_run=args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
