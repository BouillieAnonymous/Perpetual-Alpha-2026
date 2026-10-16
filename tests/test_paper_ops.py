"""WAL backup, restore, disk failure and local liveness; no external services."""
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from paper_support import configuration, enqueue
from runtime_support import Clock

from perpetual_alpha.runtime.ingress import Principal
from perpetual_alpha.runtime.jobs import JobQueue
from perpetual_alpha.runtime.paper_engine import PaperEngine
from perpetual_alpha.runtime.paper_health import probe, publish
from perpetual_alpha.runtime.paper_ops import backup, inspect, verify
from perpetual_alpha.runtime.store import RuntimeStore


class PaperOpsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/"runtime.sqlite3"
        self.clock = Clock(int(time.time()*1000))
        self.config = configuration()
        self.store = RuntimeStore(self.path, clock=self.clock)
        self.engine = PaperEngine(self.store, self.config)
        self.engine.boot()

    def test_live_wal_backup_restores_partial_fill_paused_without_changing_source(self):
        queue = JobQueue(self.store)
        principal = Principal("paper-worker", "dummy-agent", "offline-0.1", "control")
        queue.configure_control("mock-account", "dummy-agent", "offline-0.1", 1)
        queue.configure_worker(principal)
        state = self.engine.status()
        self.engine.resume("ops-resume", state["generation"], state["pause_epoch"], "ops-test")
        enqueue(queue, principal, self.clock.now)
        with self.store.connection() as keeper:
            keeper.execute("PRAGMA wal_autocheckpoint=0")
            self.engine.step()
            self.engine.step()
            self.assertGreater(Path(str(self.path)+"-wal").stat().st_size, 0)
            before = inspect(self.path)
            target = self.root/"backup.sqlite3"
            result = backup(self.path, target, self.config)
            self.assertTrue(result["restore_verified"])
            self.assertEqual(result["counts"]["paper_fills"], 1)
            self.assertEqual(inspect(self.path)["generation"], before["generation"])
            self.assertFalse(inspect(self.path)["paused"])
            self.assertEqual(inspect(target)["states"]["paper_intents"], {"partial": 1})
            original = target.read_bytes()
            self.assertTrue(verify(target, self.config)["restored_boot_paused"])
            self.assertEqual(target.read_bytes(), original)

    def test_never_overwrites_backup_or_source(self):
        target = self.root/"backup.sqlite3"
        target.write_bytes(b"existing evidence")
        with self.assertRaises(FileExistsError):
            backup(self.path, target, self.config)
        self.assertEqual(target.read_bytes(), b"existing evidence")
        with self.assertRaises(FileExistsError):
            backup(self.path, self.path, self.config)

    def test_wrong_configuration_does_not_publish_backup(self):
        config = configuration()
        config["configuration_id"] = "another-policy"
        target = self.root/"backup.sqlite3"
        with self.assertRaises(ValueError):
            backup(self.path, target, config)
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob(".paper-backup-*")))

    def test_corrupt_ledger_not_certified_as_restore(self):
        with self.store.transaction() as (c, _):
            c.execute("INSERT INTO paper_positions VALUES('par:BTC','7','80000')")
        with self.assertRaises(ValueError):
            verify(self.path, self.config)

    def test_missing_and_foreign_database_not_created_or_migrated(self):
        path = self.root/"missing.sqlite3"
        with self.assertRaises(FileNotFoundError):
            inspect(path)
        self.assertFalse(path.exists())
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE unrelated(value)")
        c.close()
        with self.assertRaises(ValueError):
            inspect(path)

    def test_sqlite_full_rolls_back_and_ledger_stays_valid(self):
        # Bound this copied SQLite to its current pages. Never fill the host disk.
        with self.store.connection() as c:
            pages = c.execute("PRAGMA page_count").fetchone()[0]
            c.execute(f"PRAGMA max_page_count={pages}")
            c.execute("BEGIN IMMEDIATE")
            with self.assertRaises(sqlite3.OperationalError) as error:
                c.execute("INSERT INTO audit(observed_ms,kind,payload) VALUES(1,'disk-full',?)", ("x"*1048576,))
            self.assertEqual(error.exception.sqlite_errorcode, sqlite3.SQLITE_FULL)
            c.rollback()
        self.engine.verify_ledger()
        self.assertTrue(inspect(self.path)["paused"])

    def test_paused_progress_is_live_but_not_ready(self):
        path = self.root/"progress.json"
        publish(path, {"generation": "test", "paused": True}, False)
        self.assertTrue(probe(path)["paused"])
        self.assertFalse(probe(path)["ready"])

    def test_stale_future_and_missing_progress_fail_closed(self):
        path = self.root/"progress.json"
        with self.assertRaises(FileNotFoundError):
            probe(path)
        for timestamp in (time.monotonic()-60, time.monotonic()+60):
            path.write_text(json.dumps({"pid": os.getpid(), "monotonic": timestamp, "mode": "synthetic-paper"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                probe(path)


if __name__ == "__main__":
    unittest.main()
