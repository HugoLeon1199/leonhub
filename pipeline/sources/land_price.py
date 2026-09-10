"""State land-price tables (Bảng giá đất), by province.

Every province issues one, it is the official price floor the state uses for
land-use fees and compensation, and it is the only published price in Vietnamese
real estate that is not an asking price. Set beside the asking-price medians this
repo already collects, it is the one external anchor available.

**Why this reads HTML rather than the gazette PDFs.** The primary documents are
reachable -- `congbao.hochiminhcity.gov.vn` serves the full 78-page decision --
but they are scans with an OCR text layer, and the OCR damages exactly the field
that has to join: Vietnamese street names come back as `CAO BẢ QUÁT`, `NGUYỀN`,
`CHU MẠN1Ỉ TRINH`. Measured against this warehouse's own `street_name` values,
the 2025 gazette PDF matched 53% and the 2026 one 19% (its table merges the
from/to columns into the name, so it needs column-boundary parsing). The HTML
mirror carries the same tables with diacritics intact and matched **60%**, with
no OCR, no overprint de-duplication, and no LLM repair step. The better source
won on measurement, not on preference.

**What that costs, and how it is handled.** This is a secondary source and it
lags: as of 2026-09-10 its HCMC page still cites `79/2024/QĐ-UBND`, the 2025
table, while `87/2025/NQ-HĐND` has been in force since January. So the document
number the page declares is stored verbatim in `source_doc` and never
overwritten with what we think the current decision is -- `validate` compares it
against the primary citation in `province_profiles.json` and warns on the gap.
A reader has to be able to see that the anchor is a year behind; hiding it would
make the comparison look more authoritative than it is.

Aggregates only. The tables are republished as district-level summaries, never
copied wholesale -- the same rule the listing publisher follows.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import unicodedata
import uuid
from typing import Any, Iterator

from pipeline.core.http import HttpClient
from pipeline.core import warehouse as wh

log = logging.getLogger(__name__)

BASE = "https://thuviennhadat.vn/bang-gia-dat"

# Post-2025 province slugs, matching PROVINCES in bds_aggregate. `hue` is absent
# deliberately: it is the one province whose page returns an empty table (4 rows,
# no prices), verified 2026-09-10. Listing it and collecting nothing would look
# like a collector fault rather than a source gap.
PROVINCE_SLUGS = (
    "ha-noi", "ho-chi-minh", "hai-phong", "da-nang", "can-tho",
    "quang-ninh", "bac-ninh", "hung-yen", "ninh-binh", "phu-tho",
    "thai-nguyen", "lao-cai", "tuyen-quang", "cao-bang", "lang-son",
    "dien-bien", "lai-chau", "son-la", "thanh-hoa", "nghe-an",
    "ha-tinh", "quang-tri", "quang-ngai", "gia-lai", "dak-lak",
    "khanh-hoa", "lam-dong", "dong-nai", "tay-ninh", "vinh-long",
    "dong-thap", "an-giang", "ca-mau",
)

# Pages hold 200 table rows, which is 100 distinct streets -- the source renders
# each table twice. HCMC runs past page 100 and is empty by 140, so a large city
# is ~10,000 street segments and ~120 requests. At the default delay that is a
# couple of minutes per big province and seconds for a small one; the whole
# country is a single unhurried run, not a crawl budget worth optimising.
MAX_PAGES = 150

# VND per m2. Below the floor the row is a footnote or a parse slip; above the
# ceiling the unit changed. State prices sit far under market -- HCMC's highest
# is ~687 million/m2 -- so the ceiling is generous rather than tight.
PRICE_MIN = 10_000.0
PRICE_MAX = 2_000_000_000.0

_TAG = re.compile(r"<[^>]+>")
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
# Document numbers as the gazette writes them, either authority.
_DOC = re.compile(r"\d{1,4}/\d{4}/(?:QĐ-UBND|NQ-HĐND)")


def _text(fragment: str) -> str:
    """Tag-stripped, entity-decoded, whitespace-collapsed cell text."""
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", fragment))).strip()


def _price(cell: str) -> float | None:
    """First VND figure in a cell, or None.

    A cell often holds two numbers stacked -- the price and a second tier -- so
    only the leading one is taken. Vietnamese thousands separators are dots, and
    a bare `250` is a footnote marker rather than a price, so a value has to
    carry at least one group separator to count.
    """
    match = re.search(r"\d{1,3}(?:\.\d{3})+", cell)
    if not match:
        return None
    try:
        value = float(match.group(0).replace(".", ""))
    except ValueError:
        return None
    return value if PRICE_MIN <= value <= PRICE_MAX else None


def normalize_street(name: str) -> str:
    """Diacritic-free, lowercased street name for joining against listings.

    The warehouse stores what posters typed -- "Đường Nguyễn Cư Trinh", "Trần
    Quang Khải, Phường Tân Định. Quận 1." -- so the join key drops diacritics,
    punctuation and the leading road-type word rather than trying to match the
    strings as written.
    """
    folded = unicodedata.normalize("NFD", name.lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    folded = re.sub(r"\b(duong|pho)\b", " ", folded)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", folded)).strip()


def parse_page(markup: str) -> tuple[list[dict[str, Any]], str | None]:
    """Rows and the document number the page declares for them.

    A row is `STT | district | street | segment | residential | commercial`.
    The document number is read from the page rather than assumed: it is the
    only evidence of which table these prices actually belong to.
    """
    doc_match = _DOC.search(_text(markup))
    doc = doc_match.group(0) if doc_match else None

    rows: list[dict[str, Any]] = []
    for fragment in _ROW.findall(markup):
        cells = [_text(c) for c in _CELL.findall(fragment)]
        if len(cells) < 5 or not cells[0].isdigit():
            continue
        street = cells[2]
        if not street:
            continue
        residential = _price(cells[4])
        if residential is None:
            continue
        rows.append({
            "district": cells[1] or None,
            "street": street,
            "street_key": normalize_street(street),
            # Part of the primary key, so it cannot be NULL. A blank cell
            # means the price covers the whole street, which is what the source
            # writes as "TRỌN ĐƯỜNG" elsewhere -- same meaning, stated.
            "segment": cells[3] or "TRỌN ĐƯỜNG",
            "price_residential": residential,
            "price_commercial": _price(cells[5]) if len(cells) > 5 else None,
        })
    return rows, doc


def iter_province(
    client: HttpClient, slug: str, max_pages: int = MAX_PAGES
) -> Iterator[tuple[dict[str, Any], str | None, int]]:
    """Page through one province until a page yields no rows."""
    seen: set[tuple[str, str | None]] = set()
    for page in range(1, max_pages + 1):
        url = BASE + f"/{slug}" + ("" if page == 1 else f"?trang={page}")
        markup = client.get_text(url)
        rows, doc = parse_page(markup)
        if not rows:
            return
        fresh = 0
        for row in rows:
            key = (row["street_key"], row["segment"])
            if key in seen:
                continue
            seen.add(key)
            fresh += 1
            yield row, doc, page

        # Deliberately no early exit on barren pages. The source's pagination is
        # not monotonic: HCMC repeats page 2 across pages 3-5, resumes real rows
        # on page 6, repeats again on 12-16, and still has new streets at pages
        # 40 and 100. Stopping after one barren page yielded 165 streets and
        # after five yielded 698, against ~10,000 that exist. The only reliable
        # terminator is a page with no parseable rows at all, handled above.
        _ = fresh


def collect(
    slugs: list[str],
    dry_run: bool = False,
    delay: float = 1.0,
    max_pages: int = MAX_PAGES,
) -> dict[str, Any]:
    client = HttpClient(delay=delay)
    run_id = uuid.uuid4().hex[:12]
    started = wh.utcnow()
    stats: dict[str, Any] = {"provinces": {}, "rows": 0, "failed": {}}
    buffered: list[dict[str, Any]] = []

    for slug in slugs:
        rows: list[dict[str, Any]] = []
        doc_seen: str | None = None
        try:
            for row, doc, page in iter_province(client, slug, max_pages):
                doc_seen = doc_seen or doc
                rows.append({
                    **row,
                    "province": slug,
                    "source_doc": doc,
                    "source_url": BASE + f"/{slug}",
                    "page": page,
                    "fetched_at": started,
                })
        except Exception as exc:  # noqa: BLE001 -- one province must not stop the rest
            log.warning("province %s failed: %s", slug, exc)
            stats["failed"][slug] = f"{type(exc).__name__}: {exc}"
            continue

        stats["provinces"][slug] = {"rows": len(rows), "doc": doc_seen}
        stats["rows"] += len(rows)
        log.info("province %s: %d rows, document %s", slug, len(rows), doc_seen)

        if dry_run:
            buffered.extend(rows)
            continue
        if rows:
            con = wh.connect()
            try:
                inserted = wh.append(con, "re_land_price", rows)
                stats["provinces"][slug]["inserted"] = inserted
            finally:
                con.close()

    if dry_run:
        stats["sample"] = buffered[:3]
        return stats

    con = wh.connect()
    try:
        wh.log_run(
            con,
            run_id=run_id,
            collector="land_price",
            started_at=started,
            status="partial" if stats["failed"] else "ok",
            rows_in=stats["rows"],
            rows_new=sum(
                p.get("inserted", 0) for p in stats["provinces"].values()
            ),
            detail={"provinces": stats["provinces"], "failed": stats["failed"]},
        )
    finally:
        con.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect state land-price tables by province"
    )
    parser.add_argument("--province", help="One province slug")
    parser.add_argument("--all", action="store_true", help="Every province")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.province:
        slugs = [args.province]
    elif args.all:
        slugs = list(PROVINCE_SLUGS)
    else:
        parser.error("pass --province <slug> or --all")

    stats = collect(slugs, args.dry_run, args.delay, args.max_pages)
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
