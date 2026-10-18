"""python -m perpetual_alpha.funding_live: inspect, preflight, run, status, pause/resume/flatten/cancel."""
import argparse
import json
import signal
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path

from ..runtime.process_lock import ProcessLock
from .engine import Engine
from .policy import VERSION, load_policy, validate_snapshot
from .store import Ledger
from .venue import TestnetVenue, millis


def paths(root, dex):
    base = Path(root).absolute()
    folder = base / dex
    # Reject aliases before creating the state directory; old namespaces cannot be adopted.
    for path in (base, folder, folder / 'funding.sqlite3', folder / 'process.lock'):
        if path.resolve() != path or path.is_symlink():
            raise ValueError('State directory aliases are forbidden')
    return folder, folder / 'funding.sqlite3'


def report(db):
    db.row_factory = sqlite3.Row
    state = json.loads(db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])
    intent_rows = db.execute('SELECT status,COUNT(*) AS n FROM intents GROUP BY status').fetchall()
    last = db.execute('SELECT kind,time_ms FROM events ORDER BY id DESC LIMIT 1').fetchone()
    return {**state, 'intent_counts': {r['status']: r['n'] for r in intent_rows},
            'decisions': db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0],
            'last_event': dict(last) if last else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('inspect', 'preflight', 'run', 'status', 'pause',
                                           'resume', 'flatten', 'cancel', 'health'))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=Path('data/funding'))
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--bootstrap', action='store_true', help='One initial current-time PAR decision')
    parser.add_argument('--generation', type=int, help='Required current boot generation for operator request')
    args = parser.parse_args(argv)
    p = load_policy(args.config)
    folder, database = paths(args.root, p.dex)
    if args.command == 'inspect':
        print(json.dumps({'policy': asdict(p), 'policy_hash': p.digest, 'strategy': VERSION,
                          'database': str(database), 'signing': False}))
        return 0
    if args.command in {'status', 'health', 'pause', 'resume', 'flatten', 'cancel'}:
        # Existing DB only; operator commands must never initialize or adopt a foreign ledger.
        db = sqlite3.connect(database.as_uri() + '?mode=rw', uri=True, timeout=20, isolation_level=None)
        try:
            identity = json.loads(db.execute('SELECT value FROM identity').fetchone()[0])
            if identity['policy_hash'] != p.digest or identity['dex'] != p.dex:
                raise ValueError('Operator policy/environment mismatch')
            if args.command in {'pause', 'resume', 'flatten', 'cancel'}:
                db.execute('BEGIN IMMEDIATE')
                s = report(db)
                if args.generation != s['generation']:
                    raise ValueError('Use current boot generation from status')
                if args.command == 'pause':
                    state = json.loads(db.execute('SELECT value FROM state').fetchone()[0])
                    state.update(paused=True, reason='operator-pause', pause_epoch=state['pause_epoch'] + 1)
                    db.execute('UPDATE state SET value=? WHERE id=1', (json.dumps(state),))
                else:
                    if args.command in {'resume', 'flatten', 'cancel'} and not p.enabled:
                        raise ValueError('Environment disabled')
                    db.execute('INSERT INTO requests(generation,pause_epoch,action) VALUES (?,?,?)',
                               (args.generation, s['pause_epoch'], args.command))
                db.execute('INSERT INTO events(time_ms,kind,value) VALUES (?,?,?)',
                           (millis(), 'operator-request', json.dumps({'action': args.command,
                                                                   'generation': args.generation})))
                db.execute('COMMIT')
            result = report(db)
            print(json.dumps(result))
            if args.command == 'health':
                age = millis() - (result['last_tick_ms'] or 0)
                return 0 if 0 <= age < p.poll_seconds * 3000 else 1
            return 0
        finally:
            db.close()
    if args.command == 'run' and (not args.execute or not p.enabled):
        raise ValueError('Run requires enabled environment AND explicit --execute')
    if args.bootstrap and p.dex != 'par':
        raise ValueError('Initial bootstrap allowed only for PAR rehearsal')
    client = TestnetVenue(p, args.env_file)
    try:
        if args.command == 'preflight':
            snapshot = client.snapshot()
            folder.mkdir(parents=True, exist_ok=True)
            evidence = folder / f'preflight-{millis()}.json'
            evidence.write_text(json.dumps(snapshot, sort_keys=True, indent=2), encoding='utf-8')
            checks = {}
            for name, leverage in [('data_account', False), ('ready_at_1x', True)]:
                try:
                    validate_snapshot(snapshot, p, millis(), require_leverage=leverage)
                    checks[name] = 'passed'
                except ValueError as exc:
                    checks[name] = str(exc)
            print(json.dumps({'dex': p.dex, 'checks': checks, 'enabled': p.enabled,
                              'funding_rows': len(snapshot['funding']), 'evidence': str(evidence),
                              'signing': False}))
            return 0 if checks['data_account'] == 'passed' else 2
        identity = {'schema': 'funding-exchange-ledger/1', 'dex': p.dex, 'collateral': p.collateral,
                    'team': client.team, 'policy_hash': p.digest, 'strategy': VERSION}
        folder.mkdir(parents=True, exist_ok=True)
        with ProcessLock(args.root.absolute() / 'signer-process.lock'), ProcessLock(folder / 'process.lock'):
            store = Ledger(database, identity)
            try:
                store.boot(millis())
                engine = Engine(p, store, client, millis)
                # Startup reconciliation is public and occurs before loading any signing material.
                try:
                    engine.reconcile()
                    engine.account_guard(engine.observe(leverage=False))
                except Exception as exc:
                    reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                    store.pause(millis(), 'startup: ' + reason)
                client.enable_signer()
                stopped = threading.Event()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    signal.signal(sig, lambda *_: stopped.set())
                print(json.dumps({'status': 'started-paused', 'generation': store.state()['generation'],
                                  'dex': p.dex}), flush=True)
                while not stopped.is_set():
                    try:
                        has_decisions = store.db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]
                        engine.tick(bootstrap=args.bootstrap and has_decisions == 0)
                    except Exception as exc:
                        # Only local validation text is allowed; SDK/network errors may carry signed payloads.
                        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                        store.pause(millis(), reason)
                        store.update(last_tick_ms=max(millis(), store.state()['last_tick_ms'] or 0))
                        print(json.dumps({'status': 'paused', 'reason': reason}), flush=True)
                    stopped.wait(p.poll_seconds)
                store.pause(millis(), 'process-stopped; position not implicitly flattened')
            finally:
                store.close()
    finally:
        client.close()
    return 0
