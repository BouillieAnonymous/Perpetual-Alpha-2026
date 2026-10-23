"""Expired quotes wait before decision; future timestamps and account faults latch."""
import unittest
from unittest.mock import Mock

import test_pas_network_wait as network_tests

from perpetual_alpha.funding_live.policy import PERIOD, StaleExchangeBook


class StaleBookTests(unittest.TestCase):
    setUp = network_tests.NetworkWaitTests.setUp
    capture = network_tests.NetworkWaitTests.capture
    active = network_tests.NetworkWaitTests.active
    live_tick_error = network_tests.NetworkWaitTests.live_tick_error

    def stale(self):
        self.capture(book_ms=self.now-self.p.max_book_age_ms-1)
        self.engine.tick()

    def test_stale_quote_waits_without_decision_or_order(self):
        self.active()
        self.stale()
        self.engine.tick()
        self.assertFalse(self.store.state()['paused'])
        self.assertEqual(self.store.state()['market_wait']['reason'], 'stale-book')
        self.assertEqual(self.client.orders, [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0], 0)

    def test_fresh_recovery_in_window_executes_once(self):
        self.active()
        self.stale()
        self.now=self.now//PERIOD*PERIOD+PERIOD+1000
        self.capture()
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(len(self.client.orders), 1)
        self.assertIsNone(self.store.state()['market_wait'])

    def test_fresh_recovery_outside_window_never_catches_up(self):
        self.active()
        self.stale()
        self.capture()
        self.engine.tick()
        self.assertEqual(self.client.orders, [])
        self.assertIsNone(self.store.state()['market_wait'])

    def test_future_book_and_account_errors_remain_hard(self):
        self.active()
        for fields in [{'book_ms':self.now+1}, {'other_positions':['foreign']},
                       {'quantity':'.1'}, {'equity':'990'}]:
            self.capture(book_ms=self.now-10001, **{k:v for k,v in fields.items() if k!='book_ms'})
            if 'book_ms' in fields:
                self.client.s['book_ms']=fields['book_ms']
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.engine.tick()

    def test_expiry_and_revocation_remain_hard(self):
        self.active()
        self.client.check_action_approval.side_effect=ValueError('expired')
        with self.assertRaisesRegex(ValueError,'expired'):
            self.stale()
        self.client.check_action_approval.side_effect=None
        self.client.check_authorization.side_effect=ValueError('revoked')
        with self.assertRaisesRegex(ValueError,'revoked'):
            self.stale()

    def test_post_submit_stale_quote_not_auto_recovered(self):
        self.active()
        self.now=self.now//PERIOD*PERIOD+PERIOD+1000
        self.capture()
        original=self.client.send
        def send(order):
            result=original(order)
            self.client.s['book_ms']=self.now-10001
            return result
        self.client.send=send
        with self.assertRaises(StaleExchangeBook):
            self.live_tick_error()
        self.assertTrue(self.store.state()['paused'])
        self.assertEqual(len(self.store.pending()),1)

    def test_operator_pause_during_stale_capture_wins(self):
        self.active()
        self.capture(book_ms=self.now-10001)
        original=self.client.snapshot
        def capture():
            self.store.pause(self.now,'operator-pause')
            return original()
        self.client.snapshot=Mock(side_effect=capture)
        self.engine.tick()
        self.assertTrue(self.store.state()['paused'])
        self.assertIsNone(self.store.state()['market_wait'])
