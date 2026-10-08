import tempfile
import unittest
from pathlib import Path

from perpetual_alpha.market_data import normalize
from perpetual_alpha.storage import Store


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.context = {"markPx": "100", "oraclePx": "100", "funding": "-0.00001"}
        self.book = {"coin": "par:BTC", "time": 100000,
                     "levels": [[{"px": "99"}], [{"px": "101"}]]}

    def test_stale_wrong_market_and_crossed_books_rejected(self):
        with self.assertRaises(ValueError):
            normalize("par:BTC", self.context, self.book, 200000, 90)
        with self.assertRaises(ValueError):
            normalize("par:ETH", self.context, self.book, 100001, 90)
        self.book["levels"][0][0]["px"] = "102"
        with self.assertRaises(ValueError):
            normalize("par:BTC", self.context, self.book, 100001, 90)

    def test_duplicate_and_restart_preserve_original_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.sqlite3")
            row = normalize("par:BTC", self.context, self.book, 100001, 90)
            store = Store(path)
            store.save(row)
            row["bid"] = 98
            store.save(row)
            store.heartbeat(success=True)
            successful = store.connection.execute("SELECT last_success_ms FROM runtime").fetchone()[0]
            store.heartbeat(error="TimeoutError")
            store.close()
            store = Store(path)
            self.assertEqual(store.connection.execute("SELECT count(*),bid FROM snapshots").fetchone(), (1, 99))
            self.assertEqual(store.connection.execute("SELECT last_success_ms,error FROM runtime").fetchone(),
                             (successful, "TimeoutError"))
            store.close()


if __name__ == "__main__":
    unittest.main()
