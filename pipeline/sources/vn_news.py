"""Collect VN financial newswire RSS and link the headlines to tickers.

``news_link`` reads the sister repo's digest, which is a general news product --
science, world markets, consumer tech, sport. It links honestly and therefore
links almost nothing: eleven stories across six tickers out of 1,719, with VNM,
FPT and HPG carrying none. That is not a matching bug. A feed that rarely writes
about Vietnamese listed companies cannot be made to yield stories about them,
and loosening the matcher against it would attach unrelated articles, which is
the one failure that collector exists to prevent.

So this reads sources that do write about listed companies. CafeF and VnEconomy
publish public RSS; a single pass over 153 headlines matched 26 distinct
tickers, against 11 links in the whole existing artifact.

The matching itself is imported from ``news_link`` rather than reimplemented.
Its rules -- headline-only, curated aliases, a blocklist for three-letter tokens
that are also words, and an explicit "mã"/"cổ phiếu" requirement before a bare
code counts -- were derived from QA against real false positives, and a second
copy would drift away from them.

    python -m pipeline.sources.vn_news --dry-run --qa-sample 20
"""

from __future__ import annotations

import argparse
import logging
import random
import re
import sys
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

from pipeline.core import warehouse as wh
from pipeline.core.http import HttpClient
from pipeline.sources.news_link import link_articles

log = logging.getLogger(__name__)

# Public RSS, no key and no scraping. Vietstock and TinnhanhChungKhoan were
# checked and both 404 their advertised feed paths, so they are left out rather
# than retried on every run.
FEEDS: tuple[tuple[str, str], ...] = (
    ("cafef_vn", "https://cafef.vn/thi-truong-chung-khoan.rss"),
    ("cafef_vn", "https://cafef.vn/doanh-nghiep.rss"),
    ("vneconomy_vn", "https://vneconomy.vn/chung-khoan.rss"),
)

# Feeds lead with a channel-level <title>; only <item> children are articles.
ITEM_TAG = "item"


def _text(node: Any, tag: str) -> str:
    found = node.find(tag)
    if found is None:
        return ""
    return "".join(found.itertext()).strip()


def _published(raw: str) -> datetime | None:
    """RSS dates are RFC 822. Return UTC, or None when the feed omits it."""
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_feed(xml: str, source: str) -> list[dict[str, Any]]:
    """One RSS document into the article shape ``link_articles`` expects."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        log.warning("%s: unparseable RSS (%s)", source, exc)
        return []

    articles: list[dict[str, Any]] = []
    for item in root.iter(ITEM_TAG):
        title = _text(item, "title")
        url = _text(item, "link")
        if not title or not url:
            continue
        # CafeF puts a thumbnail and a teaser in the description. The teaser is
        # real prose about the story, so it is kept as body text for candidate
        # finding -- the headline still decides, inside link_articles.
        description = re.sub(r"<[^>]+>", " ", _text(item, "description"))
        articles.append({
            "title": title,
            "url": url,
            "source": source,
            "summary": description.strip(),
            "published_at": _published(_text(item, "pubDate")),
        })
    return articles


def fetch_articles(client: HttpClient) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    failures = 0
    for source, url in FEEDS:
        try:
            xml = client.get_text(url)
        except Exception as exc:  # network, TLS, HTTP status
            failures += 1
            log.warning("%s: fetch failed (%s)", url, exc)
            continue
        found = parse_feed(xml, source)
        log.info("%s: %d items", url, len(found))
        articles.extend(found)
    if failures == len(FEEDS):
        raise RuntimeError("Every RSS feed failed; refusing to report an empty run as success")
    # One story syndicated across two of a publisher's sections is one story.
    unique = {a["url"]: a for a in articles}
    return list(unique.values())


def collect(dry_run: bool = False, qa_sample: int = 20) -> dict[str, Any]:
    started = wh.utcnow()
    client = HttpClient(delay=0.5)
    articles = fetch_articles(client)

    con = wh.connect_reader() if dry_run else wh.connect()
    try:
        listings = con.execute(
            """SELECT DISTINCT ON (symbol) symbol, organ_name FROM eq_listing
               ORDER BY symbol, fetched_at DESC"""
        ).fetchall()
        rows = link_articles(articles, listings, started)
        rows = list({(r["symbol"], r["url"]): r for r in rows}.values())

        if not articles:
            raise RuntimeError("RSS feeds returned no articles")
        # Unlike news_link this does NOT fail on zero links. A quiet news day
        # genuinely produces none, and these feeds are read several times a day;
        # raising here would turn an ordinary afternoon into a red build.
        stats: dict[str, Any] = {
            "articles": len(articles),
            "feeds": len(FEEDS),
            "listings": len(listings),
            "links": len(rows),
            "symbols": len({r["symbol"] for r in rows}),
            "by_method": {
                m: sum(r["matched_by"] == m for r in rows)
                for m in ("name", "alias", "symbol")
            },
            "by_source": {
                s: sum(1 for a in articles if a["source"] == s)
                for s in {a["source"] for a in articles}
            },
        }
        sample = random.Random(42).sample(rows, min(max(qa_sample, 0), len(rows)))
        stats["qa_sample"] = [
            {"s": r["symbol"], "m": r["matched_by"], "t": r["title"], "u": r["url"]}
            for r in sample
        ]
        if dry_run:
            return stats

        # Same table as news_link: both produce the identical row shape, and the
        # transform reads one stream. `source` is what tells them apart.
        new_rows = wh.append(con, "news_link", rows)
        stats["rows_new"] = new_rows
        wh.log_run(
            con, uuid.uuid4().hex[:12], "vn_news", started, "ok",
            rows_in=len(articles), rows_new=new_rows,
            detail={k: v for k, v in stats.items() if k != "qa_sample"},
        )
        return stats
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Link VN financial RSS to VN tickers")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--qa-sample", type=int, default=20)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import json
    print(json.dumps(collect(args.dry_run, args.qa_sample), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
