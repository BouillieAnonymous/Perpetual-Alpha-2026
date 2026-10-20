"""PAS public-only preparation, durable incident outbox and independent heartbeat.

This module never loads a signer. SMTP credentials, when configured, belong to
the monitor alone. Stopping or pausing a trader never closes its positions.
"""
import argparse
import hashlib
import json
import signal
import smtplib
import sqlite3
import ssl
import threading
from email.message import EmailMessage
from pathlib import Path

from .activation import exclusive_wallet
from .cli import report
from .policy import dec, funding_signal, load_policy, make_order, target_quantity, validate_snapshot
from .venue import TestnetVenue, millis


def preflight(client, policy, par_wallet_file, *, expected_quantity='0'):
    result = {'schema': 'pas-preflight/1', 'time_ms': millis(), 'signing': False,
              'ready_to_activate': False, 'issues': [], 'checks': {}}
    checks, issues = result['checks'], result['issues']
    try:
        exclusive_wallet(client, par_wallet_file)
        checks['exclusive_signer'] = True
    except ValueError as exc:
        issues.append(str(exc))
    try:
        wallet = client.settings.get('API_WALLET_ADDRESS', '').lower()
        agents = client.query('extraAgents', user=client.team)
        checks['authorized_public_wallet'] = any(
            a['address'].lower() == wallet and a['validUntil'] > millis() + 60_000 for a in agents)
        checks['account_role'] = client.query('userRole', user=client.team)
        if not checks['authorized_public_wallet']:
            issues.append('Public API wallet authorization absent/expired')
        s = client.snapshot()
        result['snapshot'] = s
        validate_snapshot(s, policy, millis(), require_leverage=False)
        if dec(s['quantity']) != dec(expected_quantity):
            raise ValueError('PAS position differs from isolated ledger; never adopt external activity')
        if dec(s['free']) < dec(s['equity']) * dec(policy.min_free_fraction):
            raise ValueError('Free collateral below required buffer')
        checks['market_account_without_leverage'] = True
        checks['signal'] = funding_signal(s['funding'], policy.symbol, millis())
        # This preview is explicitly conditional, never evidence that the
        # exchange has changed leverage and never an instruction to submit.
        at_one = {**s, 'leverage': {'type': 'cross', 'value': 1}}
        checks['conditional_preview_assuming_1x'] = make_order(
            at_one, target_quantity(at_one, checks['signal'], policy), policy, millis())
        checks['leverage_1x_cross'] = s['leverage'] == {'type': 'cross', 'value': 1}
        if not checks['leverage_1x_cross']:
            issues.append('PAS ETH leverage is not verified 1x Cross; separate authorization required')
        else:
            if dec(s['quantity']) == 0:
                checks['unsigned_order_preview'] = make_order(
                    s, target_quantity(s, checks['signal'], policy), policy, millis())
    except Exception as exc:
        issues.append(str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
    # Public checks cannot attest private signer ownership, signed acceptance,
    # independent notification delivery or the operator's activation approval.
    result['public_checks_passed'] = not issues
    result['pending_activation_requirements'] = [
        'Explicit separate leverage approval if leverage differs',
        'Dedicated PAS signer ownership and fresh trading approval',
        'Verified external notification delivery',
        'Fresh pre-order depth/margin checks and first signed exchange acceptance']
    return result


class Monitor:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        app = self.db.execute('PRAGMA application_id').fetchone()[0]
        names = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if app != 0x5041534D and (app or names):
            self.db.close()
            raise ValueError('Monitor refuses a foreign database')
        self.db.executescript('''PRAGMA application_id=1346458445;
            PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS status(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT);
            CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY, time_ms INTEGER,
                value TEXT, delivered_ms INTEGER, attempts INTEGER NOT NULL DEFAULT 0);
        ''')
        self.db.commit()

    def record(self, value):
        previous = self.db.execute('SELECT value FROM status WHERE id=1').fetchone()
        fingerprint = hashlib.sha256(json.dumps(value['issues'], sort_keys=True).encode()).hexdigest()
        value = {**value, 'incident_fingerprint': fingerprint}
        old = json.loads(previous[0]) if previous else None
        with self.db:
            if old is None or old['incident_fingerprint'] != fingerprint:
                alert = {'time_ms': value['time_ms'], 'issues': value['issues'],
                         'signing': False, 'position_warning': 'Stop/pause does not flatten positions'}
                self.db.execute('INSERT INTO outbox(time_ms,value) VALUES(?,?)',
                                (value['time_ms'], json.dumps(alert)))
            self.db.execute('INSERT OR REPLACE INTO status VALUES(1,?)', (json.dumps(value),))

    def deliver(self, config):
        if config is None:
            return 'unconfigured'
        row = self.db.execute('SELECT id,value FROM outbox WHERE delivered_ms IS NULL ORDER BY id LIMIT 1').fetchone()
        if row is None:
            return 'idle'
        with self.db:
            self.db.execute('UPDATE outbox SET attempts=attempts+1 WHERE id=?', (row[0],))
        try:
            send_mail(config, row[1])
        except Exception:
            # Never persist SMTP exceptions: they may contain credentials.
            return 'delivery-failed'
        with self.db:
            self.db.execute('UPDATE outbox SET delivered_ms=? WHERE id=?', (millis(), row[0]))
        return 'smtp-accepted'  # Acceptance by SMTP is not proof of inbox delivery.

    def close(self):
        self.db.close()


def send_mail(config, body):
    if config['port'] not in (465, 587) or not config['recipient'] or not config['sender']:
        raise ValueError('Secure SMTP port, sender and recipient required')
    message = EmailMessage()
    message['From'], message['To'] = config['sender'], config['recipient']
    message['Subject'] = 'PAS strategy health / activation readiness'
    message.set_content(body)
    context = ssl.create_default_context()
    factory = smtplib.SMTP_SSL if config['port'] == 465 else smtplib.SMTP
    options = {'context': context} if config['port'] == 465 else {}
    with factory(config['host'], config['port'], timeout=15, **options) as smtp:
        if config['port'] == 587:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        if config.get('username'):
            smtp.login(config['username'], config['password'])
        smtp.send_message(message)


def runtime_issues(path, now):
    if not path.is_file():
        return []  # Expected while waiting for first activation.
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        identity = json.loads(db.execute('SELECT value FROM identity WHERE id=1').fetchone()[0])
        if identity.get('schema') != 'funding-exchange-ledger/1' or identity.get('dex') != 'pas':
            return ['Foreign trading ledger; refusing adoption']
        state = report(db)
        issues = []
        if state['paused']:
            issues.append('PAS trader paused; position NOT automatically flattened')
        elif state.get('market_wait'):
            issues.append('PAS trader waiting for spread; automatic recheck, position not flattened')
        if not state['last_tick_ms'] or now - state['last_tick_ms'] > 180_000:
            issues.append('PAS trader heartbeat stale/stopped; position NOT automatically flattened')
        if state['intent_counts'].get('unknown', 0):
            issues.append('PAS order intent unresolved; manual reconciliation required')
        return issues
    finally:
        db.close()


def expected_position(path, policy, team):
    if not path.is_file():
        return '0'
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        identity = json.loads(db.execute('SELECT value FROM identity WHERE id=1').fetchone()[0])
        # The activated ledger is bound to the enabled policy, while the monitor
        # deliberately keeps its own disabled policy and never loads a key.
        from dataclasses import replace
        if (identity.get('schema') != 'funding-exchange-ledger/1' or identity.get('dex') != 'pas'
                or identity.get('team') != team
                or identity.get('policy_hash') != replace(policy, enabled=True).digest):
            raise ValueError('Trading ledger policy mismatch')
        return report(db)['expected_quantity']
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['preflight', 'watch', 'health'])
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--env-file', type=Path, default=Path('/run/public.env'))
    parser.add_argument('--par-wallet-file', type=Path, default=Path('/run/par-wallet.txt'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--smtp-file', type=Path)
    args = parser.parse_args(argv)
    p = load_policy(args.config)
    if p.dex != 'pas' or p.enabled:
        raise ValueError('Preparation requires disabled PAS policy')
    database = args.root / 'monitor.sqlite3'
    if args.command == 'health':
        db = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            value = json.loads(db.execute('SELECT value FROM status WHERE id=1').fetchone()[0])
            pending = db.execute('SELECT COUNT(*) FROM outbox WHERE delivered_ms IS NULL').fetchone()[0]
            fresh = 0 <= millis() - value['time_ms'] < 180_000
            print(json.dumps({'monitor_alive': fresh, 'ready_to_activate': False,
                              'issues': value['issues'], 'undelivered_alerts': pending,
                              'mail_status': value.get('mail_status', 'unconfigured')}))
            return 0 if fresh else 1
        finally:
            db.close()
    client = TestnetVenue(p, args.env_file)
    try:
        if args.command == 'preflight':
            print(json.dumps(preflight(client, p, args.par_wallet_file)))
            return 0
        args.root.mkdir(parents=True, exist_ok=True)
        monitor = Monitor(database)
        stopped = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stopped.set())
        try:
            while not stopped.is_set():
                runtime = args.root / 'pas' / 'funding.sqlite3'
                try:
                    quantity = expected_position(runtime, p, client.team)
                    value = preflight(client, p, args.par_wallet_file, expected_quantity=quantity)
                except Exception:
                    value = {'schema': 'pas-preflight/1', 'time_ms': millis(), 'signing': False,
                             'ready_to_activate': False, 'issues': ['Trading ledger identity unreadable/mismatched']}
                try:
                    value['issues'].extend(runtime_issues(runtime, millis()))
                except Exception:
                    value['issues'].append('Trading ledger unreadable')
                config = None
                if args.smtp_file and args.smtp_file.is_file():
                    try:
                        config = json.loads(args.smtp_file.read_text())
                        recipient = args.smtp_file.with_name('recipient.txt').read_text().strip()
                        if config.get('recipient') != recipient:
                            raise ValueError('Recipient mismatch')
                    except Exception:
                        config = None
                        value['issues'].append('SMTP configuration unreadable')
                if config is None:
                    value['issues'].append('External email delivery unconfigured')
                monitor.record(value)
                mail_status = monitor.deliver(config)
                # Persist status without generating another incident.
                value['mail_status'] = mail_status
                monitor.record(value)
                print(json.dumps({'signing': False, 'issues': value['issues'], 'mail_status': mail_status}), flush=True)
                stopped.wait(p.poll_seconds)
        finally:
            monitor.close()
    finally:
        client.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
