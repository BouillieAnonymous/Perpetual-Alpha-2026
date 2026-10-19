"""Independent public REST collection. No wallet, account queries or signed actions."""
import argparse
import hashlib
import json
import signal
import sqlite3
import threading
import time
from pathlib import Path

import requests

from .runtime.process_lock import ProcessLock
from .venues import venue

URL = 'https://api.hyperliquid-testnet.xyz/info'
APPLICATION_ID = 0x56434C31


def now_ms():
    return time.time_ns() // 1_000_000


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def book_quality(symbol, book, received_ms, max_age_ms=90_000):
    """Empty/one-sided books are evidence, never fabricated executable quotes."""
    try:
        from math import isfinite
        if book['coin'] != symbol:
            return 'wrong-market'
        if type(book['time']) is not int or not 0 <= received_ms - book['time'] <= max_age_ms:
            return 'stale-or-future'
        levels = book['levels']
        if len(levels) != 2 or not all(levels):
            return 'empty-or-one-sided'
        bid, ask = float(levels[0][0]['px']), float(levels[1][0]['px'])
        if not all(isfinite(x) for x in (bid, ask)) or not 0 < bid <= ask:
            return 'invalid-or-crossed'
        return 'ready'
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'malformed'


class Capture:
    def __init__(self, path, dex):
        self.selected = venue(dex)
        path = Path(path).absolute()
        if path.resolve() != path or path.is_symlink():
            raise ValueError('Collection database aliases are forbidden')
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = path.exists()
        self.db = sqlite3.connect(path, isolation_level=None, timeout=15)
        if exists and (self.db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                       or self.db.execute('PRAGMA user_version').fetchone()[0] != 1):
            self.close()
            raise ValueError('Foreign database; do not adopt Runtime/Paper/old collector data')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS identity (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS observations (
            seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, symbol TEXT,
            requested_ms INTEGER NOT NULL, received_ms INTEGER NOT NULL, stored_ms INTEGER NOT NULL,
            event_ms INTEGER, quality TEXT NOT NULL, request_json TEXT NOT NULL,
            raw_json TEXT NOT NULL, sha256 TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS observations_lookup ON observations(kind,symbol,seq);
          CREATE TABLE IF NOT EXISTS heartbeat (id INTEGER PRIMARY KEY CHECK(id=1), time_ms INTEGER NOT NULL,
            error TEXT);
        ''')
        identity = canonical({'schema': 'public-venue-collection/1', 'source': self.selected.source,
                              'dex': dex, 'collateral': self.selected.collateral,
                              'symbols': self.selected.symbols, 'endpoint': URL})
        existing = self.db.execute('SELECT value FROM identity WHERE id=1').fetchone()
        if existing is None and not exists:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('INSERT INTO identity VALUES (1,?)', (identity,))
            self.db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            self.db.execute('PRAGMA user_version=1')
            self.db.execute('COMMIT')
        elif existing is None or existing[0] != identity:
            self.close()
            raise ValueError('Stored collection environment mismatch')

    def append(self, kind, symbol, request, raw, start, received, quality, event=None):
        if symbol is not None:
            self.selected.validate_symbol(symbol)
        if start > received:
            raise ValueError('Clock rollback during public request')
        if request.get('dex', self.selected.dex) != self.selected.dex:
            raise ValueError('Request dex mismatch')
        if request.get('coin', symbol) != symbol:
            raise ValueError('Request symbol mismatch')
        payload = canonical(raw)
        self.db.execute('INSERT INTO observations VALUES(NULL,?,?,?,?,?,?,?,?,?,?)',
                        (kind, symbol, start, received, now_ms(), event, quality,
                         canonical(request), payload, hashlib.sha256(payload.encode()).hexdigest()))

    def heartbeat(self, error=None):
        self.db.execute('INSERT INTO heartbeat VALUES(1,?,?) ON CONFLICT(id) DO UPDATE '
                        'SET time_ms=excluded.time_ms,error=excluded.error', (now_ms(), error))

    def close(self):
        self.db.close()


class Collector:
    def __init__(self, store, *, session=None, clock=now_ms):
        self.store = store
        self.selected = store.selected
        self.session = session or requests.Session()
        self.clock = clock
        self.next_funding = 0

    def read(self, kind, symbol, request, quality=None):
        started = self.clock()
        try:
            response = self.session.post(URL, json=request, timeout=(5, 15))
            response.raise_for_status()
            if len(response.content) > 4_000_000:
                raise ValueError('Oversize public response')
            raw = response.json()
            received = self.clock()
            result = quality(raw, received) if quality else 'observed'
            event = raw.get('time') if isinstance(raw, dict) and type(raw.get('time')) is int else None
            self.store.append(kind, symbol, request, raw, started, received, result, event)
            return raw, received, result
        except Exception as exc:
            # Error bodies may be arbitrarily large; archive bounded exception class, never credentials.
            received = self.clock()
            self.store.append('request-error', symbol, request, {'error': type(exc).__name__},
                              started, max(started, received), 'request-error')
            return None, received, 'request-error'

    def cycle(self):
        selected = self.selected
        def context_quality(raw, _):
            try:
                meta, contexts = raw
                names = [m['name'] for m in meta['universe']]
                if len(names) != len(contexts) or len(names) != len(set(names)):
                    return 'invalid-context'
                if not set(selected.symbols) <= set(names):
                    return 'missing-market'
                if any(not name.startswith(selected.dex + ':') for name in names):
                    return 'wrong-market'
                return 'observed'
            except (KeyError, TypeError, ValueError):
                return 'invalid-context'
        raw, received, quality = self.read('metadata-context', None,
                                         {'type': 'metaAndAssetCtxs', 'dex': selected.dex}, context_quality)
        if raw is None or quality != 'observed':
            self.store.heartbeat(quality)
            return
        meta, contexts = raw
        mapping = dict(zip((m['name'] for m in meta['universe']), contexts))
        for symbol in selected.symbols:
            # Always persist new contexts even if the book did not change, or is empty.
            self.store.append('context', symbol, {'type': 'metaAndAssetCtxs', 'dex': selected.dex},
                              {'context': mapping[symbol], 'parent_received_ms': received},
                              received, received, 'observed')
            self.read('book', symbol, {'type': 'l2Book', 'coin': symbol},
                      lambda b, t, s=symbol: book_quality(s, b, t))
        if self.clock() >= self.next_funding:
            for symbol in selected.symbols:
                end = self.clock()
                def funding_quality(rows, _, s=symbol):
                    if not isinstance(rows, list) or any(not isinstance(r, dict) or r.get('coin') != s
                                                         or type(r.get('time')) is not int for r in rows):
                        return 'invalid-funding'
                    return 'observed'
                self.read('settled-funding', symbol, {'type': 'fundingHistory', 'coin': symbol,
                          'startTime': end - 3_600_000, 'endTime': end}, funding_quality)
            # Overlap one-hour windows. Raw observations are intentionally not deduplicated.
            self.next_funding = self.clock() + 1_800_000
        self.store.heartbeat()


def status(path, dex):
    selected = venue(dex)
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=15)
    try:
        identity = json.loads(db.execute('SELECT value FROM identity').fetchone()[0])
        if identity['source'] != selected.source:
            raise ValueError('Wrong collection environment')
        heartbeat = db.execute('SELECT time_ms,error FROM heartbeat').fetchone()
        now = now_ms()
        alive = bool(heartbeat and 0 <= now - heartbeat[0] <= 300_000)
        markets = {}
        for symbol in selected.symbols:
            row = db.execute("SELECT received_ms,event_ms,quality FROM observations WHERE kind='book' "
                             'AND symbol=? ORDER BY seq DESC LIMIT 1', (symbol,)).fetchone()
            count = db.execute("SELECT COUNT(*) FROM observations WHERE kind='book' AND symbol=?",
                               (symbol,)).fetchone()[0]
            context = db.execute("SELECT MAX(received_ms) FROM observations WHERE kind='context' AND symbol=?",
                                 (symbol,)).fetchone()[0]
            markets[symbol] = {'books': count, 'last_book': list(row) if row else None,
                               'context_age_ms': now-context if context else None,
                               'quote_ready': bool(row and row[2] == 'ready' and row[1] is not None
                                                   and 0 <= now-row[1] <= 90_000)}
        result = {'source': selected.source, 'signing': False, 'alive': alive,
                  'heartbeat': heartbeat, 'markets': markets,
                  'observations': db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]}
        return result
    finally:
        db.close()


def next_poll_deadline(previous, completed, interval):
    """Fixed cadence, excluding request latency; skip missed slots without catch-up bursts."""
    return previous + max(1, int((completed - previous) // interval) + 1) * interval


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('collect', 'status'))
    parser.add_argument('--system', choices=('par', 'pas'), required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--poll-seconds', type=int, default=60)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.command == 'status':
        result = status(args.database, args.system)
        print(json.dumps(result), flush=True)
        return 0 if result['alive'] else 1
    if not 10 <= args.poll_seconds <= 300:
        raise ValueError('Public collector poll must be between 10 and 300 seconds')
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    with ProcessLock(str(args.database) + '.lock'):
        store = Capture(args.database, args.system)
        collector = Collector(store)
        try:
            deadline = time.monotonic()
            while not stopped.is_set():
                collector.cycle()
                print(json.dumps({'dex': args.system, 'time_ms': now_ms(), 'signing': False}), flush=True)
                if args.once:
                    break
                deadline = next_poll_deadline(deadline, time.monotonic(), args.poll_seconds)
                stopped.wait(max(0, deadline - time.monotonic()))
        finally:
            collector.session.close()
            store.heartbeat('stopped')
            store.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
