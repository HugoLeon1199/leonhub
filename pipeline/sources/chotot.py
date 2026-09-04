"""Chotot real-estate collector — the moat.

Listing history cannot be bought or backfilled, so this runs from day one and
its output accumulates in the warehouse regardless of whether any UI exists yet.

Three source behaviours discovered by probing the API, each of which silently
corrupts the numbers if ignored:

1. Listing type is NOT filtered by default. A region query mixes sale and rental
   ads, and a rental price/m2 is ~1000x smaller than a sale one (Quan 1 returned
   0.24 vs 208 million/m2 in the same response). Every query therefore pins
   `st` explicitly -- `s` for sale, `u` for rent.
2. Deep pagination dies: offset 20k-30k starts returning HTTP 400. Crawling a
   whole province is therefore impossible; we crawl district by district, where
   `total` is a real count (1,662 for Quan 1) rather than the rounded 10,000
   ceiling the province-level query reports.
3. `total` at province level is a capped placeholder, so it must never be used
   as a completion target.
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator

from pipeline.core.http import HttpClient
from pipeline.core import warehouse as wh

log = logging.getLogger(__name__)

API = "https://gateway.chotot.com/v1/public/ad-listing"
PAGE_SIZE = 50
# Offsets beyond this return HTTP 400 (verified by probe).
MAX_OFFSET = 19_000

# Property categories, verified against live responses.
CATEGORIES = {
    1010: "Căn hộ/Chung cư",
    1020: "Nhà ở",
    1030: "Văn phòng, Mặt bằng kinh doanh",
    1040: "Đất",
}

# Listing types. Sale drives price/m2; rent drives rental-yield estimates.
SALE = "s"
RENT = "u"


def _to_utc(ms: int | None) -> datetime | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def fetch_page(
    client: HttpClient,
    region: int,
    area: int | None,
    category: int,
    listing_type: str,
    offset: int,
    limit: int = PAGE_SIZE,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch one page. Returns (ads, reported_total)."""
    params: dict[str, Any] = {
        "cg": category,
        "st": listing_type,
        "limit": limit,
        "o": offset,
    }
    # region 0 means "no region filter" (national feed). Sending region_v2=0
    # would be treated as an unknown code and silently resolved to the default
    # region, so the parameter is omitted entirely instead.
    if region:
        params["region_v2"] = region
    if area is not None:
        params["area_v2"] = area

    payload = client.get_json(API, params=params)
    return payload.get("ads") or [], int(payload.get("total") or 0)


def iter_listings(
    client: HttpClient,
    region: int,
    area: int | None,
    category: int,
    listing_type: str,
    max_pages: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Page through one district x category x type until exhausted."""
    offset = 0
    pages = 0
    while offset <= MAX_OFFSET:
        if max_pages is not None and pages >= max_pages:
            return
        ads, _ = fetch_page(client, region, area, category, listing_type, offset)
        if not ads:
            return
        yield from ads
        pages += 1
        offset += len(ads)
        if len(ads) < PAGE_SIZE:
            return


def to_row(ad: dict[str, Any], listing_type: str, fetched_at: datetime) -> dict[str, Any]:
    """Normalize one ad into an eq-style observation row.

    price_per_m2 is recomputed rather than trusted: the API field is absent on
    some ads and is meaningless for rentals, which we keep in a separate lane.
    """
    price = ad.get("price")
    size = ad.get("size")
    ppm2 = ad.get("price_million_per_m2")
    if ppm2 is None and price and size:
        ppm2 = (price / 1e6) / size

    return {
        "list_id": ad.get("list_id"),
        "source": f"chotot:{listing_type}",
        "as_of": _to_utc(ad.get("list_time")) or fetched_at,
        "fetched_at": fetched_at,
        "region": ad.get("region_name"),
        "district": ad.get("area_name"),
        "ward": ad.get("ward_name"),
        "category": ad.get("category_name"),
        "house_type": ad.get("house_type"),
        "price": price,
        "size_m2": size,
        "price_per_m2": ppm2,
        "rooms": ad.get("rooms"),
        "latitude": ad.get("latitude"),
        "longitude": ad.get("longitude"),
        "street_name": ad.get("street_name"),
        "subject": ad.get("subject"),
        # Raw code, deliberately untranslated -- see the note on the column in
        # warehouse.py. Collect it now because listing history cannot be
        # backfilled: a field not captured today is gone for today forever.
        "legal_doc": ad.get("property_legal_document"),
    }


def discover_regions(client: HttpClient, pages: int = 40) -> dict[int, str]:
    """Learn province codes by reading the unfiltered national feed.

    Chotot publishes no region tree, and the codes are not derivable: they are
    zone-prefixed and irregular (1002 Phu Tho, 5027 Can Tho, 12000 Ha Noi,
    13000 HCMC). Probing a numeric range is worse than useless because an
    unknown `region_v2` is silently ignored rather than rejected -- the API
    answers with the default region, so a probe "succeeds" for codes that do
    not exist. Harvesting the codes from ads is the only reliable route.
    """
    regions: dict[int, str] = {}
    for category in (1010, 1020, 1040):
        offset = 0
        for _ in range(pages):
            ads, _ = fetch_page(client, 0, None, category, SALE, offset, limit=PAGE_SIZE)
            if not ads:
                break
            for ad in ads:
                code = ad.get("region_v2")
                if isinstance(code, int):
                    regions.setdefault(code, ad.get("region_name") or "")
            offset += len(ads)
    log.info("discovered %d provinces", len(regions))
    return regions


def discover_districts(client: HttpClient, region: int) -> list[int]:
    """Find district codes for a province by sampling ads across categories.

    Chotot exposes no public region-tree endpoint (the documented one 404s), so
    district codes are learned from the ads themselves and cached in the
    warehouse over time.

    Ads carry two district codes: a short `area` (113) and the full `area_v2`
    (13113). Only the latter is accepted by the `area_v2` query parameter --
    passing the short form returns an empty result set rather than an error.
    """
    seen: dict[int, str] = {}
    for category in CATEGORIES:
        for listing_type in (SALE, RENT):
            ads, _ = fetch_page(client, region, None, category, listing_type, 0, limit=50)
            for ad in ads:
                code = ad.get("area_v2")
                if isinstance(code, int):
                    seen.setdefault(code, ad.get("area_name") or "")
    log.info("region %s: discovered %d districts", region, len(seen))
    return sorted(seen)


def collect(
    regions: list[int],
    dry_run: bool = False,
    max_pages: int | None = None,
    delay: float = 1.0,
) -> dict[str, Any]:
    client = HttpClient(delay=delay)
    run_id = uuid.uuid4().hex[:12]
    started = wh.utcnow()
    fetched_at = started
    stats: dict[str, Any] = {"regions": {}, "sale": 0, "rent": 0}

    def crawl_region(region: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
        districts = discover_districts(client, region) or [None]
        out: list[dict[str, Any]] = []
        counts = {"sale": 0, "rent": 0}
        for area in districts:
            for category in CATEGORIES:
                for listing_type in (SALE, RENT):
                    for ad in iter_listings(
                        client, region, area, category, listing_type, max_pages
                    ):
                        out.append(to_row(ad, listing_type, fetched_at))
                        counts["sale" if listing_type == SALE else "rent"] += 1
        return out, counts

    def add_counts(counts: dict[str, int]) -> None:
        # Merge only after a province completed. If it failed halfway through,
        # none of its buffered rows were committed, so counting those rows in
        # the run report would claim observations the warehouse does not hold.
        stats["sale"] += counts["sale"]
        stats["rent"] += counts["rent"]

    # A dry run reports on the whole crawl at once, so it is the one path that
    # still accumulates. It writes nothing, so there is nothing to lose.
    if dry_run:
        rows: list[dict[str, Any]] = []
        for region in regions:
            region_rows, counts = crawl_region(region)
            rows.extend(region_rows)
            add_counts(counts)
            stats["regions"][str(region)] = len(region_rows)
            log.info("region %s: %d observations", region, len(region_rows))

        stats["rows_in"] = len(rows)
        by_district: dict[str, int] = {}
        for r in rows:
            key = f"{r['region']} / {r['district']}"
            by_district[key] = by_district.get(key, 0) + 1
        unique_ids = len({r["list_id"] for r in rows})
        stats["unique_list_ids"] = unique_ids
        stats["duplicate_rate"] = (
            round(1 - unique_ids / len(rows), 4) if rows else 0.0
        )
        stats["by_district"] = dict(sorted(by_district.items(), key=lambda kv: -kv[1])[:20])
        return stats

    # Real estate history cannot be backfilled: a listing that is gone tomorrow
    # is gone for good. So each province is flushed as soon as it finishes and
    # its failure is contained, rather than holding a whole national crawl in
    # memory and writing once at the end -- where one bad province, or the job
    # timeout on a crawl whose length is unbounded, discards every province
    # already paid for.
    # The write handle is taken per flush, not held across the crawl. A national
    # pass runs for hours, and DuckDB allows one writer -- holding it throughout
    # would park every other collector well past connect()'s 300s wait. Opening
    # per province costs milliseconds against a province that costs minutes.
    total_in = 0
    total_new = 0
    failures = 0

    for region in regions:
        try:
            region_rows, counts = crawl_region(region)
        except Exception as exc:  # noqa: BLE001 - one province must not end the crawl
            failures += 1
            stats["regions"][str(region)] = {"error": str(exc)[:200]}
            log.warning("region %s failed, continuing: %s", region, exc)
            continue

        con = wh.connect()
        try:
            inserted = wh.append(con, "re_listing", region_rows)
        finally:
            con.close()

        total_in += len(region_rows)
        total_new += inserted
        add_counts(counts)
        stats["regions"][str(region)] = {"rows": len(region_rows), "new": inserted}
        log.info(
            "region %s: %d observations, %d new (committed)",
            region, len(region_rows), inserted,
        )

    stats["rows_in"] = total_in
    stats["rows_new"] = total_new
    stats["regions_failed"] = failures

    con = wh.connect()
    try:
        wh.log_run(
            con, run_id, "chotot", started,
            "partial" if failures else "ok",
            rows_in=total_in, rows_new=total_new, detail=stats,
        )
    finally:
        con.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Chotot real-estate listings")
    parser.add_argument(
        "--regions", default="13000",
        help="Comma-separated region_v2 codes (13000=HCMC, 12000=Hanoi)",
    )
    parser.add_argument(
        "--discover-regions", action="store_true",
        help="Print the province code map harvested from the national feed and exit",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report counts, write nothing")
    parser.add_argument("--max-pages", type=int, help="Cap pages per district/category/type")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between requests")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.discover_regions:
        found = discover_regions(HttpClient(delay=args.delay))
        print(json.dumps({str(k): v for k, v in sorted(found.items())},
                         ensure_ascii=False, indent=2))
        return

    regions = [int(r) for r in args.regions.split(",") if r.strip()]
    stats = collect(regions, args.dry_run, args.max_pages, args.delay)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
