"""Automatic spread waiting never grants authority or clears a hard pause."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_funding_live import NOW, FakeVenue, snapshot

from perpetual_alpha.funding_live.engine import Engine
from perpetual_alpha.funding_live.policy import PERIOD, Policy, SpreadAbovePolicy, validate_snapshot
from perpetual_alpha.funding_live.preparation import runtime_issues
from perpetual_alpha.funding_live.store import Ledger


class SpreadWaitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'pas.sqlite3'
        self.p = Policy('pas', 'USDP', enabled=True)
        self.store = Ledger(self.path, {'schema': 'funding-exchange-ledger/1', 'dex': 'pas',
                                       'policy_hash': self.p.digest})
        self.addCleanup(self.store.close)
        self.now = NOW
        self.store.boot(self.now)
        self.client = FakeVenue('pas')
        self.client.check_action_approval = Mock()
        self.client.check_authorization = Mock()
        self.engine = Engine(self.p, self.store, self.client, lambda: self.now)

    def capture(self, *, wide=False, **fields):
        self.client.s = {**snapshot('pas', self.now), 'quantity': self.client.s['quantity'], **fields}
        if wide:
            self.client.s.update(bid='1998', ask='2002')  # 20 bps; limit remains 15.
            self.client.s['levels'][0][0]['px'] = '1998'
            self.client.s['levels'][1][0]['px'] = '2002'

    def wait(self):
        self.capture(wide=True)
        self.store.request('resume', self.store.state()['generation'])
        self.engine.tick()
        self.assertFalse(self.store.state()['paused'])
        self.assertEqual(self.store.state()['market_wait']['reason'], 'spread')

    def live_tick_error(self):
        # Same hard-pause boundary as the real CLI, with offline exceptions only.
        try:
            self.engine.tick()
        except Exception as exc:
            self.store.pause(self.now, str(exc))
            raise

    def test_wide_spread_waits_without_order_or_consuming_decision(self):
        self.wait()
        self.engine.tick()
        self.assertEqual(self.client.orders, [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM events WHERE kind='market-spread-wait'").fetchone()[0], 1)
        self.assertTrue(any('waiting for spread' in x for x in runtime_issues(self.path, self.now)))
        with self.assertRaises(ValueError):
            self.store.reserve(1, self.now, {'cloid': 'blocked'})

    def test_recovery_inside_window_uses_fresh_checks_and_one_order(self):
        self.wait()
        self.now += 60000
        self.capture()
        self.engine.tick()
        self.assertFalse(self.store.state()['paused'])
        self.assertIsNone(self.store.state()['market_wait'])
        self.assertEqual(len(self.client.orders), 1)
        self.assertEqual(self.store.pending(), [])
        self.client.check_action_approval.assert_called_with('trading')
        self.assertGreaterEqual(self.client.check_authorization.call_count, 2)
        self.engine.tick()
        self.assertEqual(len(self.client.orders), 1)

    def test_recovery_outside_window_does_not_catch_up(self):
        self.wait()
        self.now += self.p.decision_window_ms + 1
        self.capture()
        self.engine.tick()
        self.assertIsNone(self.store.state()['market_wait'])
        self.assertEqual(self.client.orders, [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0], 0)
        self.now = NOW // PERIOD * PERIOD + PERIOD + 1000
        self.capture()
        self.engine.tick()
        self.assertEqual(len(self.client.orders), 1)

    def test_manual_pause_during_wait_never_recovers_automatically(self):
        self.wait()
        self.store.pause(self.now, 'operator-pause')
        self.capture()
        self.engine.tick()
        self.assertTrue(self.store.state()['paused'])
        self.assertIsNone(self.store.state()['market_wait'])
        self.assertEqual(self.client.orders, [])

    def test_non_spread_risk_failures_latch_even_while_spread_is_wide(self):
        failures = [{'open_orders': [{}]}, {'other_positions': ['foreign']}, {'quantity': '.1'},
                    {'fee_bps': '10'}, {'oracle': '1900'}, {'equity': '990'}, {'free': '-1'},
                    {'leverage': {'type': 'cross', 'value': 20}}, {'account_consistent': False}]
        for fields in failures:
            with self.subTest(fields=fields):
                self.setUp()
                self.wait()
                self.capture(wide=True, **fields)
                with self.assertRaises(ValueError):
                    self.live_tick_error()
                self.capture(quantity='0')
                self.engine.tick()
                self.assertTrue(self.store.state()['paused'])
                self.assertIsNone(self.store.state()['market_wait'])
                self.assertEqual(self.client.orders, [])

    def test_expired_local_approval_latches_before_decision_or_intent(self):
        self.wait()
        self.capture()
        self.client.check_action_approval.side_effect = ValueError('Approval expired')
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.live_tick_error()
        self.client.check_action_approval.side_effect = None
        self.engine.tick()
        self.assertTrue(self.store.state()['paused'])
        self.assertEqual(self.client.orders, [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0], 0)

    def test_exchange_authorization_revocation_is_not_spread_wait(self):
        self.wait()
        self.client.check_authorization.side_effect = ValueError('API authorization absent/expired')
        with self.assertRaises(ValueError):
            self.live_tick_error()
        self.assertTrue(self.store.state()['paused'])
        self.assertIsNone(self.store.state()['market_wait'])

    def test_manual_pause_wins_during_recovery_authorization_check(self):
        self.wait()
        self.capture()
        self.client.check_authorization.side_effect = lambda: self.store.pause(self.now, 'operator-pause')
        self.engine.tick()
        self.assertTrue(self.store.state()['paused'])
        self.assertIsNone(self.store.state()['market_wait'])
        self.assertEqual(self.client.orders, [])

    def test_restart_and_startup_pauses_never_auto_resume(self):
        self.wait()
        self.store.boot(self.now)
        self.capture()
        self.engine.tick()
        self.assertTrue(self.store.state()['paused'])
        self.assertIsNone(self.store.state()['market_wait'])
        self.assertEqual(self.client.orders, [])

    def test_unknown_order_remains_hard_pause(self):
        self.wait()
        self.store.update(market_wait=None)
        self.store.reserve(1, self.now, {'cloid': '0x' + 'a' * 32})
        with self.assertRaises(ValueError):
            self.live_tick_error()
        self.assertTrue(self.store.pending())
        self.assertTrue(self.store.state()['paused'])
        self.assertIsNone(self.store.state()['market_wait'])

    def test_spread_error_after_submit_is_not_automatically_retried(self):
        self.wait()
        self.capture()
        send = self.client.send
        def accepted_then_wide(order):
            result = send(order)
            self.capture(wide=True)
            return result
        self.client.send = accepted_then_wide
        with self.assertRaises(SpreadAbovePolicy):
            self.live_tick_error()
        self.assertTrue(self.store.state()['paused'])
        self.assertIsNone(self.store.state()['market_wait'])
        self.assertTrue(self.store.pending())
        self.capture()
        self.engine.tick()
        self.assertTrue(self.store.state()['paused'])
        self.assertEqual(len(self.client.orders), 1)

    def test_hard_validation_precedes_spread_classification(self):
        self.capture(wide=True, fee_bps='10')
        with self.assertRaises(ValueError) as error:
            validate_snapshot(self.client.s, self.p, self.now)
        self.assertNotIsInstance(error.exception, SpreadAbovePolicy)

    def test_waiting_updates_peak_without_resetting_loss_baseline(self):
        self.wait()
        self.capture(wide=True, equity='1006')
        self.engine.tick()
        self.assertEqual(self.store.state()['baseline'], '1000')
        self.assertEqual(self.store.state()['peak'], '1006')
        self.capture()
        with self.assertRaisesRegex(ValueError, 'drawdown'):
            self.live_tick_error()
        self.assertTrue(self.store.state()['paused'])

    def test_par_spread_behavior_is_unchanged(self):
        self.engine.p = Policy('par', 'USDPR', enabled=True)
        self.client.s = snapshot('par')
        self.client.s.update(bid='1998', ask='2002')
        with self.assertRaises(SpreadAbovePolicy):
            self.engine.tick()
        self.assertIsNone(self.store.state()['market_wait'])
