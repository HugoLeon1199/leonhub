import tempfile
import unittest
from datetime import datetime, timezone, date
from pathlib import Path
from unittest.mock import patch

from pipeline.core import warehouse as wh
from pipeline.tools.vn_reference_restore import restore
from pipeline.sources.vps_board import to_quote
from pipeline.transform.stocks_build import build


class ReferenceRestoreTest(unittest.TestCase):
    def test_board_volume_is_shares(self):
        row = to_quote({"sym": "GVR", "lastPrice": 32.9, "lot": 344180},
                       date(2026, 9, 24), datetime.now(timezone.utc))
        self.assertEqual(row["volume"], 3441800)
        self.assertEqual(row["price"], 32900)

    def test_empty_warehouse_cannot_replace_published_stocks(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(wh, "DB_PATH", Path(folder)/"test.duckdb"), \
                 patch("pipeline.transform.stocks_build.read_json", return_value={"rows": [{"s": "GVR", "pe": 18}]}), \
                 patch("pipeline.transform.stocks_build.write_json") as write:
                wh.connect().close()
                with self.assertRaisesRegex(RuntimeError, "preserving published"):
                    build()
                write.assert_not_called()

    def test_empty_cache_recovery_is_dated_and_idempotent(self):
        payload = {"as_of": "2026-09-09", "updated_at": "2026-09-09T09:00:00+00:00",
                   "rows": [{"s": "GVR", "n": "Rubber", "e": "HOSE", "sh": 4000000000}]}
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(wh, "DB_PATH", Path(folder)/"test.duckdb"), \
                 patch("pipeline.tools.vn_reference_restore.read_json", return_value=payload):
                self.assertEqual(restore()["listings_restored"], 1)
                self.assertEqual(restore()["listings_restored"], 0)
                con = wh.connect_reader()
                try:
                    row = con.execute("SELECT price, listed_share, as_of FROM eq_quote").fetchone()
                    self.assertIsNone(row[0])  # never invent a current price
                    self.assertEqual(row[1], 4000000000)
                    self.assertEqual(str(row[2]), "2026-09-09")
                    self.assertEqual(con.execute("SELECT year(fetched_at) FROM eq_listing").fetchone()[0], 2026)
                finally:
                    con.close()


if __name__ == "__main__":
    unittest.main()
