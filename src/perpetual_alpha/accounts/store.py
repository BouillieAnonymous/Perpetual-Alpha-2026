"""Independent account evidence DB; immutable raw+normalized capture and strict as-of reads."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from ..journal import canonical, digest
from ..runtime.contracts import decode
from .client import wallet_address
from .normalize import normalize

APPLICATION_ID = 0x50414131


class AccountStore:
    def __init__(self, path, *, clock=None, read_only=False):
        if str(path) == ":memory:":
            raise ValueError("Durable account evidence file required")
        self.path, self.clock, self.read_only = Path(path).resolve(), clock or (lambda: time.time_ns() // 1000000), read_only
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            if not read_only:
                c.execute("BEGIN IMMEDIATE")
            try:
                app, version = c.execute("PRAGMA application_id").fetchone()[0], c.execute("PRAGMA user_version").fetchone()[0]
                if app == 0 and version == 0 and not read_only and not c.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                    c.execute("CREATE TABLE account_meta(id INTEGER PRIMARY KEY CHECK(id=1),last_now_ms INTEGER NOT NULL)")
                    c.execute("INSERT INTO account_meta VALUES(1,0)")
                    c.execute("CREATE TABLE captures(capture_id TEXT PRIMARY KEY,wallet TEXT NOT NULL,received_ms INTEGER NOT NULL,"
                              "available_ms INTEGER NOT NULL,bundle TEXT NOT NULL,snapshot TEXT NOT NULL,content_digest TEXT NOT NULL)")
                    c.execute("CREATE INDEX capture_asof ON captures(wallet,available_ms,received_ms)")
                    c.execute(f"PRAGMA application_id={APPLICATION_ID}")
                    c.execute("PRAGMA user_version=1")
                elif app != APPLICATION_ID or version != 1:
                    raise ValueError("Not a supported account evidence database")
                if not read_only:
                    c.commit()
                    c.execute("PRAGMA journal_mode=WAL")
            except BaseException:
                if not read_only:
                    c.rollback()
                raise

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5, isolation_level=None) if self.read_only else (
            sqlite3.connect(self.path, timeout=5, isolation_level=None))
        try:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA synchronous=FULL")
            c.execute("PRAGMA foreign_keys=ON")
            if self.read_only:
                c.execute("PRAGMA query_only=ON")
            yield c
        finally:
            c.close()

    def save(self, bundle):
        if self.read_only:
            raise ValueError("Account evidence handle is read-only")
        snapshot = normalize(bundle)
        fingerprint = digest({"bundle": bundle, "snapshot": snapshot})
        if len(canonical(bundle).encode()) + len(canonical(snapshot).encode()) > 16 * 1024 * 1024:
            raise ValueError("Account capture storage budget exceeded")
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                now = self.clock()
                decode(int, now)
                if now < c.execute("SELECT last_now_ms FROM account_meta WHERE id=1").fetchone()[0]:
                    raise ValueError("Account store clock moved backwards")
                if now < bundle["ended_ms"]:
                    raise ValueError("Cannot store a future capture")
                old = c.execute("SELECT content_digest FROM captures WHERE capture_id=?", (bundle["capture_id"],)).fetchone()
                if old:
                    if old[0] != fingerprint:
                        raise ValueError("Capture ID content conflict")
                else:
                    c.execute("INSERT INTO captures VALUES(?,?,?,?,?,?,?)", (bundle["capture_id"], snapshot["wallet"],
                              bundle["ended_ms"], now, canonical(bundle), canonical(snapshot), fingerprint))
                c.execute("UPDATE account_meta SET last_now_ms=? WHERE id=1", (now,))
                c.commit()
            except BaseException:
                c.rollback()
                raise
        return snapshot

    def latest(self, wallet, asof_ms, *, max_age_ms):
        wallet = wallet_address(wallet)
        decode(int, asof_ms)
        decode(int, max_age_ms)
        with self.connection() as c:
            row = c.execute("SELECT * FROM captures WHERE wallet=? AND received_ms<=? AND available_ms<=? "
                            "ORDER BY available_ms DESC,rowid DESC LIMIT 1", (wallet, asof_ms, asof_ms)).fetchone()
        if row is None:
            return {"status": "missing", "asof_ms": asof_ms, "snapshot": None, "risk_ready": False}
        bundle, snapshot = json.loads(row["bundle"]), json.loads(row["snapshot"])
        if (digest({"bundle": bundle, "snapshot": snapshot}) != row["content_digest"]
                or digest({k: v for k, v in snapshot.items() if k != "payload_digest"}) != snapshot["payload_digest"]):
            raise ValueError("Stored account evidence digest mismatch")
        age = asof_ms - snapshot["started_ms"]  # Oldest response is conservatively bounded by capture start.
        return {"status": "stale" if age > max_age_ms else snapshot["status"], "asof_ms": asof_ms,
                "available_ms": row["available_ms"], "age_ms": age, "snapshot": snapshot,
                "content_digest": row["content_digest"], "risk_ready": False}
