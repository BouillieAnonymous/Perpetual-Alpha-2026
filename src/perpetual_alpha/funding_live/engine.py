"""Single-writer target execution with conservative unknown-order recovery."""
import hashlib
import json

from .policy import PERIOD, VERSION, dec, funding_signal, make_order, target_quantity, validate_snapshot

TERMINAL = {'filled', 'canceled', 'rejected', 'marginCanceled', 'reduceOnlyCanceled',
            'selfTradeCanceled', 'expired', 'scheduledCancel', 'marketOrderNoLiquidityRejected',
            'minTradeNtlRejected', 'tickRejected', 'perpMarginRejected', 'badAloPxRejected',
            'iocCancelRejected', 'oracleRejected', 'openInterestIncreaseRejected'}


class Engine:
    def __init__(self, policy, ledger, client, clock):
        self.p, self.store, self.client, self.clock = policy, ledger, client, clock

    def observe(self, *, leverage=True):
        s = self.client.snapshot()
        self.store.event(self.clock(), 'observation', s)
        validate_snapshot(s, self.p, self.clock(), require_leverage=leverage)
        return s

    def reconcile(self):
        for row in self.store.pending():
            order = json.loads(row['value'])
            result = self.client.order_status(row['cloid'])
            self.store.event(self.clock(), 'order-status', {'cloid': row['cloid'], 'response': result})
            if result.get('status') != 'order' or result['order']['status'] not in TERMINAL:
                raise ValueError('Unconfirmed order; never resend, inspect/cancel explicitly')
            status = result['order']
            wire = status['order']
            if wire['coin'] != self.p.symbol or wire.get('cloid') != row['cloid']:
                raise ValueError('Order identity mismatch')
            if (wire['side'] != ('B' if order['buy'] else 'A')
                    or dec(wire['origSz']) != dec(order['quantity'])
                    or dec(wire['limitPx']) != dec(order['price'])):
                raise ValueError('Confirmed order terms mismatch')
            fills = self.client.query('userFillsByTime', user=self.client.team,
                                      startTime=row['created_ms'] - 60_000, endTime=self.clock(),
                                      aggregateByTime=False)
            if len(fills) >= 500:
                raise ValueError('Fill query may be truncated; manual reconciliation needed')
            matches = [f for f in fills if f['oid'] == wire['oid'] and f['coin'] == self.p.symbol]
            keys = [(f['coin'], f['time'], f['tid']) for f in matches]
            if len(keys) != len(set(keys)):
                raise ValueError('Duplicate fills in evidence')
            if any(f['feeToken'] != self.p.collateral or f['side'] != wire['side'] for f in matches):
                raise ValueError('Unexpected fee token or fill side')
            qty = sum((dec(f['sz']) for f in matches), dec(0))
            if not 0 <= qty <= dec(order['quantity']):
                raise ValueError('Invalid aggregate fill size')
            if status['status'] == 'filled' and qty == 0:
                raise ValueError('Filled status without fill evidence')
            expected = dec(order['before']) + qty * (1 if order['buy'] else -1)
            # Actual position is authoritative; any unrelated/manual activity stops automation.
            s = self.client.snapshot()
            self.store.event(self.clock(), 'post-order-account', s)
            validate_snapshot(s, self.p, self.clock())
            if dec(s['quantity']) != expected:
                raise ValueError('Fills/position mismatch; no further orders')
            fees = sum((dec(f['fee']) for f in matches), dec(0))
            ntl = sum((dec(f['sz']) * dec(f['px']) for f in matches), dec(0))
            if any((dec(f['px']) > dec(order['price']) if order['buy'] else
                    dec(f['px']) < dec(order['price'])) for f in matches):
                raise ValueError('Fill violates limit price')
            if ntl and fees / ntl * 10000 > dec(self.p.max_fee_bps) + dec('.01'):
                raise ValueError('Actual fee exceeds budget')
            partial = qty != dec(order['quantity'])
            self.store.settle(row['cloid'], self.clock(), {'status': status['status'], 'fills': matches,
                              'filled_quantity': str(qty), 'fees': str(fees), 'partial': partial}, expected)
            if partial:
                self.store.pause(self.clock(), 'partial-or-unfilled-order; explicit review required')
                raise ValueError('Partial/unfilled IOC; no automatic chasing')

    def account_guard(self, s):
        state = self.store.state()
        if dec(s['quantity']) != dec(state['expected_quantity']):
            raise ValueError('Unexpected position; reconcile rather than adopt')
        equity = dec(s['equity'])
        baseline = dec(state['baseline']) if state['baseline'] is not None else equity
        peak = max(dec(state['peak']) if state['peak'] is not None else equity, equity)
        if baseline - equity >= dec(self.p.max_loss) or peak - equity >= dec(self.p.max_drawdown):
            raise ValueError('Loss/drawdown stop latched; baseline is never reset by resume')
        if abs(dec(s['quantity'])) * dec(s['mark']) > dec(self.p.max_notional) * dec('1.1'):
            raise ValueError('Exposure drift beyond 110% absolute cap')
        self.store.update(baseline=str(baseline), peak=str(peak))

    def execute(self, slot, order, *, emergency=False):
        if not self.p.enabled:
            raise ValueError('Environment disabled')
        order = {**order, 'cloid': '0x' + hashlib.sha256(
            f'{self.client.team}:{self.p.digest}:{VERSION}:{slot}:{order["reduce_only"]}'.encode()
        ).hexdigest()[:32]}
        self.store.reserve(slot, self.clock(), order, emergency=emergency)
        # A crash from this point is UNKNOWN, including a crash before send. No automatic replay.
        if self.store.state()['paused'] and not emergency:
            raise ValueError('Paused before submit; intent remains unknown')
        try:
            response = self.client.send(order)
            self.store.event(self.clock(), 'order-response', {'cloid': order['cloid'], 'response': response})
            if response.get('status') != 'ok':
                raise ValueError('Exchange action rejected')
            statuses = response.get('response', {}).get('data', {}).get('statuses', [])
            if len(statuses) != 1 or not isinstance(statuses[0], dict) or 'error' in statuses[0]:
                raise ValueError('Exchange order rejected/unknown; inspect evidence')
            self.reconcile()
        except Exception:
            self.store.pause(self.clock(), 'order-outcome-needs-reconciliation')
            raise

    def tick(self, *, bootstrap=False):
        previous = self.store.state()['last_tick_ms']
        if previous is not None and self.clock() < previous:
            raise ValueError('Clock rollback; keep paused until trusted time catches up')
        request = self.store.take_request()
        if request and request['action'] == 'cancel':
            # Cancel only this ledger's unsettled IDs; never unknown/manual account orders.
            for row in self.store.pending():
                self.store.event(self.clock(), 'cancel-response', self.client.cancel(row['cloid']))
            self.store.pause(self.clock(), 'operator-cancel; reconcile before resume')
            return
        self.reconcile()
        s = self.observe(leverage=False)
        if request and request['action'] == 'flatten':
            self.store.pause(self.clock(), 'operator-flatten')
            if dec(s['quantity']) != dec(self.store.state()['expected_quantity']):
                raise ValueError('Cannot flatten an unowned position')
            order = make_order(s, '0', self.p, self.clock(), emergency=True)
            if order:
                self.execute(-self.clock(), order, emergency=True)
            elif dec(s['quantity']):
                raise ValueError('Residual dust cannot be enlarged; manual venue handling required')
            return
        self.account_guard(s)
        if request and request['action'] == 'resume':
            if not self.p.enabled:
                raise ValueError('Disabled environment cannot resume')
            if self.p.dex == 'par':
                self.store.event(self.clock(), 'leverage-response', self.client.set_leverage())
            # PAS must ALREADY be at 1x; resume never changes leverage or enlarges permissions.
            s = self.observe()
            self.account_guard(s)
            self.store.resume(request, self.clock())
        if self.store.state()['paused']:
            self.store.update(last_tick_ms=self.clock())
            return
        now = self.clock()
        slot = now // PERIOD * PERIOD
        if self.store.decided(slot) or (now - slot > self.p.decision_window_ms and not bootstrap):
            self.store.update(last_tick_ms=now)
            return
        validate_snapshot(s, self.p, now)
        signal = funding_signal(s['funding'], self.p.symbol, now)
        target = target_quantity(s, signal, self.p)
        self.store.decision(slot, now, {'signal': signal, 'target': target, 'bootstrap': bootstrap,
                                      'snapshot_received_ms': s['received_ms']})
        order = make_order(s, target, self.p, self.clock())
        if order:
            self.execute(slot, order)
            if dec(order['expected_after']) == 0 and dec(target) != 0:
                # Complete a reversal only after confirmed flat and a NEW risk/market capture.
                s = self.observe()
                self.account_guard(s)
                order = make_order(s, target, self.p, self.clock())
                if order:
                    self.execute(slot, order)
        self.store.update(last_tick_ms=self.clock())
