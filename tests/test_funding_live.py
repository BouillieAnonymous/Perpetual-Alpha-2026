"""Fault and boundary tests for REAL-exchange adapter orchestration (all I/O faked)."""
import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from perpetual_alpha.funding_live.cli import main, paths
from perpetual_alpha.funding_live.engine import Engine
from perpetual_alpha.funding_live.ops import backup
from perpetual_alpha.funding_live.policy import (
    HOUR,
    PERIOD,
    Policy,
    dec,
    funding_signal,
    load_policy,
    make_order,
    target_quantity,
    validate_snapshot,
)
from perpetual_alpha.funding_live.store import Ledger
from perpetual_alpha.funding_live.venue import TestnetVenue

NOW = PERIOD * 40000 + 1000


def snapshot(dex='par', now=NOW):
    return {'symbol': dex + ':ETH', 'collateral': 'USDPR' if dex == 'par' else 'USDP',
            'started_ms': now - 200, 'received_ms': now - 100, 'book_ms': now - 300,
            'mode': 'unifiedAccount', 'collateral_scope_verified': True, 'account_consistent': True,
            'other_positions': [], 'open_orders': [], 'sz_decimals': 4, 'delisted': False,
            'max_leverage': 25, 'bid': '1999', 'ask': '2001', 'mark': '2000', 'oracle': '2000',
            'fee_bps': '9', 'equity': '1000', 'free': '1000', 'quantity': '0',
            'leverage': {'type': 'cross', 'value': 1}, 'max_trade_sizes': ['10', '10'],
            'available_to_trade': ['1000', '1000'],
            'levels': [[{'px': '1999', 'sz': '10'}], [{'px': '2001', 'sz': '10'}]],
            'funding': [{'coin': dex + ':ETH', 'time': now - HOUR, 'available_ms': now - 100,
                         'fundingRate': '-0.0001'}], 'raw': {}}


class FakeVenue:
    team = '0x' + '1' * 40

    def __init__(self, dex='par'):
        self.s = snapshot(dex)
        self.orders = []
        self.fills = []
        self.statuses = {}
        self.network_unknown = False
        self.partial = False
        self.reject = False

    def snapshot(self):
        return copy.deepcopy(self.s)

    def set_leverage(self):
        self.s['leverage'] = {'type': 'cross', 'value': 1}
        return {'status': 'ok'}

    def send(self, o):
        self.orders.append(o)
        if self.reject:
            return {'status': 'ok', 'response': {'data': {'statuses': [{'error': 'rejected'}]}}}
        qty = dec(o['quantity']) * (dec('.5') if self.partial else 1)
        self.s['quantity'] = str(dec(o['before']) + qty * (1 if o['buy'] else -1))
        wire = {'coin': o['symbol'], 'side': 'B' if o['buy'] else 'A', 'origSz': o['quantity'],
                'sz': '0', 'limitPx': o['price'], 'cloid': o['cloid'], 'oid': len(self.orders)}
        self.statuses[o['cloid']] = {'status': 'order', 'order': {
            'status': 'canceled' if self.partial else 'filled', 'order': wire}}
        self.fills.append({'coin': o['symbol'], 'oid': len(self.orders), 'time': NOW, 'tid': len(self.fills),
                           'px': o['price'], 'sz': str(qty), 'side': wire['side'], 'fee': '0.01',
                           'feeToken': self.s['collateral']})
        if self.network_unknown:
            raise TimeoutError('submit timeout AFTER exchange acceptance')
        return {'status': 'ok', 'response': {'data': {'statuses': [{'filled': {'totalSz': str(qty)}}]}}}

    def order_status(self, cloid):
        return self.statuses.get(cloid, {'status': 'unknownOid'})

    def query(self, *_, **__):
        return self.fills

    def cancel(self, cloid):
        self.statuses[cloid]['order']['status'] = 'canceled'
        return {'status': 'ok'}


class FundingPolicyTests(unittest.TestCase):
    def setUp(self):
        self.p = Policy('par', 'USDPR', enabled=True)
        self.s = snapshot()

    def test_sign_latest_available_settlement(self):
        for rate, direction in [('1', -1), ('-1', 1), ('0', 0)]:
            self.s['funding'][0]['fundingRate'] = rate
            self.assertEqual(funding_signal(self.s['funding'], self.p.symbol, NOW)['direction'], direction)

    def test_future_receive_does_not_change_signal(self):
        rows = self.s['funding'] + [{'coin': 'par:ETH', 'time': NOW, 'available_ms': NOW + 1,
                                    'fundingRate': '1'}]
        self.assertEqual(funding_signal(rows, 'par:ETH', NOW)['direction'], 1)

    def test_stale_rate_and_mixed_venue_rejected(self):
        for field, value in [('time', NOW - 2 * HOUR - 1), ('coin', 'pas:ETH')]:
            s = copy.deepcopy(self.s)
            s['funding'][0][field] = value
            with self.assertRaises(ValueError):
                funding_signal(s['funding'], 'par:ETH', NOW)

    def test_conflicting_rate_and_nonfinite_rejected(self):
        row = {**self.s['funding'][0], 'fundingRate': '2'}
        with self.assertRaises(ValueError):
            funding_signal(self.s['funding'] + [row], 'par:ETH', NOW)
        for value in ['NaN', 'Infinity', True]:
            with self.assertRaises(ValueError):
                dec(value)

    def test_snapshots_fail_closed(self):
        fields = {'symbol': 'pas:ETH', 'collateral': 'USDP', 'mode': 'default',
                  'collateral_scope_verified': False, 'account_consistent': False,
                  'other_positions': ['par:BTC'], 'open_orders': [{}], 'delisted': True,
                  'book_ms': NOW - 10001, 'received_ms': NOW + 1, 'started_ms': NOW - 30001,
                  'oracle': '1000', 'ask': '2100', 'fee_bps': '10', 'equity': '0',
                  'leverage': {'type': 'cross', 'value': 20}}
        for field, value in fields.items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_snapshot({**self.s, field: value}, self.p, NOW)

    def test_target_and_order_stay_under_both_caps(self):
        for equity in ['1000', '300']:
            self.s['equity'] = equity
            target = target_quantity(self.s, {'direction': 1}, self.p)
            order = make_order(self.s, target, self.p, NOW)
            self.assertLessEqual(dec(order['quantity']) * dec(order['price']),
                                 min(dec('25'), dec(equity) * dec('.05')))

    def test_dust_never_rounds_up(self):
        self.assertIsNone(make_order(self.s, '.0001', self.p, NOW))
        self.s['equity'] = '100'
        self.assertIsNone(make_order(self.s, target_quantity(self.s, {'direction': 1}, self.p), self.p, NOW))

    def test_depth_free_and_array_bounds(self):
        changes = [{'free': '1'}, {'max_trade_sizes': ['1', '.001']},
                   {'available_to_trade': ['1000', '1']},
                   {'levels': [[{'px': '1999', 'sz': '10'}], [{'px': '2001', 'sz': '.001'}]]}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                make_order({**self.s, **change}, '.01', self.p, NOW)

    def test_reversal_is_reduce_only_first(self):
        self.s['quantity'] = '-.01'
        order = make_order(self.s, '.01', self.p, NOW)
        self.assertTrue(order['reduce_only'])
        self.assertEqual(dec(order['expected_after']), 0)

    def test_exit_not_blocked_by_appreciation_above_entry_budget(self):
        self.s['quantity'] = '.02'
        order = make_order(self.s, '0', self.p, NOW, emergency=True)
        self.assertTrue(order['reduce_only'])
        self.assertGreater(dec(order['quantity']) * dec(order['price']), dec(self.p.max_order_notional))
        self.assertEqual(dec(order['expected_after']), 0)

    def test_disabled_pas_config_and_exact_identity(self):
        self.assertFalse(load_policy('config/funding/pas.toml').enabled)
        with self.assertRaises(ValueError):
            replace(self.p, collateral='USDP')
        with self.assertRaises(ValueError):
            replace(self.p, target_fraction='.2')
        with self.assertRaises(ValueError):
            replace(self.p, enabled='false')

    def test_pas_and_par_same_logic_separate_symbols(self):
        for dex in ('par', 'pas'):
            s = snapshot(dex)
            p = Policy(dex, s['collateral'])
            signal = funding_signal(s['funding'], p.symbol, NOW)
            self.assertEqual(signal['direction'], 1)
            self.assertEqual(make_order(s, '.01', p, NOW)['symbol'], dex + ':ETH')


class FundingEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'ledger.sqlite3'
        self.p = Policy('par', 'USDPR', enabled=True)
        self.identity = {'dex': 'par', 'policy_hash': self.p.digest}
        self.store = Ledger(self.path, self.identity)
        self.store.boot(NOW)
        self.client = FakeVenue()
        self.engine = Engine(self.p, self.store, self.client, lambda: NOW)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def resume(self):
        self.store.request('resume', self.store.state()['generation'])

    def test_startup_paused_no_orders(self):
        self.engine.tick()
        self.assertEqual(self.client.orders, [])
        self.assertTrue(self.store.state()['paused'])

    def test_resume_fill_and_slot_idempotency(self):
        self.resume()
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(len(self.client.orders), 1)
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(dec(self.store.state()['expected_quantity']), dec(self.client.s['quantity']))

    def test_timeout_after_fill_recovers_without_resending(self):
        self.client.network_unknown = True
        self.resume()
        with self.assertRaises(TimeoutError):
            self.engine.tick()
        self.assertTrue(self.store.pending())
        self.store.boot(NOW)
        self.engine.reconcile()
        self.assertEqual(self.store.pending(), [])
        self.assertTrue(self.store.state()['paused'])
        self.engine.tick()
        self.assertEqual(len(self.client.orders), 1)

    def test_durable_intent_before_network(self):
        def crash(o):
            self.assertEqual(self.store.pending()[0]['cloid'], o['cloid'])
            raise SystemExit('simulated hard exit')
        self.client.send = crash
        self.resume()
        with self.assertRaises(SystemExit):
            self.engine.tick()
        self.store.close()
        self.store = Ledger(self.path, self.identity)
        self.store.boot(NOW)
        self.engine = Engine(self.p, self.store, self.client, lambda: NOW)
        with self.assertRaises(ValueError):
            self.engine.reconcile()
        self.assertTrue(self.store.pending())
        self.assertTrue(self.store.state()['paused'])

    def test_partial_ioc_pauses_and_no_chasing(self):
        self.client.partial = True
        self.resume()
        with self.assertRaises(ValueError):
            self.engine.tick()
        self.assertTrue(self.store.state()['paused'])
        self.assertEqual(self.store.pending(), [])
        self.engine.tick()
        self.assertEqual(len(self.client.orders), 1)

    def test_request_level_ok_order_error_stops(self):
        self.client.reject = True
        self.resume()
        with self.assertRaises(ValueError):
            self.engine.tick()
        self.assertTrue(self.store.pending())
        self.assertTrue(self.store.state()['paused'])

    def test_external_position_not_adopted(self):
        self.client.s['quantity'] = '.01'
        self.resume()
        with self.assertRaises(ValueError):
            self.engine.tick()
        self.assertEqual(self.client.orders, [])

    def test_foreign_runtime_is_not_adopted(self):
        path = Path(self.tmp.name) / 'foreign.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE old_runtime (value TEXT)')
        db.close()
        with self.assertRaises(ValueError):
            Ledger(path, self.identity)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
                             [('old_runtime',)])
        db.close()

    def test_restart_invalidates_old_operator_request(self):
        self.resume()
        self.store.boot(NOW)
        self.engine.tick()
        self.assertTrue(self.store.state()['paused'])
        self.assertEqual(self.client.orders, [])

    def test_pause_invalidates_queued_resume(self):
        self.resume()
        self.store.pause(NOW, 'operator-paused-after-resume-request')
        self.engine.tick()
        self.assertTrue(self.store.state()['paused'])
        self.assertEqual(self.client.orders, [])

    def test_pause_during_resume_capture_wins(self):
        original = self.client.set_leverage
        def concurrent_pause():
            self.store.pause(NOW, 'operator-pause-during-checks')
            return original()
        self.client.set_leverage = concurrent_pause
        self.resume()
        with self.assertRaises(ValueError):
            self.engine.tick()
        self.assertTrue(self.store.state()['paused'])
        self.assertEqual(self.client.orders, [])

    def test_policy_or_venue_cannot_relabel_ledger(self):
        for identity in [{'dex': 'pas', 'policy_hash': self.p.digest},
                         {'dex': 'par', 'policy_hash': 'different'}]:
            with self.assertRaises(ValueError):
                Ledger(self.path, identity)

    def test_loss_stop_not_cleared_by_resume(self):
        self.engine.tick()
        self.client.s['equity'] = '990'
        self.resume()
        with self.assertRaises(ValueError):
            self.engine.tick()
        self.assertEqual(self.store.state()['baseline'], '1000')
        self.assertEqual(self.client.orders, [])

    def test_clock_rollback_blocks(self):
        self.store.update(last_tick_ms=NOW + 1)
        with self.assertRaises(ValueError):
            self.engine.tick()

    def test_paused_flatten_bypasses_loss_but_only_reduces(self):
        self.client.s['quantity'] = '.01'
        self.client.s['equity'] = '990'
        self.store.update(expected_quantity='.01', baseline='1000', peak='1000')
        self.store.request('flatten', self.store.state()['generation'])
        self.engine.tick()
        self.assertEqual(dec(self.client.s['quantity']), 0)
        self.assertTrue(self.client.orders[0]['reduce_only'])
        self.assertTrue(self.store.state()['paused'])

    def test_bad_fill_token_leaves_unknown(self):
        original = self.client.send
        def bad_fill(o):
            result = original(o)
            self.client.fills[0]['feeToken'] = 'USDP'
            return result
        self.client.send = bad_fill
        self.resume()
        with self.assertRaises(ValueError):
            self.engine.tick()
        self.assertTrue(self.store.pending())

    def test_disabled_pas_never_sends(self):
        self.engine.p = Policy('pas', 'USDP', enabled=False)
        self.engine.client = FakeVenue('pas')
        self.resume()
        with self.assertRaises(ValueError):
            self.engine.tick()
        self.assertEqual(self.engine.client.orders, [])

    def test_process_lock_blocks_second_writer(self):
        from perpetual_alpha.runtime.process_lock import ProcessLock
        lock = Path(self.tmp.name) / 'process.lock'
        with ProcessLock(lock):
            code = ('from perpetual_alpha.runtime.process_lock import ProcessLock; '
                    f'lock=ProcessLock({str(lock)!r}); lock.__enter__()')
            result = subprocess.run([sys.executable, '-c', code], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_actual_process_exit_preserves_unknown_intent(self):
        child_path = Path(self.tmp.name) / 'crash.sqlite3'
        code = (
            'import os; from perpetual_alpha.funding_live.store import Ledger; '
            f's=Ledger({str(child_path)!r}, {self.identity!r}); '
            f's.boot({NOW}); s.update(paused=False); '
            f's.reserve(1,{NOW},{{"cloid":"0x"+"a"*32}}); os._exit(23)'
        )
        result = subprocess.run([sys.executable, '-c', code], capture_output=True)
        self.assertEqual(result.returncode, 23)
        child = Ledger(child_path, self.identity)
        try:
            child.boot(NOW)
            self.assertTrue(child.state()['paused'])
            self.assertEqual(len(child.pending()), 1)
        finally:
            child.close()

    def test_online_wal_backup_preserves_identity_and_pending(self):
        self.store.close()
        self.identity['schema'] = 'funding-exchange-ledger/1'
        self.path = Path(self.tmp.name) / 'backup-source.sqlite3'
        self.store = Ledger(self.path, self.identity)
        self.store.boot(NOW)
        self.store.update(paused=False)
        self.store.reserve(1, NOW, {'cloid': '0x' + 'a' * 32})
        destination = Path(self.tmp.name) / 'backup.sqlite3'
        result = backup(self.path, destination)
        self.assertEqual(result['pending_intents'], 1)
        with self.assertRaises(FileExistsError):
            backup(self.path, destination)
        restored = Ledger(destination, self.identity)
        try:
            restored.boot(NOW)
            self.assertTrue(restored.state()['paused'])
            self.assertEqual(len(restored.pending()), 1)
        finally:
            restored.close()


class FundingCliTests(unittest.TestCase):
    def test_run_disabled_no_signer_loaded(self):
        with patch.object(TestnetVenue, 'enable_signer') as signer:
            for dex in ['par', 'pas']:
                with self.assertRaises(ValueError):
                    main(['run', '--config', 'config/funding/' + dex + '.toml'])
            signer.assert_not_called()

    def test_inspect_has_no_network_or_filesystem_mutation(self):
        with tempfile.TemporaryDirectory() as root, patch.object(TestnetVenue, 'query') as query:
            main(['inspect', '--config', 'config/funding/pas.toml', '--root', root])
            self.assertEqual(list(Path(root).iterdir()), [])
            query.assert_not_called()

    def test_symlink_environment_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder / 'other').mkdir()
            try:
                (folder / 'par').symlink_to(folder / 'other', target_is_directory=True)
            except OSError:
                self.skipTest('OS does not allow symlink creation')
            with self.assertRaises(ValueError):
                paths(folder, 'par')


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.p = Policy('par', 'USDPR')
        self.client = TestnetVenue.__new__(TestnetVenue)
        self.client.policy = self.p
        self.client.team = FakeVenue.team
        self.client.clock = lambda: NOW
        spec = {'name': 'par:ETH', 'szDecimals': 4, 'maxLeverage': 25, 'deployerFeeScale': '1'}
        meta = {'universe': [spec], 'collateralToken': 100}
        empty = {'assetPositions': []}
        self.responses = {
            'metaAndAssetCtxs': [meta, [{'markPx': '2000', 'oraclePx': '2000'}]],
            'spotMeta': {'tokens': [{'name': 'USDPR', 'index': 100, 'szDecimals': 2}]},
            'allPerpMetas': [meta, {'universe': [{'name': 'parx:BTC'}], 'collateralToken': 100}],
            'clearinghouseState': empty, 'userAbstraction': 'unifiedAccount', 'openOrders': [],
            'spotClearinghouseState': {'balances': [{'coin': 'USDPR', 'token': 100, 'total': '1000', 'hold': '0'}],
                                      'tokenToAvailableAfterMaintenance': [[100, '1000']]},
            'activeAssetData': {'user': FakeVenue.team, 'coin': 'par:ETH', 'leverage': {'type': 'cross', 'value': 1},
                                'maxTradeSzs': ['1', '1'], 'availableToTrade': ['1000', '1000']},
            'userFees': {'userCrossRate': '.00045'}, 'fundingHistory': [
                {'coin': 'par:ETH', 'time': NOW-HOUR, 'fundingRate': '-.0001'}],
            'userFunding': [], 'userFillsByTime': [],
            'l2Book': {'coin': 'par:ETH', 'time': NOW, 'levels': snapshot()['levels']},
        }
        self.calls = []
        def query(kind, **kw):
            self.calls.append((kind, kw))
            return copy.deepcopy(self.responses[kind])
        self.client.query = query

    def test_adapter_checks_shared_collateral_and_real_availability(self):
        s = self.client.snapshot()
        validate_snapshot(s, self.p, NOW)
        self.assertIn(('openOrders', {'user': FakeVenue.team, 'dex': 'parx'}), self.calls)
        self.assertEqual(s['funding'][0]['available_ms'], NOW)
        self.assertEqual(s['fee_bps'], '9.00000')

    def test_wrong_collateral_metadata_refused(self):
        self.responses['spotMeta']['tokens'][0]['name'] = 'USDP'
        with self.assertRaises(ValueError):
            self.client.snapshot()

    def held_capture(self, *, persistent=False, flat=False, foreign=False, negative=False,
                     changed_position=False, retry_error=False):
        """Replay the live 24.60567 -> 24.62009 asynchronous hold capture."""
        original = self.client.query
        count = {'own': 0, 'spot': 0, 'meta': 0}
        def query(kind, **kw):
            if kind == 'metaAndAssetCtxs':
                count['meta'] += 1
                if retry_error and count['meta'] == 2:
                    raise TimeoutError('public recapture failed')
            if kind == 'clearinghouseState':
                if kw.get('dex') != self.p.dex:
                    return {'assetPositions': []}
                count['own'] += 1
                if flat:
                    return {'assetPositions': []}
                margin = '24.60567' if count['own'] <= 2 or persistent else '24.62009'
                quantity = '.02' if changed_position and count['own'] == 2 else '.0103'
                return {'assetPositions': [{'position': {'coin': self.p.symbol, 'szi': quantity,
                        'leverage': {'type': 'cross', 'value': 1}, 'marginUsed': margin}}]}
            if kind == 'spotClearinghouseState':
                count['spot'] += 1
                row = original(kind, **kw)
                row['balances'][0].update(
                    total='99999.796018' if count['spot'] == 1 else '99999.810438',
                    hold='-1' if negative else ('24.60567' if count['spot'] == 1 else '24.62009'))
                return row
            if foreign and kind == 'openOrders' and kw.get('dex') == 'parx':
                return [{'coin': 'parx:BTC'}]
            return original(kind, **kw)
        self.client.query = query
        return count

    def test_transient_hold_capture_is_replaced_by_fresh_validated_capture(self):
        count = self.held_capture()
        s = self.client.snapshot()
        validate_snapshot(s, self.p, NOW)
        self.assertEqual(count['meta'], 2)
        self.assertEqual(s['other_positions'], [])
        self.assertEqual(s['raw']['collateral_recapture']['attempts'], 2)
        self.assertEqual(s['raw']['collateral_recapture']['rejected'][0]['spot_after']['balances'][0]['hold'],
                         '24.62009')
        self.assertEqual(s['raw']['after']['assetPositions'][0]['position']['marginUsed'], '24.62009')

    def test_persistent_unexplained_hold_still_blocks_after_one_recapture(self):
        count = self.held_capture(persistent=True)
        s = self.client.snapshot()
        self.assertEqual(count['meta'], 2)
        self.assertIn('collateral-hold', s['other_positions'])
        with self.assertRaises(ValueError):
            validate_snapshot(s, self.p, NOW)

    def test_pas_own_cross_hold_race_uses_same_safe_recapture(self):
        self.p = Policy('pas', 'USDP')
        self.client.policy = self.p
        self.responses = json.loads(json.dumps(self.responses).replace('par:', 'pas:').replace('USDPR', 'USDP'))
        count = self.held_capture()
        s = self.client.snapshot()
        validate_snapshot(s, self.p, NOW)
        self.assertEqual(s['symbol'], 'pas:ETH')
        self.assertEqual(s['collateral'], 'USDP')
        self.assertEqual(count['meta'], 2)

    def test_recapture_never_masks_flat_foreign_negative_or_changing_accounts(self):
        for flags in ({'flat': True}, {'foreign': True}, {'negative': True}, {'changed_position': True}):
            with self.subTest(flags=flags):
                self.setUp()
                count = self.held_capture(**flags)
                with self.assertRaises(ValueError):
                    validate_snapshot(self.client.snapshot(), self.p, NOW)
                self.assertEqual(count['meta'], 1)

    def test_failed_recapture_does_not_return_previous_unverified_snapshot(self):
        count = self.held_capture(retry_error=True)
        with self.assertRaises(TimeoutError):
            self.client.snapshot()
        self.assertEqual(count['meta'], 2)

    def test_recapture_returns_fresh_funding_book_and_free_margin(self):
        count = self.held_capture()
        original = self.client.query
        def update(kind, **kw):
            if kind == 'metaAndAssetCtxs' and count['meta'] == 1:
                self.responses['fundingHistory'][0]['fundingRate'] = '.0001'
                self.responses['l2Book']['levels'][1][0]['px'] = '2002'
                self.responses['spotClearinghouseState']['tokenToAvailableAfterMaintenance'][0][1] = '700'
            return original(kind, **kw)
        self.client.query = update
        s = self.client.snapshot()
        validate_snapshot(s, self.p, NOW)
        self.assertEqual(funding_signal(s['funding'], self.p.symbol, NOW)['direction'], -1)
        self.assertEqual(s['ask'], '2002')
        self.assertEqual(s['free'], '700')

    def test_recapture_rechecks_new_foreign_orders(self):
        count = self.held_capture()
        original = self.client.query
        def foreign(kind, **kw):
            if kind == 'openOrders' and count['meta'] == 2 and kw.get('dex') == 'parx':
                return [{'coin': 'parx:BTC'}]
            return original(kind, **kw)
        self.client.query = foreign
        s = self.client.snapshot()
        with self.assertRaises(ValueError):
            validate_snapshot(s, self.p, NOW)
        self.assertEqual(count['meta'], 2)

    def test_own_unified_margin_hold_is_allowed_and_pnl_not_double_counted(self):
        self.responses['clearinghouseState'] = {'assetPositions': [{'position': {
            'coin': 'par:ETH', 'szi': '.01', 'leverage': {'type': 'cross', 'value': 1},
            'marginUsed': '20', 'unrealizedPnl': '2'}}]}
        self.responses['spotClearinghouseState']['balances'][0].update(total='1002', hold='20')
        original = self.client.query
        def scoped(kind, **kw):
            if kind == 'clearinghouseState' and kw.get('dex') == 'parx':
                return {'assetPositions': []}
            return original(kind, **kw)
        self.client.query = scoped
        s = self.client.snapshot()
        self.assertEqual(s['equity'], '1002')
        self.assertEqual(s['other_positions'], [])
        validate_snapshot(s, self.p, NOW)
        self.responses['spotClearinghouseState']['balances'][0]['hold'] = '21'
        with self.assertRaises(ValueError):
            validate_snapshot(self.client.snapshot(), self.p, NOW)

    def test_mark_movement_during_nonzero_position_capture_is_not_foreign_activity(self):
        self.responses['clearinghouseState'] = {'assetPositions': [{'position': {
            'coin': 'par:ETH', 'szi': '.01', 'leverage': {'type': 'cross', 'value': 1},
            'marginUsed': '20', 'unrealizedPnl': '2'}}]}
        original = self.client.query
        count = 0
        def scoped(kind, **kw):
            nonlocal count
            if kind == 'clearinghouseState' and kw.get('dex') == 'parx':
                return {'assetPositions': []}
            row = original(kind, **kw)
            if kind == 'spotClearinghouseState':
                count += 1
                row['balances'][0].update(total='1002' if count == 1 else '1003', hold='20')
            return row
        self.client.query = scoped
        s = self.client.snapshot()
        self.assertTrue(s['account_consistent'])
        self.assertEqual(s['equity'], '1002')

    def test_unrelated_collateral_mark_change_does_not_mix_environments(self):
        original = self.client.query
        count = 0
        def unrelated(kind, **kw):
            nonlocal count
            row = original(kind, **kw)
            if kind == 'spotClearinghouseState':
                count += 1
                row['balances'].append({'coin': 'USDP', 'token': 200, 'total': str(count), 'hold': '0'})
            return row
        self.client.query = unrelated
        s = self.client.snapshot()
        self.assertTrue(s['account_consistent'])
        self.assertEqual(s['equity'], '1000')

    def test_shared_dex_exposure_is_not_ignored(self):
        original = self.client.query
        def shared(kind, **kw):
            if kind == 'clearinghouseState' and kw.get('dex') == 'parx':
                return {'assetPositions': [{'position': {'coin': 'parx:BTC'}}]}
            return original(kind, **kw)
        self.client.query = shared
        s = self.client.snapshot()
        self.assertIn('parx:BTC', s['other_positions'])
        with self.assertRaises(ValueError):
            validate_snapshot(s, self.p, NOW)

    def test_disabled_policy_cannot_load_signer(self):
        with patch('eth_account.Account.from_key') as from_key:
            with self.assertRaises(ValueError):
                self.client.enable_signer()
            from_key.assert_not_called()
