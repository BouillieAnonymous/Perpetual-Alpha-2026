"""Separate FULL/WAL ledger. Intents are durable BEFORE any network side effect."""
import json
import sqlite3
from pathlib import Path

APPLICATION_ID = 0x464C5631


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Ledger:
    def __init__(self, path, identity):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = path.exists()
        self.db = sqlite3.connect(path, timeout=20, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        if exists and (self.db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                       or self.db.execute('PRAGMA user_version').fetchone()[0] != 1):
            self.close()
            raise ValueError('Foreign or unsupported ledger; refusing adoption/migration')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS identity (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, time_ms INTEGER NOT NULL,
             kind TEXT NOT NULL, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS decisions (slot INTEGER PRIMARY KEY, time_ms INTEGER NOT NULL,
             value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS intents (cloid TEXT PRIMARY KEY, slot INTEGER NOT NULL,
             created_ms INTEGER NOT NULL, status TEXT NOT NULL, value TEXT NOT NULL,
             result TEXT, UNIQUE(slot, cloid));
          CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, generation INTEGER NOT NULL,
             pause_epoch INTEGER NOT NULL, action TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
        ''')
        existing = self.db.execute('SELECT value FROM identity').fetchone()
        if existing is None:
            if exists:
                self.close()
                raise ValueError('Existing ledger identity missing; do not adopt')
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('INSERT INTO identity VALUES (1,?)', (encode(identity),))
            self.db.execute('INSERT INTO state VALUES (1,?)', (encode({
                'paused': True, 'reason': 'not-started', 'generation': 0, 'pause_epoch': 0,
                'baseline': None, 'peak': None, 'expected_quantity': '0', 'last_tick_ms': None,
                'market_wait': None,
            }),))
            self.db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            self.db.execute('PRAGMA user_version=1')
            self.db.execute('COMMIT')
        elif existing[0] != encode(identity):
            self.close()
            raise ValueError('Ledger identity/policy mismatch; never relabel old state')

    def state(self):
        return json.loads(self.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])

    def update(self, **fields):
        own_transaction = not self.db.in_transaction
        if own_transaction:
            self.db.execute('BEGIN IMMEDIATE')
        try:
            s = self.state()
            s.update(fields)
            self.db.execute('UPDATE state SET value=? WHERE id=1', (encode(s),))
            if own_transaction:
                self.db.execute('COMMIT')
            return s
        except BaseException:
            if own_transaction:
                self.db.execute('ROLLBACK')
            raise

    def event(self, now, kind, value):
        self.db.execute('INSERT INTO events(time_ms,kind,value) VALUES (?,?,?)', (now, kind, encode(value)))

    def boot(self, now):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            s = self.state()
            self.update(paused=True, reason='startup-reconciliation', generation=s['generation'] + 1,
                        pause_epoch=s['pause_epoch'] + 1, last_tick_ms=max(now, s['last_tick_ms'] or 0),
                        market_wait=None)
            self.db.execute('UPDATE requests SET consumed=1')
            self.event(now, 'boot', self.state())
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def pause(self, now, reason):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.update(paused=True, reason=reason, pause_epoch=self.state()['pause_epoch'] + 1,
                        market_wait=None)
            self.event(now, 'paused', {'reason': reason})
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def request(self, action, generation):
        if action not in {'resume', 'flatten', 'cancel'}:
            raise ValueError('Unsupported operator request')
        self.db.execute('INSERT INTO requests(generation,pause_epoch,action) VALUES (?,?,?)',
                        (generation, self.state()['pause_epoch'], action))

    def take_request(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT * FROM requests WHERE consumed=0 ORDER BY id LIMIT 1').fetchone()
            if row:
                self.db.execute('UPDATE requests SET consumed=1 WHERE id=?', (row['id'],))
            self.db.execute('COMMIT')
            s = self.state()
            return dict(row) if row and row['generation'] == s['generation'] and row['pause_epoch'] == s['pause_epoch'] else None
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def resume(self, request, now):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            s = self.state()
            if request['generation'] != s['generation'] or request['pause_epoch'] != s['pause_epoch']:
                raise ValueError('Pause changed during resume checks; new request required')
            self.update(paused=False, reason='operator-resumed', market_wait=None)
            self.event(now, 'resumed', {'generation': s['generation'], 'pause_epoch': s['pause_epoch']})
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def set_spread_wait(self, now, waiting, *, reason='spread', detail=None):
        """Atomic transition that can NEVER clear a manual/hard pause."""
        self.db.execute('BEGIN IMMEDIATE')
        try:
            state = self.state()
            if state['paused']:
                self.db.execute('COMMIT')
                return False
            prior = state.get('market_wait')
            if waiting:
                value = {'reason': reason, 'since_ms': prior['since_ms'] if prior else now}
                if detail:
                    value['detail'] = detail
                if not prior or prior['reason'] != reason:
                    self.event(now, 'market-' + reason + '-wait', value)
                self.update(market_wait=value, reason='market-' + reason + '-wait', last_tick_ms=now)
            elif prior:
                self.event(now, 'market-' + prior['reason'] + '-recovered', {'since_ms': prior['since_ms']})
                self.update(market_wait=None, reason='market-' + prior['reason'] + '-recovered', last_tick_ms=now)
            self.db.execute('COMMIT')
            return True
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def reserve(self, slot, now, order, *, emergency=False):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            state = self.state()
            if (state['paused'] or state.get('market_wait')) and not emergency:
                raise ValueError('Paused or waiting before intent persistence')
            if self.pending():
                raise ValueError('Unresolved intent blocks new orders')
            self.db.execute('INSERT INTO intents VALUES (?,?,?,?,?,NULL)',
                            (order['cloid'], slot, now, 'unknown', encode(order)))
            self.event(now, 'intent', order)
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def pending(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM intents WHERE status='unknown'")]

    def settle(self, cloid, now, result, expected_quantity):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("UPDATE intents SET status='terminal',result=? WHERE cloid=?",
                            (encode(result), cloid))
            self.update(expected_quantity=str(expected_quantity))
            self.event(now, 'reconciled', {'cloid': cloid, **result})
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def decision(self, slot, now, value):
        # A failed/partial decision is NOT retried under a different ID in the same slot.
        self.db.execute('INSERT INTO decisions VALUES (?,?,?)', (slot, now, encode(value)))
        self.event(now, 'decision', {'slot': slot, **value})

    def decided(self, slot):
        return self.db.execute('SELECT 1 FROM decisions WHERE slot=?', (slot,)).fetchone() is not None

    def close(self):
        self.db.close()
