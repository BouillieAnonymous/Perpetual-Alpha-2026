"""Real SQLite vintage/quality/concurrency tests; public HTTP is replaced at the transport boundary."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from perpetual_alpha.historical.client import ENDPOINT, PublicHistoryClient
from perpetual_alpha.historical.contracts import INTERVALS, PRICE_UNITS, candle, metadata, window
from perpetual_alpha.historical.ingest import HistoryIngestor
from perpetual_alpha.historical.store import HistoricalStore
from perpetual_alpha.journal import digest

ROOT = Path(__file__).resolve().parents[1]


def bar(opened=0, **changes):
    return {"t": opened, "T": opened + 59999, "s": "par:BTC", "i": "1m", "o": "100.00",
            "h": "110.00", "l": "90.00", "c": "105.00", "v": "1.25", "n": 3, **changes}


def meta(**changes):
    return {"universe": [{"name": s, "szDecimals": 4, "maxLeverage": 25, **changes} for s in PRICE_UNITS],
            "marginTables": []}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.db"
        self.now = 180000
        self.store = HistoricalStore(self.path, clock=lambda: self.now)

    def save(self, rows=None, identifier="one", received=120001, start=0, end=180000):
        return self.store.record_candles(identifier, "par:BTC", "1m", start, end,
                                         [bar()] if rows is None else rows, received)

    def read(self, *, asof=None, **kwargs):
        return self.store.read_candles("par:BTC", "1m", 0, 180000, self.now if asof is None else asof, **kwargs)

    def test_separate_versioned_wal_database_and_read_only_missing_does_not_create(self):
        with self.store.connection() as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(c.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(c.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        other = self.path.parent / "collector.db"
        c = sqlite3.connect(other)
        c.execute("CREATE TABLE snapshots(x)")
        c.close()
        with self.assertRaises(ValueError):
            HistoricalStore(other)
        missing = self.path.parent / "missing.db"
        with self.assertRaises(sqlite3.OperationalError):
            HistoricalStore(missing, read_only=True)
        self.assertFalse(missing.exists())

    def test_closed_bar_and_decimal_units(self):
        self.save([bar(), bar(60000), bar(120000)], received=180000)
        result = self.read()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["expected_records"], 3)
        self.assertEqual(result["records"][0]["close_ms"], 60000)
        self.assertEqual(result["records"][0]["open"], "100.00")
        self.assertEqual(result["price_unit"], "USD/BTC")
        self.assertEqual(result["volume_unit"], "venue-size")
        self.assertFalse(result["valuation_supported"])
        self.assertIsNone(result["records"][0]["published_ms"])
        self.assertEqual(result["payload_digest"], digest({k: v for k, v in result.items() if k != "payload_digest"}))

    def test_forming_bar_never_becomes_final_without_refetch(self):
        summary = self.save([bar(120000)], received=179999)
        self.assertEqual(summary["forming"], 1)
        self.assertEqual(self.read()["records"], [])
        self.now += 60000
        self.assertEqual(self.read()["records"], [])
        self.save([bar(120000)], identifier="closed", received=180000)
        self.assertEqual(self.read()["records"][0]["open_ms"], 120000)

    def test_close_boundary_is_exclusive_and_received_is_not_event_time(self):
        self.now = 60000
        self.save(received=60000)
        self.assertEqual(self.read(asof=59999)["records"], [])
        self.assertEqual(len(self.read(asof=60000)["records"]), 1)
        row = self.read(asof=60000)["records"][0]
        self.assertEqual(row["received_ms"], 60000)
        self.assertEqual(row["close_ms"], 60000)

    def test_backfill_received_after_asof_cannot_enter_earlier_job(self):
        self.save(received=170000)
        self.assertEqual(self.read(asof=160000)["records"], [])
        self.assertEqual(self.read(asof=179999)["records"], [])  # Received but not yet ingested.
        self.assertEqual(len(self.read(asof=180000)["records"]), 1)

    def test_revision_asof_and_pinned_snapshot_are_reproducible(self):
        self.save()
        original = self.read()
        self.now = 200000
        self.save([bar(c="106.00")], identifier="revision", received=190000)
        self.assertEqual(self.read(asof=180000)["records"], original["records"])
        newer = self.read()["records"][0]
        self.assertEqual(newer["close"], "106.00")
        self.assertEqual(newer["first_seen_ms"], 180000)
        pinned = self.read(snapshot_seq=original["snapshot_seq"])
        self.assertEqual(pinned["records"], original["records"])

    def test_a_b_a_revision_is_preserved_not_global_content_dedup(self):
        self.save()
        for n, close in enumerate(("106.00", "105.00"), start=1):
            self.now += 10000
            self.save([bar(c=close)], identifier=str(n), received=self.now)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM candle_versions").fetchone()[0], 3)
        self.assertEqual(self.read()["records"][0]["close"], "105.00")

    def test_same_observation_replay_and_conflicting_id_atomic(self):
        first = self.save()
        self.assertEqual(first, self.save())
        with self.assertRaises(ValueError):
            self.save([bar(c="101")])
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM observations").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM candle_versions").fetchone()[0], 1)

    def test_same_content_refresh_keeps_original_available_time(self):
        self.save()
        self.now += 10000
        summary = self.save(identifier="repeat", received=self.now)
        self.assertEqual(summary["unchanged"], 1)
        row = self.read()["records"][0]
        self.assertEqual(row["received_ms"], 120001)
        self.assertEqual(row["available_ms"], 180000)

    def test_invalid_revision_quarantines_instead_of_falling_back(self):
        self.save()
        self.now += 10000
        result = self.save([bar(h="99")], identifier="invalid", received=self.now)
        self.assertEqual(result["invalid"], 1)
        self.assertEqual(self.read()["status"], "invalid")
        self.assertEqual(self.read()["records"], [])
        self.assertEqual(len(self.read()["invalid"]), 1)
        self.assertEqual(len(self.read(asof=180000)["records"]), 1)

    def test_gap_ranges_and_partial_history_not_synthetic_zero_bars(self):
        self.save([bar(), bar(120000)], received=180000)
        result = self.read()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["gaps"], [{"start_ms": 60000, "end_ms": 120000}])
        self.assertEqual(len(result["records"]), 2)

    def test_empty_response_does_not_invent_availability_or_history(self):
        self.save([])
        result = self.read()
        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["gaps"], [{"start_ms": 0, "end_ms": 180000}])

    def test_duplicates_out_of_order_and_conflicting_duplicates(self):
        summary = self.save([bar(60000), bar(), bar()], received=180000)
        self.assertTrue(summary["out_of_order"])
        self.assertEqual(summary["duplicates"], 1)
        self.assertEqual([r["open_ms"] for r in self.read()["records"]], [0, 60000])
        self.now += 10000
        self.save([bar(), bar(c="103")], identifier="conflicting", received=self.now)
        self.assertEqual(self.read()["invalid"][0]["open_ms"], 0)
        self.assertEqual([r["open_ms"] for r in self.read()["records"]], [60000])

    def test_scope_wrong_interval_outside_window_rejects_whole_response(self):
        for bad in (bar(s="BTC"), bar(i="5m"), bar(180000), bar(t=True)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.save([bar(), bad])
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM observations").fetchone()[0], 0)

    def test_nonfinite_negative_impossible_ohlc_and_close_boundary_are_invalid(self):
        for n, bad in enumerate((bar(o="NaN"), bar(h="Infinity"), bar(v="-1"), bar(c=100),
                                  bar(n=True), bar(T=60000), bar(l="0"), bar(o="1e1000000"))):
            with self.subTest(bad=bad):
                self.assertEqual(self.save([bad], identifier=str(n))["invalid"], 1)

    def test_truncation_never_reports_ready_and_stale_is_explicit_policy(self):
        self.save([bar(), bar(60000), bar(120000)], received=180000)
        result = self.read(max_records=2)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["available_records"], 3)
        self.assertEqual(result["status"], "partial")
        self.now += 60000
        self.assertEqual(self.read()["status"], "ready")  # Explicit old window remains complete.
        self.assertEqual(self.read(max_age_ms=59999)["status"], "stale")

    def test_metadata_all_assets_units_and_no_invented_valuation(self):
        self.store.record_metadata("meta", meta(), 170000)
        result = self.store.read_metadata(self.now, max_age_ms=300000)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["assets"]), 10)
        self.assertEqual(result["assets"]["par:USDEUR"]["price_unit"], "USD/EUR")
        self.assertEqual(result["assets"]["par:USDJPY"]["price_unit"], "JPY/USD")
        for row in result["assets"].values():
            self.assertEqual(row["quantity_step"], "0.0001")
            self.assertIsNone(row["valuation_multiplier"])
            self.assertFalse(row["valuation_supported"])

    def test_metadata_missing_delisted_stale_and_revision_asof(self):
        payload = meta()
        payload["universe"] = payload["universe"][:2]
        payload["universe"][0]["isDelisted"] = True
        self.store.record_metadata("one", payload, 170000)
        old = self.store.read_metadata(self.now, max_age_ms=300000)
        self.assertEqual(old["status"], "partial")
        self.assertEqual(old["assets"]["par:HYPE"]["status"], "missing")
        self.assertEqual(old["assets"]["par:BTC"]["status"], "delisted")
        self.now += 10000
        self.store.record_metadata("two", meta(), self.now)
        self.assertEqual(self.store.read_metadata(180000, max_age_ms=300000)["assets"], old["assets"])
        self.assertEqual(self.store.read_metadata(500000, max_age_ms=10000)["status"], "stale")

    def test_invalid_metadata_rejected_no_partial_rules_and_no_future_visibility(self):
        for payload in (meta(szDecimals=True), meta(maxLeverage=0), meta(isDelisted="false"),
                        {"universe": [meta()["universe"][0]] * 2}):
            with self.assertRaises(ValueError):
                self.store.record_metadata("bad", payload, 170000)
        self.assertEqual(self.store.read_metadata(self.now, max_age_ms=10000)["status"], "missing")
        self.store.record_metadata("valid", meta(), 170000)
        self.assertEqual(self.store.read_metadata(179999, max_age_ms=10000)["status"], "missing")

    def test_old_response_cannot_overwrite_newer_metadata_or_candle(self):
        self.save(received=180000)
        self.store.record_metadata("meta", meta(), 180000)
        self.now += 10000
        with self.assertRaises(ValueError):
            self.save([bar(c="101")], identifier="older", received=170000)
        with self.assertRaises(ValueError):
            self.store.record_metadata("older", meta(szDecimals=3), 170000)
        self.assertEqual(self.read()["records"][0]["close"], "105.00")

    def test_future_timestamp_clock_rollback_and_window_resource_limits(self):
        with self.assertRaises(ValueError):
            self.save(received=180001)
        self.save()
        self.now -= 1
        with self.assertRaises(ValueError):
            self.save(identifier="rollback")
        for args in (("BTC", "1m", 0, 60000), ("par:BTC", "1M", 0, 60000),
                     ("par:BTC", "1m", 1, 60000), ("par:BTC", "1m", 0, 10001 * 60000)):
            with self.assertRaises(ValueError):
                window(*args)
        with self.assertRaises(ValueError):
            self.read(snapshot_seq=999)
        with self.assertRaises(ValueError):
            self.read(max_records=5001)

    def test_restart_and_read_only_reader_see_committed_wal(self):
        self.save()
        reader = HistoricalStore(self.path, clock=lambda: self.now, read_only=True)
        self.assertEqual(reader.read_candles("par:BTC", "1m", 0, 180000, self.now), self.read())
        with self.assertRaises(ValueError):
            reader.record_metadata("forbidden", meta(), 170000)
        restarted = HistoricalStore(self.path, clock=lambda: self.now)
        self.assertEqual(restarted.read_candles("par:BTC", "1m", 0, 180000, self.now), self.read())

    def test_concurrent_writers_deduplicate_one_vintage(self):
        def write(n):
            store = HistoricalStore(self.path, clock=lambda: self.now)
            return store.record_candles(str(n), "par:BTC", "1m", 0, 180000, [bar()], 170000)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(write, range(8)))
        self.assertEqual(sum(r["inserted"] for r in results), 1)
        self.assertEqual(sum(r["unchanged"] for r in results), 7)

    def test_failure_during_page_commit_rolls_back_observation(self):
        original = self.store._observation

        def fail(*args):
            original(*args)
            raise RuntimeError("crash before page commit")

        with patch.object(self.store, "_observation", fail), self.assertRaises(RuntimeError):
            self.save()
        self.assertEqual(self.read()["records"], [])
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM observations").fetchone()[0], 0)
        self.save()
        self.assertEqual(len(self.read()["records"]), 1)

    def test_hard_process_exit_rolls_back_uncommitted_page(self):
        self.save()
        code = '''
import os,sys
from perpetual_alpha.historical.store import HistoricalStore
s=HistoricalStore(sys.argv[1],clock=lambda:190000)
with s.write() as (c,now):
    s._observation(c,now,'uncommitted','candles',{},190000,[])
    os._exit(74)
'''
        result = subprocess.run([sys._base_executable, "-c", code, str(self.path)], timeout=10,
                                capture_output=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
        self.assertEqual(result.returncode, 74, result.stderr.decode())
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM observations").fetchone()[0], 1)
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(len(self.read()["records"]), 1)

    def test_failed_second_network_page_keeps_first_page_and_can_resume(self):
        owner = self

        class Client:
            fail = True

            def candles(self, symbol, interval, start, end):
                if start and self.fail:
                    raise TimeoutError("public request timed out")
                return [bar(start)], owner.now

        self.now = 1000 * 60000
        client = Client()
        ingestor = HistoryIngestor(self.store, client)
        with self.assertRaises(TimeoutError):
            ingestor.backfill("par:BTC", "1m", 0, self.now)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM candle_versions").fetchone()[0], 1)
        client.fail = False
        ingestor.backfill("par:BTC", "1m", 0, self.now)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM candle_versions").fetchone()[0], 2)

    def test_read_transaction_pins_snapshot_while_a_writer_commits(self):
        self.save()
        original = self.store._snapshot

        def interleave(c, asof, snapshot_seq):
            pinned = original(c, asof, snapshot_seq)
            # Same-millisecond commit is intentionally possible; the reader must keep its SQL snapshot.
            self.save([bar(c="106.00")], identifier="interleaved", received=180000)
            return pinned

        with patch.object(self.store, "_snapshot", interleave):
            self.assertEqual(self.read()["records"][0]["close"], "105.00")
        self.assertEqual(self.read()["records"][0]["close"], "106.00")

    def test_paging_bounded_empty_history_and_no_network_in_transaction(self):
        owner = self

        class Client:
            calls = []

            def candles(self, symbol, interval, start, end):
                # A second writer succeeds, proving the HTTP fetch does not hold a DB write transaction.
                with owner.store.write():
                    pass
                self.calls.append((start, end))
                return [], owner.now

        self.now = 1000 * 60000
        client = Client()
        ingestor = HistoryIngestor(self.store, client)
        with self.assertRaises(ValueError):
            ingestor.backfill("par:BTC", "1m", 0, 1000 * 60000, max_requests=1)
        self.assertEqual(client.calls, [])
        result = ingestor.backfill("par:BTC", "1m", 0, 1000 * 60000)
        self.assertEqual(client.calls, [(0, 500 * 60000), (500 * 60000, 1000 * 60000)])
        self.assertEqual(result["coverage"]["status"], "missing")

    def test_unsupported_intervals_do_not_silently_relabel(self):
        for interval, length in INTERVALS.items():
            self.assertEqual(window("par:BTC", interval, 0, length), length)
        for interval in ("1w", "1M", "3d", "60s"):
            with self.assertRaises(ValueError):
                window("par:BTC", interval, 0, 60000)


class ClientTests(unittest.TestCase):
    def test_source_json_duplicate_keys_nonfinite_and_size_are_rejected(self):
        client = PublicHistoryClient()
        for body in (b'{"universe":[],"universe":[]}', b'[NaN]', b'\xff', b' ' * (8 * 1024 * 1024 + 1)):
            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

                def read(self, limit):
                    return body

            with patch.object(client.opener, "open", return_value=Response()), patch("time.sleep"):
                with self.assertRaises((ValueError, UnicodeError)):
                    client.metadata()

    def test_public_request_is_only_fixed_testnet_info_no_auth(self):
        client = PublicHistoryClient(clock=lambda: 180000)
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, limit):
                return b'[]'

        def open_request(request, *, timeout):
            requests.append(request)
            self.assertEqual(timeout, 15)
            self.assertEqual(request.full_url, ENDPOINT)
            self.assertFalse(request.has_header("Authorization"))
            return Response()

        with patch.object(client.opener, "open", open_request), patch("time.sleep"):
            payload, received = client.candles("par:BTC", "1m", 0, 60000)
            client.metadata()
        self.assertEqual(payload, [])
        self.assertEqual(received, 180000)
        self.assertEqual(json.loads(requests[0].data), {"type": "candleSnapshot", "req": {
            "coin": "par:BTC", "interval": "1m", "startTime": 0, "endTime": 59999}})
        self.assertEqual(json.loads(requests[1].data), {"type": "meta", "dex": "par"})

    def test_public_capture_btc_eth_normalizes_offline(self):
        sample = json.loads((ROOT / "examples/history/testnet-par-capture.json").read_text(encoding="utf-8"))
        self.assertEqual(len(metadata(sample["metadata"]["payload"])), 10)
        self.assertEqual(digest(sample["metadata"]["payload"]), sample["metadata"]["payload_digest"])
        for response in sample["candles"]:
            self.assertEqual(response["sample_digest"], digest(response["records"]))
            for raw in response["records"]:
                normalized = candle(raw, response["symbol"], response["interval"])
                self.assertLessEqual(normalized["close_ms"], response["received_ms"])
        self.assertEqual({r["symbol"] for r in sample["candles"]}, {"par:BTC", "par:ETH"})
