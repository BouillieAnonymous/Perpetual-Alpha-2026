"""Append-only vintages on a separate WAL database; each operation owns its connection."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from ..journal import canonical, digest
from ..runtime.contracts import decode
from .contracts import CATALOG_VERSION, PRICE_UNITS, SCHEMA, SOURCE, candle, metadata, window

APPLICATION_ID = 0x50414831
SCHEMA_VERSION = 1


class HistoricalStore:
    def __init__(self, path, *, clock=None, read_only=False):
        if str(path) == ":memory:":
            raise ValueError("History needs a durable file")
        self.path, self.clock, self.read_only = Path(path).resolve(), clock or (lambda: time.time_ns() // 1000000), read_only
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            if not read_only:
                c.execute("BEGIN IMMEDIATE")
            try:
                application, version = c.execute("PRAGMA application_id").fetchone()[0], c.execute("PRAGMA user_version").fetchone()[0]
                if application == 0 and version == 0 and not read_only and not c.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                    sql = (Path(__file__).parent / "migrations/001_history.sql").read_text(encoding="utf-8")
                    for statement in sql.split(";"):
                        if statement.strip():
                            c.execute(statement)
                    c.execute(f"PRAGMA application_id={APPLICATION_ID}")
                    c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                elif application != APPLICATION_ID or version != SCHEMA_VERSION:
                    raise ValueError("Not a supported historical database")
                if not read_only:
                    c.commit()
                    c.execute("PRAGMA journal_mode=WAL")
            except BaseException:
                if not read_only:
                    c.rollback()
                raise

    @contextmanager
    def connection(self):
        if self.read_only:
            c = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5, isolation_level=None)
            c.execute("PRAGMA query_only=ON")
        else:
            c = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA synchronous=FULL")
            yield c
        finally:
            c.close()

    @contextmanager
    def write(self):
        if self.read_only:
            raise ValueError("Read-only history handle")
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                now = self.clock()
                decode(int, now)
                old = c.execute("SELECT last_recorded_ms FROM history_meta WHERE id=1").fetchone()[0]
                if now < old:
                    raise ValueError("History clock moved backwards")
                c.execute("UPDATE history_meta SET last_recorded_ms=? WHERE id=1", (now,))
                yield c, now
                c.commit()
            except BaseException:
                c.rollback()
                raise

    def _observation(self, c, now, observation_id, kind, request, received_ms, raw):
        decode(str, observation_id)
        decode(int, received_ms)
        if received_ms > now:
            raise ValueError("Future receive timestamp")
        encoded = canonical(raw)
        if len(encoded.encode()) > 8 * 1024 * 1024:
            raise ValueError("History response exceeds storage bound")
        fingerprint = digest(raw)
        saved = c.execute("SELECT * FROM observations WHERE observation_id=?", (observation_id,)).fetchone()
        if saved:
            if (saved["kind"], saved["request"], saved["received_ms"], saved["raw_digest"]) != (
                    kind, canonical(request), received_ms, fingerprint):
                raise ValueError("Observation ID conflict")
            return saved["seq"], json.loads(saved["summary"])
        cursor = c.execute("INSERT INTO observations(observation_id,kind,request,received_ms,available_ms,raw_digest,raw,summary) "
                           "VALUES(?,?,?,?,?,?,?,'{}')", (observation_id, kind, canonical(request), received_ms, now,
                                                         fingerprint, encoded))
        return cursor.lastrowid, None

    def record_metadata(self, observation_id, payload, received_ms):
        normalized = metadata(payload)
        with self.write() as (c, now):
            seq, old = self._observation(c, now, observation_id, "metadata", {"type": "meta", "dex": "par"}, received_ms, payload)
            if old is not None:
                return old
            last = c.execute("SELECT max(received_ms) FROM observations WHERE kind='metadata' AND seq<?", (seq,)).fetchone()[0]
            if last is not None and received_ms < last:
                raise ValueError("Older metadata response cannot supersede a newer response")
            c.execute("INSERT INTO metadata_versions VALUES(?,?)", (seq, canonical(normalized)))
            summary = {"seq": seq, "received_ms": received_ms, "available_ms": now,
                       "missing": [s for s, row in normalized.items() if row["status"] == "missing"]}
            c.execute("UPDATE observations SET summary=? WHERE seq=?", (canonical(summary), seq))
            return summary

    def record_candles(self, observation_id, symbol, interval, start_ms, end_ms, payload, received_ms):
        step = window(symbol, interval, start_ms, end_ms)
        if not isinstance(payload, list) or len(payload) > 5000:
            raise ValueError("Expected at most 5000 candles")
        decode(int, received_ms)
        request = {"coin": symbol, "interval": interval, "start_ms": start_ms, "end_ms": end_ms}
        # Validate scope before writing anything; malformed/foreign keys cannot pollute another series.
        groups = {}
        order = []
        for raw in payload:
            if (not isinstance(raw, dict) or raw.get("s") != symbol or raw.get("i") != interval
                    or type(raw.get("t")) is not int or raw["t"] % step or not start_ms <= raw["t"] < end_ms):
                raise ValueError("Candle outside requested series/window")
            groups.setdefault(raw["t"], []).append(raw)
            order.append(raw["t"])
        with self.write() as (c, now):
            seq, old = self._observation(c, now, observation_id, "candles", request, received_ms, payload)
            if old is not None:
                return old
            summary = {"seq": seq, "received_ms": received_ms, "available_ms": now, "inserted": 0, "unchanged": 0,
                       "forming": 0, "invalid": 0, "duplicates": len(payload) - len(groups),
                       "out_of_order": order != sorted(order)}
            for opened, rows in sorted(groups.items()):
                if opened + step > received_ms:
                    summary["forming"] += 1
                    continue
                reason, normalized = None, None
                try:
                    if len({digest(r) for r in rows}) > 1:
                        raise ValueError("Conflicting duplicate candle")
                    normalized = candle(rows[0], symbol, interval)
                except (ValueError, TypeError, KeyError, ArithmeticError) as error:
                    reason = str(error)
                    summary["invalid"] += 1
                fingerprint = digest({"rows": rows if reason else rows[0], "reason": reason})
                previous = c.execute("SELECT v.digest,o.received_ms FROM candle_versions v JOIN observations o USING(seq) "
                                     "WHERE symbol=? AND interval=? AND open_ms=? ORDER BY seq DESC LIMIT 1",
                                     (symbol, interval, opened)).fetchone()
                if previous and received_ms < previous["received_ms"]:
                    raise ValueError("Older candle response cannot supersede a newer response")
                if previous and previous[0] == fingerprint:
                    summary["unchanged"] += 1
                    continue
                c.execute("INSERT INTO candle_versions(seq,symbol,interval,open_ms,close_ms,digest,valid,reason,payload) "
                          "VALUES(?,?,?,?,?,?,?,?,?)", (seq, symbol, interval, opened, opened + step, fingerprint,
                                                      int(reason is None), reason, canonical(normalized) if normalized else None))
                summary["inserted"] += 1
            c.execute("UPDATE observations SET summary=? WHERE seq=?", (canonical(summary), seq))
            return summary

    @staticmethod
    def _snapshot(c, asof_ms, snapshot_seq):
        decode(int, asof_ms)
        maximum = c.execute("SELECT coalesce(max(seq),0) FROM observations").fetchone()[0]
        if snapshot_seq is None:
            return c.execute("SELECT coalesce(max(seq),0) FROM observations WHERE available_ms<=? AND received_ms<=?",
                             (asof_ms, asof_ms)).fetchone()[0]
        decode(int, snapshot_seq)
        if snapshot_seq > maximum:
            raise ValueError("Future snapshot sequence")
        return snapshot_seq

    def read_metadata(self, asof_ms, *, max_age_ms, snapshot_seq=None):
        decode(int, max_age_ms)
        with self.connection() as c:
            c.execute("BEGIN")
            snap = self._snapshot(c, asof_ms, snapshot_seq)
            row = c.execute("SELECT o.*,m.payload FROM metadata_versions m JOIN observations o USING(seq) "
                            "WHERE o.seq<=? AND o.available_ms<=? AND o.received_ms<=? ORDER BY o.seq DESC LIMIT 1",
                            (snap, asof_ms, asof_ms)).fetchone()
            result = {"schema_version": SCHEMA, "source": SOURCE, "asof_ms": asof_ms, "snapshot_seq": snap,
                      "status": "missing", "received_ms": None, "available_ms": None,
                      "event_ms": None, "published_ms": None, "venue_metadata": None,
                      "assets": metadata({"universe": []})}
            if row:
                assets = json.loads(row["payload"])
                complete = all(a["status"] == "ready" for a in assets.values())
                result.update(status="stale" if asof_ms - row["received_ms"] > max_age_ms else "ready" if complete else "partial",
                              received_ms=row["received_ms"], available_ms=row["available_ms"],
                              assets=assets, venue_metadata=json.loads(row["raw"]),
                              raw_digest=row["raw_digest"], revision_seq=row["seq"])
            result["payload_digest"] = digest(result)
            return result

    def read_candles(self, symbol, interval, start_ms, end_ms, asof_ms, *, max_records=5000,
                     max_age_ms=None, snapshot_seq=None):
        step = window(symbol, interval, start_ms, end_ms)
        decode(int, max_records)
        if not 1 <= max_records <= 5000:
            raise ValueError("Record budget must be 1..5000")
        if max_age_ms is not None:
            decode(int, max_age_ms)
        with self.connection() as c:
            c.execute("BEGIN")
            snap = self._snapshot(c, asof_ms, snapshot_seq)
            # Pick the newest visible version including invalid revisions: never fall back around quarantine.
            rows = c.execute("WITH visible AS (SELECT v.*,o.received_ms,o.available_ms,o.raw_digest response_digest, "
                             "row_number() OVER(PARTITION BY v.open_ms ORDER BY v.seq DESC) rank "
                             "FROM candle_versions v JOIN observations o USING(seq) "
                             "WHERE v.symbol=? AND v.interval=? AND v.open_ms>=? AND v.open_ms<? "
                             "AND v.close_ms<=? AND o.available_ms<=? AND o.received_ms<=? AND v.seq<=?) "
                             "SELECT * FROM visible WHERE rank=1 ORDER BY open_ms",
                             (symbol, interval, start_ms, end_ms, asof_ms, asof_ms, asof_ms, snap)).fetchall()
            valid = {row["open_ms"]: row for row in rows if row["valid"]}
            invalid = [{"open_ms": row["open_ms"], "reason": row["reason"], "revision_id": row["revision_id"]}
                       for row in rows if not row["valid"]]
            missing = [opened for opened in range(start_ms, end_ms, step) if opened not in valid]
            gaps = []
            for opened in missing:
                if gaps and gaps[-1]["end_ms"] == opened:
                    gaps[-1]["end_ms"] += step
                else:
                    gaps.append({"start_ms": opened, "end_ms": opened + step})
            records = []
            for row in list(valid.values())[:max_records]:
                first = c.execute("SELECT min(o.available_ms) FROM candle_versions v JOIN observations o USING(seq) "
                                  "WHERE v.symbol=? AND v.interval=? AND v.open_ms=? AND v.seq<=?",
                                  (symbol, interval, row["open_ms"], snap)).fetchone()[0]
                records.append({**json.loads(row["payload"]), "revision_id": row["revision_id"], "revision_seq": row["seq"],
                                "received_ms": row["received_ms"], "available_ms": row["available_ms"],
                                "first_seen_ms": first, "published_ms": None, "payload_digest": row["digest"],
                                "source_response_digest": row["response_digest"]})
            truncated = len(valid) > max_records
            status = "ready" if not gaps and not truncated else "partial" if valid else "invalid" if invalid else "missing"
            if max_age_ms is not None and valid and asof_ms - max(r["close_ms"] for r in valid.values()) > max_age_ms:
                status = "stale"
            result = {"schema_version": SCHEMA, "source": SOURCE, "symbol": symbol, "interval": interval,
                      "price_unit": PRICE_UNITS[symbol], "volume_unit": "venue-size", "settlement_asset": "USDPR",
                      "catalog_version": CATALOG_VERSION, "valuation_supported": False,
                      "start_ms": start_ms, "end_ms": end_ms, "asof_ms": asof_ms, "snapshot_seq": snap,
                      "status": status, "expected_records": (end_ms - start_ms) // step, "available_records": len(valid),
                      "not_yet_closed_records": (end_ms - max(start_ms, min(end_ms, asof_ms // step * step))) // step,
                      "records": records, "gaps": gaps, "invalid": invalid, "truncated": truncated,
                      "closed_only": True, "published_time_known": False}
            result["payload_digest"] = digest(result)
            return result
