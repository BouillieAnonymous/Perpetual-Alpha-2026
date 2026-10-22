"""Only pre-intent transient public I/O may recover without operator action."""
import unittest
from unittest.mock import Mock

import requests
import test_pas_spread_wait as spread_tests

from perpetual_alpha.funding_live.venue import PublicQueryError, TestnetVenue, TransientPublicQueryError


class NetworkWaitTests(unittest.TestCase):
    setUp = spread_tests.SpreadWaitTests.setUp
    capture = spread_tests.SpreadWaitTests.capture
    wait = spread_tests.SpreadWaitTests.wait
    live_tick_error = spread_tests.SpreadWaitTests.live_tick_error

    def active(self):
        # Resume outside the window, without generating a decision.
        self.now += self.p.decision_window_ms + 1
        self.capture()
        self.store.request('resume', self.store.state()['generation'])
        self.engine.tick()
        self.assertFalse(self.store.state()['paused'])

    def outage(self):
        self.client.snapshot = Mock(side_effect=TransientPublicQueryError('l2Book', 503))
        self.engine.tick()

    def test_network_wait_blocks_reserve_and_deduplicates(self):
        self.active()
        self.outage()
        self.engine.tick()
        self.assertEqual(self.store.state()['market_wait']['reason'], 'network')
        self.assertEqual(self.client.orders, [])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM events WHERE kind='market-network-wait'").fetchone()[0], 1)
        with self.assertRaises(ValueError):
            self.store.reserve(1, self.now, {'cloid': 'blocked'})

    def test_fresh_recovery_outside_window_never_catches_up(self):
        self.active()
        self.outage()
        self.client.snapshot = Mock(return_value=self.client.s)
        self.engine.tick()
        self.assertIsNone(self.store.state()['market_wait'])
        self.assertEqual(self.client.orders, [])

    def test_recovery_inside_next_window_executes_once(self):
        self.active()
        self.outage()
        from perpetual_alpha.funding_live.policy import PERIOD
        self.now = self.now // PERIOD * PERIOD + PERIOD + 1000
        self.capture()
        self.client.snapshot = Mock(side_effect=lambda: dict(self.client.s))
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(len(self.client.orders), 1)

    def test_local_expiry_still_hard_during_outage(self):
        self.active()
        self.outage()
        self.client.check_action_approval.side_effect = ValueError('expired')
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.live_tick_error()
        self.assertTrue(self.store.state()['paused'])

    def test_manual_pause_is_never_cleared_by_network_recovery(self):
        self.active()
        self.outage()
        self.store.pause(self.now, 'operator-pause')
        self.client.snapshot = Mock(return_value=self.client.s)
        self.engine.tick()
        self.assertTrue(self.store.state()['paused'])

    def test_post_submit_public_failure_remains_hard(self):
        self.wait()
        self.capture()
        self.client.order_status = Mock(side_effect=TransientPublicQueryError('orderStatus', 503))
        with self.assertRaises(TransientPublicQueryError):
            self.live_tick_error()
        self.assertTrue(self.store.state()['paused'])
        self.assertEqual(len(self.store.pending()), 1)
        self.assertEqual(len(self.client.orders), 1)

    def test_authorization_query_outage_soft_but_revocation_hard(self):
        self.active()
        self.client.check_authorization.side_effect = TransientPublicQueryError('extraAgents', 429)
        self.engine.tick()
        self.assertEqual(self.store.state()['market_wait']['reason'], 'network')
        self.client.check_authorization.side_effect = ValueError('revoked')
        with self.assertRaisesRegex(ValueError, 'revoked'):
            self.live_tick_error()
        self.assertTrue(self.store.state()['paused'])

    def test_recovered_snapshot_risk_failure_hard_pauses(self):
        self.active()
        self.outage()
        self.capture(quantity='.5')
        self.client.snapshot = Mock(return_value=self.client.s)
        with self.assertRaises(ValueError):
            self.live_tick_error()
        self.assertTrue(self.store.state()['paused'])

    def test_restart_while_waiting_requires_operator_resume(self):
        self.active()
        self.outage()
        self.store.boot(self.now)
        self.assertTrue(self.store.state()['paused'])
        self.assertIsNone(self.store.state()['market_wait'])

    def test_par_transient_failure_never_becomes_soft_wait(self):
        from dataclasses import replace
        self.engine.p = replace(self.p, dex='par', collateral='USDPR')
        self.client.snapshot = Mock(side_effect=TransientPublicQueryError('l2Book', 503))
        with self.assertRaises(TransientPublicQueryError):
            self.engine.tick()

    def test_operator_pause_racing_failure_is_preserved(self):
        self.active()
        def fail():
            self.store.pause(self.now, 'operator-pause')
            raise TransientPublicQueryError('l2Book', 503)
        self.client.snapshot = fail
        self.engine.tick()
        self.assertTrue(self.store.state()['paused'])
        self.assertIsNone(self.store.state()['market_wait'])


class QueryClassificationTests(unittest.TestCase):
    def test_http_status_allowlist_and_sanitization(self):
        for status in [400, 401, 403, 408, 429, 500, 502, 503, 504]:
            with self.subTest(status=status):
                response = requests.Response()
                response.status_code = status
                response._content = b'secret must not appear'
                venue = object.__new__(TestnetVenue)
                venue.session = Mock()
                venue.session.post.return_value = response
                expected = TransientPublicQueryError if status in {408, 429, 500, 502, 503, 504} else PublicQueryError
                with self.assertRaises(expected) as caught:
                    venue.query('l2Book', coin='pas:ETH')
                if expected is TransientPublicQueryError:
                    self.assertEqual(caught.exception.detail, {'query': 'l2Book', 'status': status})
                    self.assertNotIn('secret', str(caught.exception))

    def test_transport_retries_except_tls(self):
        for exc in [requests.Timeout(), requests.ConnectionError(), requests.exceptions.SSLError()]:
            venue = object.__new__(TestnetVenue)
            venue.session = Mock()
            venue.session.post.side_effect = exc
            expected = requests.exceptions.SSLError if isinstance(exc, requests.exceptions.SSLError) else TransientPublicQueryError
            with self.assertRaises(expected):
                venue.query('l2Book')
