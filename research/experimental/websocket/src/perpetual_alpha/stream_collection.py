"""Public testnet streams, append-only evidence, and conservative asynchronous joins.

Independent CLI: python -m perpetual_alpha.stream_collection --database ...
Does not write legacy snapshots/runtime or import execution components.
"""
import argparse
import hashlib
import json
import math
import signal
import sqlite3
import time
import uuid
from pathlib import Path

from .historical.client import PublicHistoryClient
from .historical.contracts import PRICE_UNITS, metadata
from .market_data import normalize

ENDPOINT = "wss://api.hyperliquid-testnet.xyz/ws"


class ObservationStore:
    """Additive migration: never alter/backfill/delete the legacy snapshots table."""

    def __init__(self, path):
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS collection_events_v2 (
                session TEXT NOT NULL, sequence INTEGER NOT NULL, received_ms INTEGER NOT NULL,
                channel TEXT NOT NULL, symbol TEXT, raw_json TEXT NOT NULL,
                PRIMARY KEY(session,sequence));
            CREATE TABLE IF NOT EXISTS observations_v2 (
                session TEXT NOT NULL, sequence INTEGER NOT NULL, symbol TEXT NOT NULL,
                event_ms INTEGER NOT NULL, received_ms INTEGER NOT NULL,
                quality TEXT NOT NULL, raw_json TEXT NOT NULL,
                PRIMARY KEY(session,sequence));
            CREATE INDEX IF NOT EXISTS observations_v2_symbol_time
                ON observations_v2(symbol,received_ms);
        """)

    def save(self, session, sequence, now, message, row=None):
        # Wire observation identity, not book event time. Context-only updates survive.
        with self.connection:
            self.connection.execute("INSERT INTO collection_events_v2 VALUES (?,?,?,?,?,?)",
                                    (session, sequence, now, message.get("channel", "unknown"),
                                     message.get("data", {}).get("coin")
                                     if isinstance(message.get("data"), dict) else None, json.dumps(message)))
            if row is not None:
                self.connection.execute("INSERT INTO observations_v2 VALUES (?,?,?,?,?,?,?)",
                                        (session, sequence, row["symbol"], row["event_ms"], now,
                                         row["quality"], json.dumps(row)))

    def close(self):
        self.connection.close()


class StreamJoin:
    def __init__(self, symbols, max_age_ms=10000, max_skew_ms=2000):
        if not symbols or len(symbols) != len(set(symbols)) or set(symbols) - PRICE_UNITS.keys():
            raise ValueError("Select unique competition markets")
        if not 0 < max_skew_ms <= max_age_ms <= 90000:
            raise ValueError("Invalid freshness bounds")
        self.symbols, self.max_age, self.max_skew = tuple(symbols), max_age_ms, max_skew_ms
        self.books, self.contexts = {}, {}

    def feed(self, message, now):
        channel, data = message.get("channel"), message.get("data", {})
        if channel not in ("l2Book", "activeAssetCtx"):
            return None
        if not isinstance(data, dict):
            raise ValueError("Invalid stream payload")
        symbol = data.get("coin")
        if symbol not in self.symbols:
            raise ValueError("Unexpected stream market")
        cache = self.books if channel == "l2Book" else self.contexts
        if channel == "l2Book" and symbol in cache and data.get("time", -1) < cache[symbol][0]["time"]:
            raise ValueError("Out-of-order book")
        cache[symbol] = (data if channel == "l2Book" else data["ctx"], now)
        if symbol not in self.books or symbol not in self.contexts:
            return None
        book, book_received = self.books[symbol]
        context, context_received = self.contexts[symbol]
        row = dict(symbol=symbol, event_ms=book.get("time", -1), received_ms=now,
                   context_received_ms=context_received, book_received_ms=book_received,
                   context_event_ms=None, oracle_source_event_ms=None,
                   book=book, context=context, quality="invalid", quality_reason=None,
                   source="hyperliquid-testnet/par", schema_version=2)
        try:
            normalized = normalize(symbol, context, book, now, self.max_age / 1000, context_received)
            for side in book["levels"]:
                if not side or any(not math.isfinite(float(x[k])) or float(x[k]) <= 0
                                   for x in side for k in ("px", "sz")):
                    raise ValueError("Invalid depth")
            for side, descending in zip(book["levels"], (True, False)):
                prices = [float(x["px"]) for x in side]
                if prices != sorted(set(prices), reverse=descending):
                    raise ValueError("Unsorted depth")
            row.update({k: normalized[k] for k in ("bid", "ask", "mark", "oracle", "funding")})
            if not 0 <= now - context_received <= self.max_age or not 0 <= now - book_received <= self.max_age:
                raise ValueError("Stale stream receipt")
            if abs(book_received - context_received) > self.max_skew:
                raise ValueError("Asynchronous receipt skew")
            row["quality"] = "receipt_fresh_source_time_unknown"
        except (KeyError, IndexError, TypeError, ValueError) as error:
            row["quality_reason"] = str(error)
        return row


def capture(database, symbols, seconds=None, max_age_ms=10000, max_skew_ms=2000):
    import websocket

    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    # Separate local database recommended; SQLite serializes writers if an existing DB is selected.
    store = ObservationStore(database)
    start, attempts = time.monotonic(), 0
    deadline = start + seconds if seconds is not None else float("inf")
    sequence, session = 0, uuid.uuid4().hex

    def persist(message, row=None):
        nonlocal sequence
        sequence += 1
        store.save(session, sequence, time.time_ns() // 1000000, message, row)

    try:
        while not stopped and time.monotonic() < deadline:
            socket = None
            try:
                request_started = time.perf_counter()
                payload, received = PublicHistoryClient().metadata()
                request_elapsed_ms = (time.perf_counter() - request_started) * 1000
                persist({"channel": "metadata", "data": {"payload": payload, "received_ms": received,
                         "markets": metadata(payload), "request_elapsed_ms": request_elapsed_ms,
                         "sha256": hashlib.sha256(json.dumps(payload).encode()).hexdigest()}})
                # New connection always starts empty: never join pre-disconnect context/book.
                joined = StreamJoin(symbols, max_age_ms, max_skew_ms)
                connection_id = uuid.uuid4().hex
                socket = websocket.create_connection(ENDPOINT, timeout=10, http_no_proxy=["*"])
                persist({"channel": "connected", "data": {}})
                for symbol in symbols:
                    for channel in ("l2Book", "activeAssetCtx"):
                        socket.send(json.dumps({"method": "subscribe", "subscription": {"type": channel,
                                                                                              "coin": symbol}}))
                connection_started = time.monotonic()
                ping_at = time.monotonic()
                while not stopped and time.monotonic() < deadline:
                    if time.monotonic() - connection_started >= 3600:
                        raise ConnectionError("Hourly metadata refresh")
                    if time.monotonic() - ping_at >= 20:
                        socket.send('{"method":"ping"}')
                        ping_at = time.monotonic()
                    try:
                        wire = socket.recv()
                    except websocket.WebSocketTimeoutException:
                        # Timeout is a discontinuity; reconnect instead of trusting cached context.
                        raise ConnectionError("No websocket response within timeout") from None
                    if not wire:
                        raise ConnectionError("Websocket closed")
                    if len(wire) > 1024 * 1024:
                        raise ValueError("Oversized market message")
                    try:
                        message = json.loads(wire)
                    except ValueError:
                        persist({"channel": "malformed", "data": {"raw": wire}})
                        raise
                    if not isinstance(message, dict):
                        persist({"channel": "malformed", "data": {"raw": wire}})
                        raise ValueError("Invalid message envelope")
                    now = time.time_ns() // 1000000
                    try:
                        row = joined.feed(message, now)
                    except (ValueError, KeyError, TypeError) as error:
                        # Keep malformed evidence before resetting the connection and both caches.
                        persist(message)
                        raise ValueError("Malformed stream message") from error
                    if row is not None:
                        row["collection_session"] = connection_id
                    sequence += 1
                    store.save(session, sequence, now, message, row)
                    if message.get("channel") == "error":
                        raise ConnectionError("Server subscription error")
                    attempts = 0
            except (OSError, ValueError, KeyError, websocket.WebSocketException) as error:
                persist({"channel": "disconnected", "data": {"error": type(error).__name__}})
                attempts += 1
                # Interruptible, bounded exponential backoff; under connection-rate limit.
                retry_at = time.monotonic() + min(60, 2 ** min(attempts, 6))
                while not stopped and time.monotonic() < min(deadline, retry_at):
                    time.sleep(.1)
            finally:
                if socket is not None:
                    socket.close()
    finally:
        persist({"channel": "stopped", "data": {"elapsed_s": time.monotonic() - start}})
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--symbols", nargs="+", default=list(PRICE_UNITS))
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--max-age-ms", type=int, default=10000)
    parser.add_argument("--max-skew-ms", type=int, default=2000)
    args = parser.parse_args()
    StreamJoin(args.symbols, args.max_age_ms, args.max_skew_ms)
    if args.seconds is not None and not 0 < args.seconds <= 86400:
        parser.error("Finite capture must be within one day; omit for continuous operation")
    capture(str(args.database), args.symbols, args.seconds, args.max_age_ms, args.max_skew_ms)


if __name__ == "__main__":
    main()
