"""PAS capabilities, durable alerts and exchange-independent lifecycle acceptance."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from test_funding_live import NOW, FakeVenue, snapshot

from perpetual_alpha.funding_live.activation import require_approval
from perpetual_alpha.funding_live.engine import Engine
from perpetual_alpha.funding_live.leverage import configure
from perpetual_alpha.funding_live.ops import backup
from perpetual_alpha.funding_live.policy import Policy, dec
from perpetual_alpha.funding_live.preparation import Monitor, preflight, runtime_issues, send_mail
from perpetual_alpha.funding_live.store import Ledger
from perpetual_alpha.funding_live.venue import TestnetVenue


class PASLaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.p = Policy('pas', 'USDP', enabled=True)
        self.client = FakeVenue('pas')
        self.identity = {'schema': 'funding-exchange-ledger/1', 'dex': 'pas', 'policy_hash': self.p.digest}
        self.store = Ledger(self.root / 'live.sqlite3', self.identity)
        self.addCleanup(self.store.close)
        self.store.boot(NOW)
        self.engine = Engine(self.p, self.store, self.client, lambda: NOW)

    def resume(self):
        self.store.request('resume', self.store.state()['generation'])

    def test_pas_never_implicitly_sets_leverage(self):
        self.client.set_leverage = MagicMock(side_effect=AssertionError('must not sign leverage'))
        self.client.s['leverage']['value'] = 20
        self.resume()
        with self.assertRaisesRegex(ValueError, '1x'):
            self.engine.tick()
        self.client.set_leverage.assert_not_called()
        self.assertEqual(self.client.orders, [])

    def test_pas_fill_idempotence_and_pause_does_not_flatten(self):
        self.resume()
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(len(self.client.orders), 1)
        quantity = self.client.s['quantity']
        self.store.pause(NOW, 'operator')
        self.engine.tick()
        self.assertEqual(self.client.s['quantity'], quantity)
        self.assertEqual(len(self.client.orders), 1)

    def test_pas_unknown_fill_restart_reconciles_without_replay(self):
        self.client.network_unknown = True
        self.resume()
        with self.assertRaises(TimeoutError):
            self.engine.tick()
        self.store.boot(NOW)
        self.engine.reconcile()
        self.engine.tick()
        self.assertEqual(len(self.client.orders), 1)
        self.assertEqual(self.store.pending(), [])
        self.assertTrue(self.store.state()['paused'])

    def test_pas_explicit_flatten_reduces_only(self):
        self.resume()
        self.engine.tick()
        self.store.pause(NOW, 'operator')
        self.store.request('flatten', self.store.state()['generation'])
        self.engine.tick()
        self.assertEqual(dec(self.client.s['quantity']), 0)
        self.assertTrue(self.client.orders[-1]['reduce_only'])

    def test_pas_account_change_stops_and_partial_never_chased(self):
        self.resume()
        self.client.partial = True
        with self.assertRaises(ValueError):
            self.engine.tick()
        self.engine.tick()
        self.assertEqual(len(self.client.orders), 1)
        self.client.s['quantity'] = '1'
        with self.assertRaises(ValueError):
            self.engine.tick()

    def test_online_pas_backup_restores_pending_without_signing(self):
        self.store.update(paused=False)
        self.store.reserve(1, NOW, {'cloid': '0x' + 'a' * 32})
        result = backup(self.root / 'live.sqlite3', self.root / 'backup.sqlite3')
        self.assertEqual(result['pending_intents'], 1)
        restored = Ledger(self.root / 'backup.sqlite3', self.identity)
        try:
            restored.boot(NOW)
            self.assertTrue(restored.state()['paused'])
            self.assertEqual(len(restored.pending()), 1)
        finally:
            restored.close()

    def test_runtime_monitor_detects_paused_and_stopped(self):
        issues = runtime_issues(self.root / 'live.sqlite3', NOW + 181_000)
        self.assertTrue(any('paused' in x for x in issues))
        self.assertTrue(any('stopped' in x for x in issues))
        self.assertEqual(runtime_issues(self.root / 'absent.sqlite3', NOW), [])


class ApprovalAndAlertsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.p = Policy('pas', 'USDP')
        self.client = SimpleNamespace(team='0x' + '1' * 40,
                                      settings={'API_WALLET_ADDRESS': '0x' + '2' * 40})
        self.par = self.root / 'par.txt'
        self.par.write_text('0x' + '3' * 40)
        self.path = self.root / 'approval.json'
        self.approval = {'schema': 'pas-operator-approval/1', 'purpose': 'trading', 'dex': 'pas',
                         'team_wallet': self.client.team, 'api_wallet': self.client.settings['API_WALLET_ADDRESS'],
                         'policy_hash': self.p.digest, 'not_before_ms': NOW - 1, 'expires_ms': NOW + 1000}

    def check(self):
        self.path.write_text(json.dumps(self.approval))
        return require_approval(self.path, 'trading', self.p, self.client, NOW, self.par)

    def test_approval_binds_account_policy_purpose_and_time(self):
        self.assertEqual(self.check()['purpose'], 'trading')
        for name, value in [('purpose', 'leverage'), ('policy_hash', 'bad'),
                            ('expires_ms', NOW), ('not_before_ms', NOW + 1),
                            ('team_wallet', '0x' + '4' * 40), ('expires_ms', NOW + 86_400_001)]:
            old = self.approval[name]
            self.approval[name] = value
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.check()
            self.approval[name] = old

    def test_shared_par_wallet_and_missing_approval_fail_closed(self):
        with self.assertRaises(ValueError):
            require_approval(None, 'trading', self.p, self.client, NOW, self.par)
        self.par.write_text(self.client.settings['API_WALLET_ADDRESS'])
        with self.assertRaisesRegex(ValueError, 'separate'):
            self.check()

    def test_pas_signing_denied_before_key_access(self):
        client = TestnetVenue.__new__(TestnetVenue)
        client.policy = Policy('pas', 'USDP', enabled=True)
        client.action_guard = None
        with patch('os.environ.get', side_effect=AssertionError('must not read key')):
            with self.assertRaisesRegex(ValueError, 'approval'):
                client.enable_signer()

    def test_trading_capability_cannot_change_pas_leverage(self):
        client = TestnetVenue.__new__(TestnetVenue)
        client.policy, client.signing_scope = self.p, 'trading'
        with self.assertRaisesRegex(ValueError, 'separately'):
            client.set_leverage()

    def test_leverage_readback_rejects_unconfirmed_setting(self):
        client = MagicMock()
        client.query.return_value = {'leverage': {'type': 'cross', 'value': 20}}
        with patch('perpetual_alpha.funding_live.leverage.preflight', return_value={'issues': []}):
            with self.assertRaisesRegex(ValueError, 'confirm'):
                configure(client, self.p, self.path, self.par)
        client.enable_signer.assert_called_once_with(scope='leverage-only')

    def test_preflight_is_unsigned_and_not_activation_approval(self):
        client = MagicMock(team=self.client.team, settings=self.client.settings)
        client.snapshot.return_value = snapshot('pas')
        client.query.side_effect = lambda kind, **kwargs: ([{'address': self.client.settings['API_WALLET_ADDRESS'],
                                                          'validUntil': NOW + 100000}] if kind == 'extraAgents'
                                                         else {'role': 'user'})
        with patch('perpetual_alpha.funding_live.preparation.millis', return_value=NOW):
            result = preflight(client, self.p, self.par)
        self.assertTrue(result['public_checks_passed'])
        self.assertFalse(result['ready_to_activate'])
        client.enable_signer.assert_not_called()

    def test_durable_outbox_dedup_recovery_and_delivery_failure(self):
        path = self.root / 'monitor.sqlite3'
        mon = Monitor(path)
        value = {'time_ms': NOW, 'issues': ['failed']}
        mon.record(value)
        mon.record(value)
        self.assertEqual(mon.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 1)
        self.assertEqual(mon.deliver(None), 'unconfigured')
        with patch('perpetual_alpha.funding_live.preparation.send_mail', side_effect=RuntimeError('secret')):
            self.assertEqual(mon.deliver({}), 'delivery-failed')
        mon.close()
        mon = Monitor(path)
        try:
            mon.record({'time_ms': NOW + 1, 'issues': []})
            self.assertEqual(mon.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 2)
            with patch('perpetual_alpha.funding_live.preparation.send_mail'):
                self.assertEqual(mon.deliver({}), 'smtp-accepted')
        finally:
            mon.close()

    def test_monitor_refuses_existing_foreign_database(self):
        path = self.root / 'foreign.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE raw(value TEXT)')
        db.close()
        with self.assertRaises(ValueError):
            Monitor(path)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('PRAGMA application_id').fetchone()[0], 0)
        db.close()

    def test_smtp_requires_tls_and_uses_confirmed_recipient(self):
        config = {'host': 'smtp.example.invalid', 'port': 587,
                  'sender': 'sender@example.invalid', 'recipient': 'recipient@example.invalid'}
        with patch('smtplib.SMTP') as factory:
            send_mail(config, 'incident')
            smtp = factory.return_value.__enter__.return_value
            smtp.starttls.assert_called_once()
            message = smtp.send_message.call_args.args[0]
            self.assertEqual(message['To'], config['recipient'])
        with self.assertRaises(ValueError):
            send_mail({**config, 'port': 25}, 'incident')
