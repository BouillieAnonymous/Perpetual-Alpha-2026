import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from perpetual_alpha.collection_quality import export, quality
from perpetual_alpha.stream_collection import ObservationStore, StreamJoin


def book(symbol="par:USDJPY", now=1000):
    return {"channel": "l2Book", "data": {"coin": symbol, "time": now,
            "levels": [[{"px": "149", "sz": "20"}], [{"px": "151", "sz": "20"}]]}}


def context(symbol="par:USDJPY", oracle="150"):
    return {"channel": "activeAssetCtx", "data": {"coin": symbol,
            "ctx": {"oraclePx": oracle, "markPx": "150", "funding": "0"}}}


class StreamTests(unittest.TestCase):
    def test_same_book_new_oracle_retained_and_restart_preserves_legacy(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "market.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE snapshots (raw_json TEXT)")
                db.execute("INSERT INTO snapshots VALUES ('original')")
            db.close()
            store, join = ObservationStore(path), StreamJoin(["par:USDJPY"])
            self.assertIsNone(join.feed(book(), 1000))
            for sequence, oracle in enumerate(("150", "152"), 1):
                msg = context(oracle=oracle)
                row = join.feed(msg, 1000 + sequence)
                store.save("one", sequence, 1000 + sequence, msg, row)
            store.close()
            store = ObservationStore(path)
            store.save("two", 1, 2000, {"channel": "connected", "data": {}})
            self.assertEqual(store.connection.execute("SELECT * FROM snapshots").fetchall(), [("original",)])
            values = store.connection.execute("SELECT raw_json FROM observations_v2").fetchall()
            self.assertEqual([json.loads(v[0])["oracle"] for v in values], [150, 152])
            store.close()
            rows, report = export(path)
            self.assertEqual(report["integrity"], "ok")
            self.assertEqual(report["assets"]["par:USDJPY"]["same_book_event_observations"], 1)
            self.assertEqual(len(rows), 2)

    def test_reconnect_requires_both_fresh_streams(self):
        join = StreamJoin(["par:USDJPY"])
        join.feed(context(), 1000)
        self.assertIsNotNone(join.feed(book(), 1001))
        # Every network reconnection creates a fresh StreamJoin.
        join = StreamJoin(["par:USDJPY"])
        self.assertIsNone(join.feed(book(now=2000), 2001))
        self.assertIsNotNone(join.feed(context(), 2002))

    def test_skew_stale_invalid_depth_and_out_of_order(self):
        join = StreamJoin(["par:USDJPY"], max_age_ms=10000, max_skew_ms=100)
        join.feed(book(), 1000)
        row = join.feed(context(), 1200)
        self.assertEqual(row["quality"], "invalid")
        self.assertIn("skew", row["quality_reason"])
        row = join.feed(context(), 12000)
        self.assertEqual(row["quality"], "invalid")
        with self.assertRaises(ValueError):
            join.feed(book(now=900), 12001)
        bad = book(now=12001)
        bad["data"]["levels"][0][0]["sz"] = "NaN"
        self.assertEqual(join.feed(bad, 12002)["quality"], "invalid")

    def test_all_ten_market_configuration_and_missing_coverage(self):
        from perpetual_alpha.historical.contracts import PRICE_UNITS
        join = StreamJoin(list(PRICE_UNITS))
        for symbol in PRICE_UNITS:
            join.feed(book(symbol), 1000)
            self.assertEqual(join.feed(context(symbol), 1001)["symbol"], symbol)
        self.assertEqual(len(quality([])), 10)
        self.assertIsNone(quality([])["par:HYPE"]["grid_5s_coverage"])
        with self.assertRaises(ValueError):
            StreamJoin(["BTC"])
        with self.assertRaises(ValueError):
            StreamJoin(["par:BTC", "par:BTC"])

    def test_atomic_duplicate_identity_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ObservationStore(str(Path(folder) / "test.sqlite3"))
            store.save("session", 1, 1000, book())
            with self.assertRaises(sqlite3.IntegrityError):
                store.save("session", 1, 1000, context())
            self.assertEqual(store.connection.execute("SELECT count(*) FROM collection_events_v2").fetchone()[0], 1)
            store.close()

    def test_network_loop_reconnect_resubscribes_without_cached_context(self):
        from unittest.mock import patch

        from perpetual_alpha.stream_collection import capture

        class Socket:
            def __init__(self, messages):
                self.messages, self.sent = iter(messages), []
            def send(self, value):
                self.sent.append(json.loads(value))
            def recv(self):
                return json.dumps(next(self.messages, {})) if self.messages else ""
            def close(self):
                pass

        # First connection has only context; second only book. No joined observation may leak across it.
        first, second = Socket([context()]), Socket([book(now=100000)])
        ticks = iter(range(1000))
        with tempfile.TemporaryDirectory() as folder:
            database = str(Path(folder) / "loop.sqlite3")
            payload = {"universe": [{"name": "par:USDJPY", "szDecimals": 2, "maxLeverage": 50}]}
            with patch("perpetual_alpha.stream_collection.PublicHistoryClient") as client, \
                 patch("websocket.create_connection", side_effect=[first, second, OSError()]), \
                 patch("perpetual_alpha.stream_collection.time.monotonic", side_effect=lambda: next(ticks)), \
                 patch("perpetual_alpha.stream_collection.time.sleep"), \
                 patch("perpetual_alpha.stream_collection.signal.signal"):
                client.return_value.metadata.return_value = payload, 100000
                # Unknown messages then deadline eventually closes; first connection needs explicit closure.
                first.recv = lambda: json.dumps(context()) if not getattr(first, "done", False) else ""
                original = first.recv
                def receive_first():
                    answer = original()
                    first.done = True
                    return answer
                first.recv = receive_first
                second.recv = lambda: json.dumps(book(now=100000)) if not getattr(second, "done", False) else ""
                original_second = second.recv
                def receive_second():
                    answer = original_second()
                    second.done = True
                    return answer
                second.recv = receive_second
                capture(database, ["par:USDJPY"], seconds=25)
            rows, report = export(database)
            self.assertEqual(rows, [])
            self.assertEqual(len(first.sent), 2)
            self.assertEqual(len(second.sent), 2)
            self.assertGreaterEqual(report["channels"]["disconnected"], 2)

    def test_archived_public_quality_is_reproducible(self):
        import gzip
        root = Path(__file__).resolve().parents[1] / "docs/research/collector-2026-10-18"
        rows = [json.loads(line) for line in gzip.decompress((root / "observations.jsonl.gz").read_bytes()).decode().splitlines()]
        expected = json.loads((root / "quality.json").read_text())
        self.assertEqual(json.loads(json.dumps(quality(rows))), expected["assets"])
