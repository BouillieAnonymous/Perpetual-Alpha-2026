"""Independent accounting and causal checks for the weekly exploratory screen."""
import gzip
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from study_pas_weekly_alpha import execution, funding_integral, load, panel, payoff, quantity, signal


def quote(t, event=None, bid=100., ask=101., seq=1):
    return dict(t=t, event=t if event is None else event, bid=bid, ask=ask,
                bs=100., az=100., mid=(bid+ask)/2, seq=seq, imbalance=.3, decimals=3)


def checks():
    # Known touch-price/fee direction; the short is not the negation of a long.
    a, b = quote(1), quote(2, bid=103., ask=104.)
    x, y = payoff(a, b, 1, 1, .1), payoff(a, b, 1, -1, .1)
    assert abs(x['net'] - 1.67560036) < 1e-10
    assert x['spread'] > 0 and y['spread'] > 0 and x['net'] + y['net'] < 0
    assert quantity(100, 2400, 4) == .0416
    # A receipt of an unchanged event cannot fill; future fresh event can.
    qs = [quote(60001, event=100), quote(120001, event=110000)]
    found, _ = execution(qs, [q['t'] for q in qs], 60000, 100, 1)
    assert found[0]['event'] == 110000
    blocked = deepcopy(qs)
    blocked[1]['az'] = .001
    assert execution(blocked, [q['t'] for q in blocked], 60000, 100, 1)[1] == 'entry_depth'
    # Future settlement is unavailable to the signal; future rows cannot change old features.
    books = {'pas:BTC': [quote(60000*i+1000, seq=i) for i in range(1, 82)]}
    contexts = {'pas:BTC': [dict(t=60000*i, oracle=100., funding=-.001, seq=i) for i in range(1, 82)]}
    funds = {'pas:BTC': [dict(t=3600001, event=3600000, rate=.002, seq=99)]}
    p, _, _, _ = panel(books, contexts, funds, start=120000, end=4800000)
    q, _, _, _ = panel({'pas:BTC': books['pas:BTC'][:65]},
                        {'pas:BTC': contexts['pas:BTC'][:65]}, funds,
                        start=120000, end=3840000)
    assert p['pas:BTC'][:len(q['pas:BTC'])] == q['pas:BTC']
    assert next(x for x in p['pas:BTC'] if x and x['t']==3600000)['funding_last'] is None
    assert next(x for x in p['pas:BTC'] if x and x['t']==3660000)['funding_last'] == .002
    assert signal({'funding_last': -.001}, 'funding_last') == 1
    integral, missing = funding_integral([dict(event=3600050, rate=.001)],
                                         [dict(t=3599000, oracle=100.)], [3599000], 3500000, 3700000)
    assert integral == .1 and not missing
    assert funding_integral([], [], [], 3500000, 3700000)[1]
    print('Causal prefix, delayed funding, new-event/depth fill and cost direction checks passed.')


def verify(output):
    books, _, _, _, manifest = load(Path('.cache/alpha-week-2026-10-26/pas-prefix.jsonl.gz'))
    by_seq = {q['seq']: q for qs in books.values() for q in qs}
    count = 0
    last = {}
    with gzip.open(output/'trades.jsonl.gz', 'rt', encoding='utf-8') as f:
        for line in f:
            t = json.loads(line)
            key = (t['symbol'], t['rule'], t['hold_minutes'], t['split'])
            assert t['signal_ms'] < t['entry_ms'] < t['exit_ms']
            assert t['signal_ms'] > last.get(key, 0)
            assert t['holding_minutes'] >= t['hold_minutes']
            last[key] = t['exit_ms']
            q, side = Decimal(str(t['qty'])), t['side']
            step = Decimal(10) ** (-manifest['specs'][t['symbol']])
            assert q % step == 0
            a, b = by_seq[t['entry_seq']], by_seq[t['exit_seq']]
            assert t['entry_ms'] == a['t'] and t['exit_ms'] == b['t']
            assert a['event'] < b['event']
            assert t['qty'] <= a['az' if side == 1 else 'bs'] + 1e-12
            assert t['qty'] <= b['bs' if side == 1 else 'az'] + 1e-12
            et = Decimal(str(t['entry_ask'] if side == 1 else t['entry_bid']))
            xt = Decimal(str(t['exit_bid'] if side == 1 else t['exit_ask']))
            ep = et * (1 + side * Decimal('.0002'))
            xp = xt * (1 - side * Decimal('.0002'))
            assert Decimal('10') <= q * et
            assert q * ep <= Decimal('100.00000001')
            fee = q * (ep + xp) * Decimal('.0009')
            funding = -side*q*Decimal(str(t['funding_integral']))
            net = side*q*(xp-ep)-fee+funding
            assert abs(net-Decimal(str(t['net']))) < Decimal('0.00000001')
            assert abs(t['mid_gross']-t['spread']-t['slippage']-t['fees']+t['funding']-t['net']) < 1e-9
            count += 1
    print('All', count, 'trades independently reconciled with Decimal, raw book depth/lot/size and no overlap.')


if __name__ == '__main__':
    checks()
    output = Path('docs/research/weekly-synthesis-2026-10-26/alpha-screen')
    if (output/'trades.jsonl.gz').exists():
        verify(output)
