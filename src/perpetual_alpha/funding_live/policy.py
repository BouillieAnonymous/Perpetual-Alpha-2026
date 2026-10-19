"""Strict release policy, Decimal sizing and independent pre-order checks."""
import hashlib
import json
import tomllib
from dataclasses import asdict, dataclass
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from ..models import limit_price
from ..venues import venue

HOUR = 3_600_000
PERIOD = 12 * HOUR
VERSION = 'eth-funding-last/1'


def dec(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not an exchange number')
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('Nonfinite number')
    return result


@dataclass(frozen=True)
class Policy:
    dex: str
    collateral: str
    enabled: bool = False
    target_fraction: str = '0.05'
    max_notional: str = '25'
    max_order_notional: str = '25'
    max_loss: str = '5'
    max_drawdown: str = '5'
    min_free_fraction: str = '0.90'
    max_spread_bps: str = '15'
    slippage_bps: str = '5'
    max_oracle_deviation_bps: str = '50'
    max_fee_bps: str = '9'
    max_book_age_ms: int = 10_000
    max_snapshot_ms: int = 30_000
    decision_window_ms: int = 900_000
    poll_seconds: int = 60

    def __post_init__(self):
        if venue(self.dex).collateral != self.collateral or type(self.enabled) is not bool:
            raise ValueError('Invalid venue/collateral/enabled')
        positive = ('target_fraction', 'max_notional', 'max_order_notional', 'max_loss',
                    'max_drawdown', 'min_free_fraction', 'max_spread_bps', 'slippage_bps',
                    'max_oracle_deviation_bps', 'max_fee_bps')
        for name in positive:
            if not isinstance(getattr(self, name), str) or dec(getattr(self, name)) <= 0:
                raise ValueError('Positive Decimal strings required')
        if dec(self.target_fraction) > dec('.05') or dec(self.min_free_fraction) < dec('.9'):
            raise ValueError('Release policy exceeds conservative fraction bounds')
        if dec(self.min_free_fraction) > 1 or dec(self.max_order_notional) > dec(self.max_notional):
            raise ValueError('Invalid order/free budget')
        for name, maximum in [('max_book_age_ms', 10_000), ('max_snapshot_ms', 30_000),
                              ('decision_window_ms', 900_000), ('poll_seconds', 300)]:
            if type(getattr(self, name)) is not int or not 0 < getattr(self, name) <= maximum:
                raise ValueError('Invalid timing policy')
        if self.poll_seconds < 60:
            raise ValueError('Respect shared venue rate budget: poll >= 60s')

    @property
    def symbol(self):
        return self.dex + ':ETH'

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


def load_policy(path):
    with Path(path).open('rb') as stream:
        return Policy(**tomllib.load(stream)['funding'])


def funding_signal(records, symbol, asof_ms):
    """Only settled historical observations already received by this decision."""
    known = {}
    for row in records:
        if row['coin'] != symbol:
            raise ValueError('Funding venue mismatch')
        t, available = row['time'], row['available_ms']
        if type(t) is not int or type(available) is not int or available < t:
            raise ValueError('Invalid Funding availability')
        rate = dec(row['fundingRate'])
        if t <= asof_ms and available <= asof_ms:
            if t in known and known[t] != rate:
                raise ValueError('Conflicting settled rates')
            known[t] = rate
    if not known or asof_ms - max(known) > 2 * HOUR:
        raise ValueError('Missing or stale settled Funding')
    t = max(known)
    rate = known[t]
    return {'direction': -int(rate > 0) + int(rate < 0), 'time': t, 'rate': str(rate),
            'asof_ms': asof_ms, 'version': VERSION}


def validate_snapshot(s, p, now_ms, *, require_leverage=True):
    if s['symbol'] != p.symbol or s['collateral'] != p.collateral:
        raise ValueError('Snapshot environment mismatch')
    if not 0 <= now_ms - s['started_ms'] <= p.max_snapshot_ms:
        raise ValueError('Snapshot too old or clock moved backwards')
    if not s['started_ms'] <= s['received_ms'] <= now_ms:
        raise ValueError('Invalid local availability')
    if not 0 <= now_ms - s['book_ms'] <= p.max_book_age_ms:
        raise ValueError('Stale/future exchange book')
    if s['mode'] != 'unifiedAccount' or s['other_positions'] or s['open_orders']:
        raise ValueError('Account scope/mode/orders not ready')
    if not s['collateral_scope_verified'] or s['delisted']:
        raise ValueError('Unverified collateral scope or delisted market')
    if not s['account_consistent']:
        raise ValueError('Account changed during capture')
    if type(s['sz_decimals']) is not int or not 0 <= s['sz_decimals'] <= 6:
        raise ValueError('Invalid quantity precision')
    bid, ask, mark, oracle = map(dec, (s['bid'], s['ask'], s['mark'], s['oracle']))
    if not 0 < bid <= ask or min(mark, oracle) <= 0:
        raise ValueError('Invalid market prices')
    mid = (bid + ask) / 2
    if (ask - bid) / mid * 10000 > dec(p.max_spread_bps):
        raise ValueError('Spread above policy')
    if max(abs(mid / oracle - 1), abs(mark / oracle - 1)) * 10000 > dec(p.max_oracle_deviation_bps):
        raise ValueError('Oracle deviation above policy')
    if not 0 <= dec(s['fee_bps']) <= dec(p.max_fee_bps):
        raise ValueError('Unknown/excessive fee')
    if dec(s['equity']) <= 0 or dec(s['free']) < 0:
        raise ValueError('No verified collateral equity/free margin')
    if require_leverage and s['leverage'] != {'type': 'cross', 'value': 1}:
        raise ValueError('Explicit 1x cross leverage required')
    return mid


def make_order(s, target, p, now_ms, *, emergency=False):
    validate_snapshot(s, p, now_ms)
    current, target = dec(s['quantity']), dec(target)
    # Reverse through a reduce-only zero leg. Re-capture before opening the other side.
    reverse = current * target < 0
    delta = -current if reverse else target - current
    if not delta:
        return None
    buy = delta > 0
    reduce = current != 0 and current * delta < 0 and abs(delta) <= abs(current)
    if emergency and not reduce:
        raise ValueError('Emergency action must reduce exposure')
    unit = Decimal(10) ** -s['sz_decimals']
    qty = abs(delta).quantize(unit, rounding=ROUND_DOWN)
    touch = dec(s['ask'] if buy else s['bid'])
    cap = touch * (1 + dec(p.slippage_bps) / 10000 * (1 if buy else -1))
    price = dec(limit_price(float(cap), s['sz_decimals'], buy))
    if (buy and price > cap) or (not buy and price < cap):
        raise ValueError('Price rounding exceeds limit')
    notional = qty * price
    if qty == 0 or notional < 10:
        # Never enlarge a risk budget to satisfy the exchange minimum.
        return None
    if not reduce and notional > dec(p.max_order_notional):
        raise ValueError('Order exceeds absolute limit')
    after = current + (qty if buy else -qty)
    if not reduce:
        budget = min(dec(p.max_notional), dec(s['equity']) * dec(p.target_fraction))
        # Use the larger execution/mark value for the target cap.
        if abs(after) * max(price, dec(s['mark'])) > budget:
            raise ValueError('Target exposure exceeds policy')
        if dec(s['free']) - notional * (1 + dec(s['fee_bps']) / 10000) < (
            dec(s['equity']) * dec(p.min_free_fraction)
        ):
            raise ValueError('Insufficient verified free margin buffer')
        # Array side mapping is deliberately not assumed: require BOTH bounds.
        if qty > min(map(dec, s['max_trade_sizes'])):
            raise ValueError('Exchange maximum size exceeded')
        if notional > min(map(dec, s['available_to_trade'])):
            raise ValueError('Exchange buying power insufficient')
    depth = sum((dec(level['sz']) for level in s['levels'][1 if buy else 0]
                 if (dec(level['px']) <= price if buy else dec(level['px']) >= price)), Decimal(0))
    if depth < qty:
        raise ValueError('Insufficient executable depth')
    return {'symbol': p.symbol, 'buy': buy, 'quantity': str(qty), 'price': str(price),
            'reduce_only': reduce, 'before': str(current), 'expected_after': str(after), 'tif': 'Ioc'}


def target_quantity(s, signal, p):
    budget = min(dec(p.max_notional), dec(s['equity']) * dec(p.target_fraction))
    # Reserve slippage/mark headroom. No upside rounding to min-notional.
    price = max(dec(s['ask']), dec(s['mark'])) * (1 + dec(p.slippage_bps) / 10000)
    unit = Decimal(10) ** -s['sz_decimals']
    return str((budget / price).quantize(unit, rounding=ROUND_DOWN) * signal['direction'])
