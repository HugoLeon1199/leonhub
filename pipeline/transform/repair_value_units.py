"""One-off repair: normalise `eq_quote.value` to plain VND.

Two collectors write this column with different scales. The SSI board writes
plain VND; VCI writes millions of VND. `vn_equity.py` now converts on the way
in, but rows already in the warehouse were written before that fix and still
carry the millions convention, so a money-flow ranking run today puts every
HOSE/HNX/UPCOM row 1e6 too low and effectively hides them.

The warehouse is append-only, so this does not edit history in place. It writes
a corrected observation for each affected row, carrying a later `fetched_at`
so the standard "latest per key" read picks up the repaired figure while the
original stays on the record.

Detection is by measured ratio rather than by exchange spelling: a row is
treated as millions when its `value` is within an order of magnitude of
`price * volume / 1e6`. That survives a source relabelling its exchange field,
which the spelling test would not.

Run once:

    python -m pipeline.transform.repair_value_units --dry-run
    python -m pipeline.transform.repair_value_units
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from typing import Any

from pipeline.core import warehouse as wh

log = logging.getLogger(__name__)

# A row is "millions" when value * 1e6 lands near price * volume. Real turnover
# sits a little under price * volume (the close is not the session's average
# price), so the window is deliberately wide on the low side.
MILLIONS_LOW, MILLIONS_HIGH = 0.5, 2.0

SUSPECT_SQL = f"""
SELECT symbol, as_of, fetched_at, exchange, price, ref_price, open_price,
       high, low, volume, value, listed_share,
       foreign_buy_value, foreign_sell_value, foreign_buy_vol, foreign_sell_vol
FROM eq_quote
WHERE value IS NOT NULL AND value > 0
  AND price IS NOT NULL AND price > 0
  AND volume IS NOT NULL AND volume > 0
  AND (value * 1e6) / (price * volume) BETWEEN {MILLIONS_LOW} AND {MILLIONS_HIGH}
"""

# Rows already in plain VND, used only to report the split so a run that
# repairs nothing is distinguishable from a run that found nothing to repair.
ALREADY_VND_SQL = f"""
SELECT count(*) FROM eq_quote
WHERE value IS NOT NULL AND value > 0
  AND price IS NOT NULL AND price > 0
  AND volume IS NOT NULL AND volume > 0
  AND value / (price * volume) BETWEEN {MILLIONS_LOW} AND {MILLIONS_HIGH}
"""


def repair(dry_run: bool = False) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    started = wh.utcnow()

    con = wh.connect()
    try:
        columns = [c[0] for c in con.execute("DESCRIBE eq_quote").fetchall()]

        cur = con.execute(SUSPECT_SQL)
        names = [d[0] for d in cur.description]
        suspect = [dict(zip(names, row)) for row in cur.fetchall()]

        already = con.execute(ALREADY_VND_SQL).fetchone()[0]

        stats: dict[str, Any] = {
            "rows_in_millions": len(suspect),
            "rows_already_vnd": already,
        }
        if suspect:
            sample = suspect[0]
            stats["sample"] = {
                "symbol": sample["symbol"],
                "exchange": sample["exchange"],
                "value_before": sample["value"],
                "value_after": sample["value"] * 1e6,
                "price_x_volume": sample["price"] * sample["volume"],
            }

        if dry_run or not suspect:
            return stats

        corrected = []
        for row in suspect:
            fixed = {key: row.get(key) for key in columns}
            fixed["value"] = row["value"] * 1e6
            # A later instant is what makes the repaired row win the latest-per-
            # key read without deleting the original observation.
            fixed["fetched_at"] = started
            corrected.append(fixed)

        stats["rows_written"] = wh.append(con, "eq_quote", corrected)
        wh.log_run(
            con, run_id, "repair_value_units", started, "ok",
            rows_in=len(suspect), rows_new=stats["rows_written"], detail=stats,
        )
    finally:
        con.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(repair(args.dry_run), indent=2, default=str))


if __name__ == "__main__":
    main()
