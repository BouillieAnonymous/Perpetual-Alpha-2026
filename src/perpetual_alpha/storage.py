"""Single-process durable public market snapshots."""
import json
import sqlite3
import time


class Store:
    def __init__(self, path):
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS snapshots (
            symbol TEXT NOT NULL, event_ms INTEGER NOT NULL, received_ms INTEGER NOT NULL,
            bid REAL NOT NULL, ask REAL NOT NULL, mark REAL NOT NULL, oracle REAL NOT NULL,
            funding REAL NOT NULL, raw_json TEXT NOT NULL,
            PRIMARY KEY(symbol, event_ms))""")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS runtime (
            id INTEGER PRIMARY KEY CHECK(id=1), heartbeat_ms INTEGER NOT NULL,
            last_success_ms INTEGER, error TEXT)""")
        self.connection.commit()

    def save(self, row):
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?,?,?,?,?,?)",
                (row["symbol"], row["event_ms"], row["received_ms"], row["bid"], row["ask"],
                 row["mark"], row["oracle"], row["funding"], json.dumps(row["raw"])),
            )

    def heartbeat(self, error=None, success=False):
        now = int(time.time() * 1000)
        with self.connection:
            self.connection.execute("""INSERT INTO runtime VALUES (1,?,?,?)
                ON CONFLICT(id) DO UPDATE SET heartbeat_ms=excluded.heartbeat_ms,
                last_success_ms=COALESCE(excluded.last_success_ms,runtime.last_success_ms),
                error=excluded.error""", (now, now if success else None, error))

    def close(self):
        self.connection.close()
