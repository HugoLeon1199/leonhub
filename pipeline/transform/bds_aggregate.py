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
- **Age out listings we have stopped seeing.** A sold or withdrawn ad leaves the
  feed silently -- Chotot never reports a `sold` status -- so without a cutoff
  its last price sets the median forever. Measured on a five-day warehouse, 27%
  of the rows behind the medians were already stale.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import statistics
from collections import defaultdict
from datetime import date
from typing import Any

from pipeline.core import warehouse as wh

log = logging.getLogger(__name__)

MIN_SAMPLES = 20          # below this a district/type cell is not published
# How far behind its cell's own last crawl a listing may be and still count.
# Three days tolerates two consecutive failed nightly runs; beyond that the
# listing is assumed gone from the feed rather than merely unobserved.
STALE_AFTER_DAYS = 3
CLIP_LOW, CLIP_HIGH = 0.05, 0.95
# Sanity bounds for VN asking prices, million VND per m2.
PRICE_MIN, PRICE_MAX = 1.0, 3000.0
# Rental yield needs monthly rent per m2 in a believable band.
RENT_MIN, RENT_MAX = 0.02, 5.0
# Annualising a handful of early snapshots produces nonsense.  Keep CAGR
# unavailable until the warehouse has at least six observations spread across
# six months.  The UI explains this instead of substituting zero.
CAGR_MIN_DAYS = 180
CAGR_MIN_POINTS = 6

# Post-2025 province structure (Decision 19/2025/QD-TTg).  Chotot still emits
# the former 63-province names, which is useful because its district names are
# still the familiar pre-merger labels.  We retain `r` as the source label and
# publish `rs` as the current 34-province group.
# Mapping cross-checked against the MIT-licensed vietnam-map-34-provinces data:
# https://github.com/hoccungduy/vietnam-map-34-provinces
PROVINCES: list[dict[str, Any]] = [
    {"name": "Hà Nội", "slug": "ha-noi", "from": ["Hà Nội", "Hà Tây"]},
    {"name": "Bắc Ninh", "slug": "bac-ninh", "from": ["Bắc Ninh", "Bắc Giang"]},
    {"name": "Quảng Ninh", "slug": "quang-ninh", "from": ["Quảng Ninh"]},
    {"name": "Hải Phòng", "slug": "hai-phong", "from": ["Hải Phòng", "Hải Dương"]},
    {"name": "Hưng Yên", "slug": "hung-yen", "from": ["Hưng Yên", "Thái Bình"]},
    {"name": "Ninh Bình", "slug": "ninh-binh", "from": ["Ninh Bình", "Nam Định", "Hà Nam"]},
    {"name": "Cao Bằng", "slug": "cao-bang", "from": ["Cao Bằng"]},
    {"name": "Tuyên Quang", "slug": "tuyen-quang", "from": ["Tuyên Quang", "Hà Giang"]},
    {"name": "Lào Cai", "slug": "lao-cai", "from": ["Lào Cai", "Yên Bái"]},
    {"name": "Thái Nguyên", "slug": "thai-nguyen", "from": ["Thái Nguyên", "Bắc Kạn"]},
    {"name": "Lạng Sơn", "slug": "lang-son", "from": ["Lạng Sơn"]},
    {"name": "Phú Thọ", "slug": "phu-tho", "from": ["Phú Thọ", "Vĩnh Phúc", "Hòa Bình"]},
    {"name": "Điện Biên", "slug": "dien-bien", "from": ["Điện Biên"]},
    {"name": "Lai Châu", "slug": "lai-chau", "from": ["Lai Châu"]},
    {"name": "Sơn La", "slug": "son-la", "from": ["Sơn La"]},
    {"name": "Thanh Hóa", "slug": "thanh-hoa", "from": ["Thanh Hóa"]},
    {"name": "Nghệ An", "slug": "nghe-an", "from": ["Nghệ An"]},
    {"name": "Hà Tĩnh", "slug": "ha-tinh", "from": ["Hà Tĩnh"]},
    {"name": "Quảng Trị", "slug": "quang-tri", "from": ["Quảng Trị", "Quảng Bình"]},
    {"name": "Huế", "slug": "hue", "from": ["Thừa Thiên Huế", "Huế"]},
    {"name": "Đà Nẵng", "slug": "da-nang", "from": ["Đà Nẵng", "Quảng Nam"]},
    {"name": "Quảng Ngãi", "slug": "quang-ngai", "from": ["Quảng Ngãi", "Kon Tum"]},
    {"name": "Khánh Hòa", "slug": "khanh-hoa", "from": ["Khánh Hòa", "Ninh Thuận"]},
    {"name": "Gia Lai", "slug": "gia-lai", "from": ["Gia Lai", "Bình Định"]},
    {"name": "Đắk Lắk", "slug": "dak-lak", "from": ["Đắk Lắk", "Phú Yên"]},
    {"name": "Lâm Đồng", "slug": "lam-dong", "from": ["Lâm Đồng", "Đắk Nông", "Bình Thuận"]},
    {"name": "Tây Ninh", "slug": "tay-ninh", "from": ["Tây Ninh", "Long An"]},
    {"name": "Đồng Nai", "slug": "dong-nai", "from": ["Đồng Nai", "Bình Phước"]},
    {"name": "Hồ Chí Minh", "slug": "ho-chi-minh", "from": ["Hồ Chí Minh", "Bình Dương", "Bà Rịa - Vũng Tàu"]},
    {"name": "Vĩnh Long", "slug": "vinh-long", "from": ["Vĩnh Long", "Bến Tre", "Trà Vinh"]},
    {"name": "Đồng Tháp", "slug": "dong-thap", "from": ["Đồng Tháp", "Tiền Giang"]},
    {"name": "An Giang", "slug": "an-giang", "from": ["An Giang", "Kiên Giang"]},
    {"name": "Cần Thơ", "slug": "can-tho", "from": ["Cần Thơ", "Hậu Giang", "Sóc Trăng"]},
    {"name": "Cà Mau", "slug": "ca-mau", "from": ["Cà Mau", "Bạc Liêu"]},
]

CATEGORY_SLUGS = {
    "Đất": "dat",
    "Nhà ở": "nha-rieng",
    "Căn hộ/Chung cư": "chung-cu",
    "Văn phòng, Mặt bằng kinh doanh": "van-phong",
}

# Every listing's newest observation, dropping the ones we have stopped seeing.
#
# A listing that has left the feed -- sold, expired, withdrawn -- keeps its last
# observed price forever unless it is aged out, so without this the median is
# increasingly set by properties that are no longer for sale. Measured on a
# five-day warehouse: 27% of the rows feeding the medians were last seen on an
# earlier day, and that share only grows.
#
# The cutoff is relative to the last time we crawled THAT CELL, never to today.
# A national pass spans hours and a failed run would otherwise age out a whole
# province at once -- blanking the map is a worse failure than staleness.
FRESH_CTE = f"""
crawl AS (
    -- When we last looked at this cell at all.
    SELECT region, category, max(fetched_at) AS cell_crawled_at
    FROM re_listing GROUP BY 1, 2
),
observed AS (
    SELECT DISTINCT ON (list_id)
        list_id, source, region, district, ward, category,
        price, size_m2, price_per_m2, latitude, longitude, as_of, fetched_at
    FROM re_listing
    WHERE price_per_m2 IS NOT NULL
      AND size_m2 > 0
    ORDER BY list_id, fetched_at DESC
),
latest AS (
    SELECT o.*
    FROM observed o JOIN crawl c USING (region, category)
    WHERE date_diff('day', o.fetched_at, c.cell_crawled_at) <= {STALE_AFTER_DAYS}
)"""

AGGREGATE_SQL = f"""
WITH {FRESH_CTE},
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
    -- Age of the ad when we observed it, NOT days-on-market: it is bounded
    -- below by our own start date and reset by every repost. The panel build
    -- computes the real first-seen-to-last-seen figure.
    median(greatest(0, date_diff('day', as_of, fetched_at))) AS median_age,
    avg(latitude)                                   AS lat,
    avg(longitude)                                  AS lon,
    max(fetched_at)                                 AS updated_at
FROM clipped
GROUP BY 1, 2, 3
HAVING count(*) >= {MIN_SAMPLES}
ORDER BY region, district, category
"""

# Rental yield: median monthly rent per m2 x 12 / median sale price per m2.
#
# The rent leg applies the same three guards as the sale leg -- freshness,
# repost collapse, and p5-p95 clipping. It previously applied only the first,
# which made the yield a ratio of two differently-cleaned numbers: measured, the
# median yield moved 2.3% and the worst cell 18.8%. Two legs, one rule.
YIELD_SQL = f"""
WITH {FRESH_CTE},
deduped AS (
    SELECT DISTINCT ON (region, district, category, source,
                        round(size_m2), round(price / 1e6))
        *
    FROM latest
    ORDER BY region, district, category, source,
             round(size_m2), round(price / 1e6), fetched_at DESC
),
rent_raw AS (
    SELECT * FROM deduped
    WHERE source = 'chotot:u'
      AND price_per_m2 BETWEEN {RENT_MIN} AND {RENT_MAX}
),
rent_bounds AS (
    SELECT region, district, category,
           quantile_cont(price_per_m2, {CLIP_LOW})  AS lo,
           quantile_cont(price_per_m2, {CLIP_HIGH}) AS hi
    FROM rent_raw GROUP BY 1, 2, 3
),
rent_clipped AS (
    SELECT r.*
    FROM rent_raw r JOIN rent_bounds b USING (region, district, category)
    WHERE r.price_per_m2 BETWEEN b.lo AND b.hi
)
SELECT region, district, category,
       median(price_per_m2) AS rent_ppm2,
       count(*)             AS rent_n
FROM rent_clipped
GROUP BY 1, 2, 3
HAVING count(*) >= {MIN_SAMPLES}
"""

# Daily district medians form an honest trend history.  Today this contains a
# single point because the warehouse only started on 2026-09-03; the scheduled
# crawl appends a new point automatically.  We deliberately do not backfill
# Turtle/Batdongsan values or manufacture a growth rate.
TREND_SQL = f"""
WITH daily_latest AS (
    SELECT * EXCLUDE (rn) FROM (
        SELECT
            date_trunc('day', fetched_at)::DATE AS day,
            list_id, source, region, district, category,
            price, size_m2, price_per_m2,
            row_number() OVER (
                PARTITION BY date_trunc('day', fetched_at), list_id
                ORDER BY fetched_at DESC
            ) AS rn
        FROM re_listing
        WHERE price_per_m2 IS NOT NULL AND size_m2 > 0
    ) WHERE rn = 1
),
deduped AS (
    SELECT DISTINCT ON (day, region, district, category, source,
                        round(size_m2), round(price / 1e6)) *
    FROM daily_latest
    ORDER BY day, region, district, category, source,
             round(size_m2), round(price / 1e6), list_id DESC
),
sale AS (
    SELECT * FROM deduped
    WHERE source = 'chotot:s'
      AND price_per_m2 BETWEEN {PRICE_MIN} AND {PRICE_MAX}
),
bounds AS (
    SELECT day, region, district, category,
           quantile_cont(price_per_m2, {CLIP_LOW}) AS lo,
           quantile_cont(price_per_m2, {CLIP_HIGH}) AS hi
    FROM sale GROUP BY 1, 2, 3, 4
),
clipped AS (
    SELECT s.* FROM sale s
    JOIN bounds b USING (day, region, district, category)
    WHERE s.price_per_m2 BETWEEN b.lo AND b.hi
)
SELECT day, region, district, category,
       count(*) AS n, median(price_per_m2) AS median_ppm2
FROM clipped
GROUP BY 1, 2, 3, 4
HAVING count(*) >= {MIN_SAMPLES}
ORDER BY day, region, district, category
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


_OLD_TO_NEW = {
    slugify(old): province["name"]
    for province in PROVINCES
    for old in province["from"]
}
_OLD_TO_NEW.update({"tp-ho-chi-minh": "Hồ Chí Minh", "thanh-pho-ho-chi-minh": "Hồ Chí Minh"})


def current_province(region: str) -> str:
    """Return the post-2025 province while preserving unknown source labels."""
    key = slugify(region)
    for prefix in ("tinh-", "thanh-pho-", "tp-"):
        if key.startswith(prefix):
            key = key[len(prefix):]
    return _OLD_TO_NEW.get(key, region)


def _median(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.median(clean) if clean else None


def _load_panel() -> dict[str, dict[str, Any]]:
    """Panel metrics keyed by `slug__category`, or empty if none are published.

    Absent is a real state, not an error: a fresh warehouse has no panel yet,
    and the page distinguishes "not measured" from a measured zero.
    """
    from pipeline.publish.emit import read_json

    payload = read_json("bds_panel.json")
    if not isinstance(payload, dict):
        return {}
    cells = payload.get("cells")
    if not isinstance(cells, list):
        return {}
    return {c["k"]: c for c in cells if isinstance(c, dict) and c.get("k")}


def _annualised(points: list[dict[str, Any]]) -> tuple[float | None, int]:
    """CAGR and span.  Null is a first-class result until history is mature."""
    if len(points) < CAGR_MIN_POINTS:
        return None, 0 if not points else (points[-1]["day"] - points[0]["day"]).days
    first, last = points[0], points[-1]
    span = (last["day"] - first["day"]).days
    if span < CAGR_MIN_DAYS or first["p"] <= 0 or last["p"] <= 0:
        return None, span
    value = (last["p"] / first["p"]) ** (365.25 / span) - 1
    # A larger number is normally a changed listing mix, not investable growth.
    if not math.isfinite(value) or abs(value) > 2:
        return None, span
    return round(value * 100, 1), span


def build(dry_run: bool = False) -> dict[str, Any]:
    con = wh.connect_reader()
    try:
        rows = con.execute(AGGREGATE_SQL).fetchall()
        cols = [d[0] for d in con.description]
        yields = {
            (r[0], r[1], r[2]): (r[3], r[4])
            for r in con.execute(YIELD_SQL).fetchall()
        }
        trend_rows = con.execute(TREND_SQL).fetchall()
        raw_stats = con.execute("""
            SELECT count(*) AS observations, count(DISTINCT list_id) AS listings,
                   count(DISTINCT date_trunc('day', fetched_at)) AS snapshot_days,
                   min(fetched_at), max(fetched_at)
            FROM re_listing
        """).fetchone()
        # Coverage only. Chotot publishes no dictionary for this code and the
        # correlation against the ads' own wording was not clean enough to
        # translate -- in VN property the gap between a titled and an untitled
        # plot is most of the price, so a guessed label would mislead about the
        # one field that matters most. Publishing the coverage says what we
        # hold without claiming to know what it means.
        legal_stats = con.execute("""
            SELECT count(*) FILTER (WHERE legal_doc IS NOT NULL), count(*)
            FROM re_listing WHERE source = 'chotot:s'
        """).fetchone()
        # How much of the raw pool the freshness rule excludes. Publishing this
        # is the point: it is the size of the error the medians used to carry,
        # and it lets a reader see the rule working rather than trust it.
        stale_stats = con.execute(f"""
            WITH crawl AS (
                SELECT region, category, max(fetched_at) AS cell_crawled_at
                FROM re_listing GROUP BY 1, 2
            ),
            observed AS (
                SELECT DISTINCT ON (list_id) list_id, region, category, fetched_at
                FROM re_listing ORDER BY list_id, fetched_at DESC
            )
            SELECT count(*) FILTER (
                       WHERE date_diff('day', o.fetched_at, c.cell_crawled_at)
                             <= {STALE_AFTER_DAYS}),
                   count(*)
            FROM observed o JOIN crawl c USING (region, category)
        """).fetchone()
    finally:
        con.close()

    # Panel metrics come from the file bds_panel_build already published rather
    # than from a second pass over the warehouse: the workflow runs the panel
    # first, DuckDB allows one connection at a time, and re-deriving them here
    # would let the two artifacts disagree. Missing file means the keys are
    # simply absent from the rows.
    panel_cells = _load_panel()

    out = []
    for row in rows:
        rec = dict(zip(cols, row))
        region, district, category = rec["region"], rec["district"], rec["category"]
        rent = yields.get((region, district, category))

        item: dict[str, Any] = {
            "slug": f"{slugify(region)}__{slugify(district)}",
            "d": district,
            "r": region,
            "rs": current_province(region),
            "i": category,
            "cs": CATEGORY_SLUGS.get(category, slugify(category)),
            "p": round(rec["median_ppm2"], 1),
            "p25": round(rec["p25_ppm2"], 1),
            "p75": round(rec["p75_ppm2"], 1),
            "sz": round(rec["median_size"], 0),
            "tp": round(rec["median_price"] / 1e9, 2),
            "age": round(rec["median_age"]),
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

    # Compact daily district history, keyed by the same slug as the table.
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for day_value, region, district, category, n, median_ppm2 in trend_rows:
        key = f"{slugify(region)}__{slugify(district)}__{CATEGORY_SLUGS.get(category, slugify(category))}"
        by_key[key].append({"day": day_value, "p": float(median_ppm2), "n": int(n)})

    history: list[dict[str, Any]] = []
    growth_by_key: dict[str, tuple[float | None, int]] = {}
    for key, points in by_key.items():
        points.sort(key=lambda point: point["day"])
        growth_by_key[key] = _annualised(points)
        for point in points:
            history.append({
                "d": point["day"].isoformat(), "k": key,
                "p": round(point["p"], 1), "n": point["n"],
            })

    for item in out:
        key = f'{item["slug"]}__{item["cs"]}'
        growth, span = growth_by_key.get(key, (None, 0))
        item["g"] = growth
        item["hd"] = span

    # Panel metrics ride along on the same rows rather than in a second file:
    # the grain is identical, and a separate fetch for the same key would cost
    # a request to say what these few keys say. Absent when the panel has not
    # been built, which the page must render as "not measured" rather than zero.
    for item in out:
        panel = panel_cells.get(f'{item["slug"]}__{item["cs"]}')
        if panel:
            for field in ("pc", "pcm", "pcn", "dm", "dmc", "gr", "grn", "grx"):
                if field in panel:
                    item[field] = panel[field]

    # Relative valuation from actual rent/sale yield peers, never from an opaque
    # model.  The label is intentionally concise for the screener.
    yields_by_category: dict[str, list[float]] = defaultdict(list)
    for item in out:
        if isinstance(item.get("y"), (int, float)):
            yields_by_category[item["i"]].append(item["y"])
    yield_medians = {key: _median(values) for key, values in yields_by_category.items()}
    for item in out:
        peer_yield = yield_medians.get(item["i"])
        if not peer_yield or not item.get("y"):
            item["v"] = None
        elif item["y"] >= peer_yield * 1.15:
            item["v"] = "cheap"
        elif item["y"] <= peer_yield * 0.85:
            item["v"] = "rich"
        else:
            item["v"] = "fair"

    # 34 province cards drive the map and expose no-data regions explicitly.
    province_rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        matches = [item for item in out if item["rs"] == province["name"]]
        cats: dict[str, Any] = {}
        for category_slug in CATEGORY_SLUGS.values():
            cells = [item for item in matches if item["cs"] == category_slug]
            if not cells:
                continue
            cats[category_slug] = {
                "p": round(_median([cell["p"] for cell in cells]) or 0, 1),
                "y": round(_median([cell.get("y") for cell in cells]) or 0, 2)
                    if any(cell.get("y") is not None for cell in cells) else None,
                "n": sum(cell["n"] for cell in cells),
                "d": len({(cell["r"], cell["d"]) for cell in cells}),
            }
        province_rows.append({
            "name": province["name"], "slug": province["slug"],
            "from": province["from"], "districts": len({(m["r"], m["d"]) for m in matches}),
            "rows": len(matches), "n": sum(m["n"] for m in matches), "cats": cats,
        })

    stats = {
        "rows": len(out),
        "districts": len({(o["r"], o["d"]) for o in out}),
        "with_yield": sum(1 for o in out if "y" in o),
        "min_samples": MIN_SAMPLES,
        # Sample count behind the whole file, so the page can say what the
        # medians rest on rather than only the per-cell n.
        "listings": sum(o.get("n", 0) for o in out),
        "raw_observations": int(raw_stats[0] or 0),
        "unique_listings": int(raw_stats[1] or 0),
        "snapshot_days": int(raw_stats[2] or 0),
        "legal_doc_rows": int(legal_stats[0] or 0),
        "legal_doc_pct": round(100 * (legal_stats[0] or 0) / max(legal_stats[1] or 1, 1), 1),
        "stale_after": STALE_AFTER_DAYS,
        "fresh_listings": int(stale_stats[0] or 0),
        "aged_out": int((stale_stats[1] or 0) - (stale_stats[0] or 0)),
        "aged_out_pct": round(
            100 * ((stale_stats[1] or 0) - (stale_stats[0] or 0))
            / max(stale_stats[1] or 1, 1), 1),
    }

    if not dry_run:
        from pipeline.publish.emit import write_json
        # Wrapped so the page can state how old the crawl is; a bare array
        # leaves a stale file looking identical to a fresh one.
        stats["path"] = str(write_json("bds.json", {
            "rows": out,
            "provinces": province_rows,
            "history": history,
            "listings": stats.get("listings"),
            "raw_observations": stats["raw_observations"],
            "unique_listings": stats["unique_listings"],
            "snapshot_days": stats["snapshot_days"],
            "legal_doc_rows": stats["legal_doc_rows"],
            "legal_doc_pct": stats["legal_doc_pct"],
            "stale_after": stats["stale_after"],
            "fresh_listings": stats["fresh_listings"],
            "aged_out": stats["aged_out"],
            "aged_out_pct": stats["aged_out_pct"],
            "history_from": raw_stats[3].isoformat() if raw_stats[3] else None,
            "history_to": raw_stats[4].isoformat() if raw_stats[4] else None,
            "cagr_min_days": CAGR_MIN_DAYS,
            "min_samples": MIN_SAMPLES,
                "source": {
                "name": "Chợ Tốt/Nhà Tốt",
                "kind": "Giá rao công khai",
                "url": "https://www.chotot.com/mua-ban-bat-dong-san",
            },
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
