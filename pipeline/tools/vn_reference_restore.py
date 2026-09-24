"""Recover missing VN reference rows from our dated, published snapshot.

GitHub caches can be evicted. A board fallback supplies prices but no company
directory, so an empty listing table made the daily build erase every symbol.
This is disaster recovery of already published observations, not a new source
observation. Keep the original publication timestamp and leave existing rows.
"""
from __future__ import annotations

import json
from datetime import datetime, date

from pipeline.core import warehouse as wh
from pipeline.publish.emit import read_json


def restore() -> dict:
    payload = read_json("stocks.json") or {}
    if not payload.get("rows"):
        raise RuntimeError("No published stock reference available for recovery")
    fetched = datetime.fromisoformat(payload["updated_at"])
    if fetched.tzinfo is None:
        raise ValueError("Published reference must have an explicit timezone")
    session = date.fromisoformat(payload["as_of"])
    con = wh.connect()
    try:
        existing = {r[0] for r in con.execute("SELECT DISTINCT symbol FROM eq_listing").fetchall()}
        have_shares = {r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM eq_quote WHERE listed_share > 0"
        ).fetchall()}
        listings, shares = [], []
        for row in payload["rows"]:
            symbol = row["s"]
            if symbol not in existing:
                listings.append(dict(symbol=symbol, organ_name=row.get("n"),
                                     exchange=row.get("e"), industry=row.get("i"), fetched_at=fetched))
            if symbol not in have_shares and row.get("sh", 0) > 0:
                shares.append(dict(symbol=symbol, as_of=session, fetched_at=fetched,
                                   listed_share=row["sh"]))
        return {"reference_timestamp": fetched.isoformat(),
                "listings_restored": wh.append(con, "eq_listing", listings),
                "share_counts_restored": wh.append(con, "eq_quote", shares)}
    finally:
        con.close()


if __name__ == "__main__":
    print(json.dumps(restore(), indent=2))
