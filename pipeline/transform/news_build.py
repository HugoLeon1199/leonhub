"""Build compact per-ticker news links from the append-only warehouse."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from typing import Any

from pipeline.core import warehouse as wh
from pipeline.publish.emit import write_json

MAX_PER_SYMBOL = 10

# Days of published history to keep. Two collectors now write news_link --
# news_link itself from the sister repo's digest, vn_news from the VN financial
# wires -- and they run on different schedules. The original query took only the
# single newest fetched_at batch, which was right when one collector owned the
# table and silently discards the other one's stories now that two do. Window on
# how recent the STORY is instead of which run last touched the table.
NEWS_WINDOW_DAYS = 30

NEWS_SQL = """
WITH current_snapshot AS (
    SELECT * FROM news_link
    WHERE published_at IS NULL
       OR published_at >= now() - INTERVAL (?) DAY
), latest AS (
    SELECT DISTINCT ON (symbol, url)
        symbol, url, title, source, published_at, matched_by, fetched_at
    FROM current_snapshot
    ORDER BY symbol, url, fetched_at DESC
), ranked AS (
    SELECT *, row_number() OVER (
        PARTITION BY symbol ORDER BY published_at DESC NULLS LAST, fetched_at DESC
    ) AS rank
    FROM latest
)
SELECT symbol, url, title, source, published_at, matched_by
FROM ranked
WHERE rank <= ?
ORDER BY symbol, rank
"""


def build(dry_run: bool = False) -> dict[str, Any]:
    con = wh.connect_reader()
    try:
        rows = con.execute(NEWS_SQL, [NEWS_WINDOW_DAYS, MAX_PER_SYMBOL]).fetchall()
    finally:
        con.close()

    tickers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for symbol, url, title, source, published_at, matched_by in rows:
        item = {"t": title, "u": url, "m": matched_by}
        if source:
            item["src"] = source
        if published_at:
            item["d"] = published_at.date().isoformat()
        tickers[symbol].append(item)

    stats: dict[str, Any] = {
        "symbols": len(tickers), "links": sum(map(len, tickers.values())),
        "max_per_symbol": MAX_PER_SYMBOL,
    }
    if not dry_run:
        path = write_json("news_ticker.json", {"tickers": dict(tickers)})
        stats.update(path=str(path), size_kb=round(path.stat().st_size / 1024, 1))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build data/news_ticker.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.dry_run), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
