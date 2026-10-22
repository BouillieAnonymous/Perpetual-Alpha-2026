"""Standing consent never bypasses grant duration, health or notification evidence."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_funding_live import NOW, snapshot

from perpetual_alpha.funding_live.policy import Policy
from perpetual_alpha.funding_live.preparation import Monitor
from perpetual_alpha.funding_live.renewal import DAY, LEAD, maybe_renew, recover_notice
from perpetual_alpha.funding_live.store import Ledger


class RenewalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.p = Policy('pas', 'USDP', enabled=True)
        self.client = SimpleNamespace(team='0x'+'1'*40, settings={'API_WALLET_ADDRESS': '0x'+'2'*40})
        self.client.query = Mock(return_value=[{'address': '0x'+'2'*40, 'validUntil': NOW+2*DAY}])
        self.par = self.root/'par.txt'
        self.par.write_text('0x'+'3'*40)
        self.approval = self.root/'trading.json'
        self.old = {'schema': 'pas-operator-approval/1', 'purpose': 'trading', 'dex': 'pas',
                    'team_wallet': self.client.team, 'api_wallet': '0x'+'2'*40,
                    'policy_hash': self.p.digest, 'not_before_ms': NOW-DAY+1000, 'expires_ms': NOW+1000}
        self.approval.write_text(json.dumps(self.old))
        self.permission = self.root/'standing.json'
        self.consent = {**self.old, 'schema': 'pas-standing-trading/1', 'enabled': True,
                        'not_before_ms': NOW-1, 'duration_ms': DAY, 'renew_before_ms': LEAD}
        del self.consent['expires_ms']
        self.permission.write_text(json.dumps(self.consent))
        self.ledger = Ledger(self.root/'live.sqlite3', {'schema': 'funding-exchange-ledger/1',
            'dex': 'pas', 'team': self.client.team, 'policy_hash': self.p.digest})
        self.addCleanup(self.ledger.close)
        self.ledger.boot(NOW)
        self.ledger.update(paused=False, baseline='1000', peak='1000')
        self.mon = Monitor(self.root/'monitor.sqlite3')
        self.addCleanup(self.mon.close)
        self.evidence = {'time_ms': NOW, 'issues': [], 'snapshot': snapshot('pas')}

    def run_renewal(self):
        return maybe_renew(self.permission, self.approval, self.root/'live.sqlite3', self.p,
                           self.client, self.par, self.mon, self.evidence, {}, NOW)

    def test_healthy_renewal_and_one_durable_notice(self):
        self.assertTrue(self.run_renewal())
        new = json.loads(self.approval.read_text())
        self.assertEqual(new['expires_ms']-new['not_before_ms'], DAY)
        self.assertEqual(new['policy_hash'], self.old['policy_hash'])
        self.assertFalse(self.run_renewal())
        self.assertEqual(self.mon.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 1)
        with patch('perpetual_alpha.funding_live.preparation.send_mail') as send:
            self.assertEqual(self.mon.deliver({}), 'smtp-accepted')
        self.assertEqual(json.loads(send.call_args.args[1])['event'], 'trading-approval-renewed')

    def test_expired_does_not_renew_or_resume(self):
        self.old.update(expires_ms=NOW)
        self.approval.write_text(json.dumps(self.old))
        with self.assertRaises(ValueError):
            self.run_renewal()
        self.assertEqual(json.loads(self.approval.read_text()), self.old)

    def test_health_risk_and_pause_blocks(self):
        for fields in [{'paused': True}, {'market_wait': {'reason': 'network'}},
                       {'expected_quantity': '.1'}, {'peak': '1010'}, {'last_tick_ms': NOW-180001}]:
            with self.subTest(fields=fields):
                old = self.ledger.state()
                self.ledger.update(**fields)
                with self.assertRaises(ValueError):
                    self.run_renewal()
                self.ledger.update(**old)
        self.assertEqual(json.loads(self.approval.read_text()), self.old)

    def test_bad_market_and_public_checks_block(self):
        for field, val in [('issues', ['HTTPError']), ('time_ms', NOW-180001)]:
            old = self.evidence[field]
            self.evidence[field] = val
            with self.assertRaises(ValueError):
                self.run_renewal()
            self.evidence[field] = old
        self.evidence['snapshot']['leverage']['value'] = 20
        with self.assertRaises(ValueError):
            self.run_renewal()

    def test_revoked_or_short_exchange_authorization_blocks(self):
        self.client.query.return_value[0]['validUntil'] = NOW+1000
        with self.assertRaises(ValueError):
            self.run_renewal()

    def test_permission_revocation_policy_and_long_duration_block(self):
        for fields in [{'enabled': False}, {'duration_ms': 2*DAY}, {'policy_hash': 'wrong'}]:
            self.permission.write_text(json.dumps({**self.consent, **fields}))
            if fields.get('enabled') is False:
                self.assertFalse(self.run_renewal())
            else:
                with self.assertRaises(ValueError):
                    self.run_renewal()

    def test_pending_intent_or_operator_request_blocks(self):
        self.ledger.request('resume', self.ledger.state()['generation'])
        with self.assertRaises(ValueError):
            self.run_renewal()

    def test_unknown_order_blocks(self):
        self.ledger.reserve(1, NOW, {'cloid': 'unknown'})
        with self.assertRaises(ValueError):
            self.run_renewal()

    def test_unconfigured_smtp_blocks(self):
        with self.assertRaises(ValueError):
            maybe_renew(self.permission, self.approval, self.root/'live.sqlite3', self.p,
                        self.client, self.par, self.mon, self.evidence, None, NOW)

    def test_notice_journal_before_publication_does_not_claim_renewal(self):
        real_replace = __import__('os').replace
        def fail_grant(source, dest):
            if Path(dest) == self.approval:
                raise OSError('crash before grant')
            real_replace(source, dest)
        with patch('perpetual_alpha.funding_live.renewal.os.replace', side_effect=fail_grant):
            with self.assertRaises(OSError):
                self.run_renewal()
        recover_notice(self.approval, self.mon)
        self.assertEqual(json.loads(self.approval.read_text()), self.old)
        self.assertEqual(self.mon.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 0)

    def test_mail_failure_retains_notice_for_retry(self):
        self.run_renewal()
        with patch('perpetual_alpha.funding_live.preparation.send_mail', side_effect=RuntimeError('secret')):
            self.assertEqual(self.mon.deliver({}), 'delivery-failed')
        self.assertEqual(self.mon.db.execute('SELECT COUNT(*) FROM outbox WHERE delivered_ms IS NULL').fetchone()[0], 1)

    def test_pending_mail_prevents_next_grant(self):
        self.mon.record({'time_ms': NOW, 'issues': ['old alert']})
        self.mon.db.execute('UPDATE outbox SET attempts=1')
        self.mon.db.commit()
        with self.assertRaises(ValueError):
            self.run_renewal()

    def test_crash_after_replacement_recovers_one_notice(self):
        with patch.object(self.mon, 'renewal_notice', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                self.run_renewal()
        recover_notice(self.approval, self.mon)
        recover_notice(self.approval, self.mon)
        self.assertEqual(self.mon.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 1)

    def test_operator_change_during_checks_never_overwritten(self):
        def revoke(*args, **kwargs):
            self.ledger.pause(NOW, 'operator-pause')
            return [{'address': '0x'+'2'*40, 'validUntil': NOW+2*DAY}]
        self.client.query.side_effect = revoke
        with self.assertRaises(ValueError):
            self.run_renewal()
        self.assertEqual(json.loads(self.approval.read_text()), self.old)
