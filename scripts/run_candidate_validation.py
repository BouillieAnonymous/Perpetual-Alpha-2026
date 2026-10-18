"""Frozen EWY/HYPE robustness study; public inputs, no production integration."""
import csv
import gzip
import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from perpetual_alpha.historical.contracts import candle
from perpetual_alpha.offline_quant.data import HOUR, Bar, contiguous
from perpetual_alpha.offline_quant.relative import funding_orders
from perpetual_alpha.offline_quant.replay import Costs, replay
from perpetual_alpha.offline_quant.run import write_json
from perpetual_alpha.offline_quant.signals import Order, efficiency, sign, single_orders

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'docs/research/new-alpha-discovery-2026-10-18/candidate-validation'


def validate_market(venue, symbol):
    allowed = {'mainnet': {'HYPE', 'xyz:EWY'}, 'par': {'par:HYPE', 'par:EWY'}}
    if symbol not in allowed.get(venue, set()):
        raise ValueError('Venue/coin mismatch; no native, PAR or PAS history substitution')


def records(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        yield from map(json.loads, stream)


def load():
    bars, funding, specs, audit = defaultdict(dict), defaultdict(dict), {}, []
    for row in records(OUT/'public-history.jsonl.gz'):
        req, venue = row['request'], row['venue']
        if 'error' in row:
            audit.append({'venue': venue, 'request': req, 'error': row['error']})
            continue
        data = row['payload']
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if digest != row['payload_sha256']:
            raise ValueError('Response digest mismatch')
        if req['type'] == 'metaAndAssetCtxs':
            for spec in data[0]['universe']:
                if spec['name'] in {'HYPE', 'xyz:EWY', 'par:HYPE', 'par:EWY'}:
                    specs[venue, spec['name']] = {'quantity_step': 10**-spec['szDecimals'], 'valuation_supported': False}
        elif req['type'] == 'candleSnapshot':
            coin, interval = req['req']['coin'], req['req']['interval']
            validate_market(venue, coin)
            for raw in data:
                item = candle(raw, coin, interval)
                if item['close_ms'] > row['received_ms']:
                    continue
                bar = Bar(coin, item['close_ms'], *[float(item[k]) for k in ['open', 'high', 'low', 'close', 'volume']], row['available_ms'])
                key = venue, coin, interval
                if bar.time in bars[key] and bars[key][bar.time] != bar:
                    raise ValueError('Conflicting bar')
                bars[key][bar.time] = bar
        elif req['type'] == 'fundingHistory':
            coin = req['coin']
            validate_market(venue, coin)
            for rate in data:
                if rate['coin'] != coin or not req['startTime'] <= rate['time'] <= req['endTime']:
                    raise ValueError('Funding identity/window mismatch')
                key = venue, coin
                value = float(rate['fundingRate'])
                if rate['time'] in funding[key] and funding[key][rate['time']] != value:
                    raise ValueError('Conflicting rate')
                funding[key][rate['time']] = value
    return {k: sorted(v.values(), key=lambda r: r.time) for k, v in bars.items()}, funding, specs, audit


def time_stats(times, step=None):
    values = sorted(set(times))
    diffs = [b-a for a, b in zip(values, values[1:])]
    return {'count': len(times), 'unique': len(values), 'first_ms': min(values) if values else None,
            'last_ms': max(values) if values else None, 'median_gap_ms': statistics.median(diffs) if diffs else None,
            'max_gap_ms': max(diffs) if diffs else None,
            'gap_count': sum(d > step for d in diffs) if step else None,
            'missing_grid_points': sum(max(0, (b//step)-(a//step)-1) for a, b in zip(values, values[1:])) if step else None}


def orders(rows, rates, kind, start):
    symbol = rows[0].symbol
    if kind in {'atr', 'donchian'}:
        return single_orders(rows, kind, active_start=start)
    if kind == 'no-trade':
        return []
    original = funding_orders({symbol: rows}, {symbol: rates}, [symbol], mode='mean24')
    if kind == 'funding-mean24':
        return original
    output, by_time = [], {r.time: i for i, r in enumerate(rows)}
    for order in original:
        target = next(iter(order.targets.values()))
        if kind == 'reverse-funding':
            target = -target
        elif kind == 'fixed-long':
            target = .2
        elif kind == 'fixed-short':
            target = -.2
        elif kind == 'momentum24':
            i = by_time[order.time]
            if i < 24 or not contiguous(rows[i-24:i+1]):
                continue
            target = .2*sign(math.log(rows[i].close/rows[i-24].close))
        else:
            raise ValueError('Unknown fixed baseline')
        output.append(Order(order.time, {symbol: target}, kind))
    return output


def windows(rows):
    first, end = rows[0].time, rows[-1].time
    n = (end-first)//HOUR
    a, b = first+int(.6*n)*HOUR, first+int(.8*n)*HOUR
    result = {'full_retrospective': (first, end), 'development_retrospective': (first, a),
              'validation_retrospective': (a+12*HOUR, b), 'test_retrospective': (b+12*HOUR, end)}
    groups = defaultdict(list)
    for row in rows:
        groups[datetime.fromtimestamp(row.time/1000, timezone.utc).strftime('%Y-%m')].append(row.time)
    for month, times in groups.items():
        if max(times) > min(times)+24*HOUR:
            result['calendar-'+month] = (min(times), min(max(times)+HOUR, end))
    return result


def predictive_rows(rows, rates):
    result = []
    by_time = {r.time: i for i, r in enumerate(rows)}
    opens = {r.time-HOUR: r.open for r in rows}
    for order in funding_orders({rows[0].symbol: rows}, {rows[0].symbol: rates}, [rows[0].symbol], mode='mean24'):
        t, i = order.time, by_time[order.time]
        if i < 24 or not contiguous(rows[i-24:i+1]) or t+HOUR not in opens or t+13*HOUR not in opens:
            continue
        # No gap bridging in the outcome either.
        if any(t+j*HOUR not in by_time for j in range(1, 15)):
            continue
        result.append({'time_ms': t, 'label_end_ms': t+13*HOUR,
                       'funding_direction': next(iter(order.targets.values()))/.2,
                       'past_momentum24': math.log(rows[i].close/rows[i-24].close),
                       'mean_funding24': order.features['known_rates'][rows[0].symbol],
                       'future_return12': math.log(opens[t+13*HOUR]/opens[t+HOUR])})
    return result


def conditional(rows, rates):
    import numpy as np
    import statsmodels.api as sm
    data = predictive_rows(rows, rates)
    if len(data) < 60:
        return {'status': 'INSUFFICIENT_DATA', 'rows': len(data)}, data
    first, end = rows[0].time, rows[-1].time
    a, b = first+int(.6*((end-first)//HOUR))*HOUR, first+int(.8*((end-first)//HOUR))*HOUR
    train = [r for r in data if r['label_end_ms'] < a]
    if len({r['funding_direction'] for r in train}) < 2:
        return {'status': 'INSUFFICIENT_DATA', 'reason': 'no-funding-direction-variation', 'rows': len(data)}, data
    def design(group, full):
        return np.array([[1., r['past_momentum24'], *([r['funding_direction']] if full else [])] for r in group])
    y = np.array([r['future_return12'] for r in train])
    base = sm.OLS(y, design(train, False)).fit(cov_type='HAC', cov_kwds={'maxlags': 2})
    augmented = sm.OLS(y, design(train, True)).fit(cov_type='HAC', cov_kwds={'maxlags': 2})
    result = {'status': 'INCONCLUSIVE', 'train_labels': len(train), 'total_labels': len(data),
              'train_funding_coefficient': float(augmented.params[-1]),
              'train_HAC2_pvalue': float(augmented.pvalues[-1]), 'train_R2_base': float(base.rsquared),
              'train_R2_augmented': float(augmented.rsquared),
              'note': 'funding_direction = -sign(mean24), return label next-hour open to +12h; retrospective; no causal claim'}
    for name, left, right in [('validation', a+12*HOUR, b), ('test', b+12*HOUR, end)]:
        group = [r for r in data if left <= r['time_ms'] and r['label_end_ms'] < right]
        if len(group) < 10:
            result[name] = {'status': 'INSUFFICIENT_DATA', 'labels': len(group)}
            continue
        target = np.array([r['future_return12'] for r in group])
        bm, am = base.predict(design(group, False)), augmented.predict(design(group, True))
        result[name] = {'labels': len(group), 'MSE_base': float(np.mean((target-bm)**2)),
                        'MSE_augmented': float(np.mean((target-am)**2)),
                        'direction_accuracy_base': float(np.mean(np.sign(target) == np.sign(bm))),
                        'direction_accuracy_augmented': float(np.mean(np.sign(target) == np.sign(am))),
                        'funding_standalone_signed_mean_bps': statistics.mean(r['funding_direction']*r['future_return12'] for r in group)*10000}
    # Descriptive trend-strata check; fixed at negative/nonnegative past momentum.
    for state in ['negative', 'nonnegative']:
        group = [r for r in data if (r['past_momentum24'] < 0) == (state == 'negative')]
        result['trend_'+state] = {'labels': len(group), 'signed_mean_bps':
                                 statistics.mean(r['funding_direction']*r['future_return12'] for r in group)*10000 if group else None}
    return result, data


def run():
    bars, funding, specs, errors = load()
    summaries, state_rows = [], []
    with gzip.open(OUT/'ledgers.jsonl.gz', 'wt', encoding='utf-8', newline='\n') as stream:
        for (venue, symbol, interval), rows in bars.items():
            if interval != '1h':
                continue
            rules = ['atr', 'donchian', 'no-trade'] if 'EWY' in symbol else [
                'funding-mean24', 'reverse-funding', 'momentum24', 'fixed-long', 'fixed-short', 'no-trade']
            fee = 4.5 if symbol == 'HYPE' and venue == 'mainnet' else 9
            for kind in rules:
                # Funding rules/controls are stateless at each rebalance. Cache past-only
                # orders once, then discard decisions at/after each window's end.
                cached = None if 'EWY' in symbol else orders(rows, funding[venue, symbol], kind, rows[0].time)
                for split, (start, end) in windows(rows).items():
                    # Exactly original bar truncation: no feature sees bars at/after end.
                    truncated = [r for r in rows if r.time < end]
                    signal = (orders(truncated, funding[venue, symbol], kind, start) if cached is None else
                              [o for o in cached if o.time < end])
                    for scenario, cost in [('fee_only', Costs(fee_bps=fee, spread_bps=0, slippage_bps=0)),
                                           ('base', Costs(fee_bps=fee)),
                                           ('stress', Costs(fee_bps=2*fee, spread_bps=18, slippage_bps=4, delay_hours=2))]:
                        result = replay({symbol: rows}, {symbol: funding[venue, symbol]}, {symbol: specs[venue, symbol]},
                                        signal, start, end, cost)
                        summary = result['summary']
                        net = summary['net_scenario_return']
                        status = ('INSUFFICIENT_DATA' if not summary['scenario_complete'] or not summary['fill_count']
                                  else 'REJECTED' if net < 0 else 'INCONCLUSIVE')
                        if kind == 'no-trade':
                            status = 'INCONCLUSIVE'
                        identity = {'venue': venue, 'symbol': symbol, 'rule': kind, 'split': split, 'scenario': scenario}
                        summaries.append({**identity, 'status': status, **summary})
                        stream.write(json.dumps({'identity': identity, **result}, allow_nan=False)+'\n')
                        if 'EWY' in symbol and scenario == 'base' and split == 'full_retrospective':
                            mapping = {r.time: i for i, r in enumerate(rows)}
                            grouped = defaultdict(list)
                            for trade in result['trades']:
                                i = mapping.get(trade['entry_ms']-cost.delay_hours*HOUR)
                                state = 'unknown' if i is None or i < 24 else (
                                    'trending' if efficiency(rows[i-24:i+1]) > .35 else 'range')
                                grouped[state].append(trade)
                            for state, trades in grouped.items():
                                state_rows.append({**identity, 'past_only_state': state, 'closed_segments': len(trades),
                                                   **{k: sum(t[k] for t in trades) for k in
                                                      ['gross_price_pnl', 'fees', 'funding_scenario', 'net_scenario']}})
            print(venue, symbol, 'fixed backtests complete', flush=True)
    write_json(OUT/'summary.json', summaries)
    with (OUT/'comparison.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        fields = [k for k in summaries[0] if k not in {'costs', 'unclosed_positions'}]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(summaries)
    write_json(OUT/'ewy-trend-attribution.json', state_rows)
    models = {}
    for venue, symbol in [('mainnet', 'HYPE'), ('par', 'par:HYPE')]:
        models[venue], labels = conditional(bars[venue, symbol, '1h'], funding[venue, symbol])
        write_json(OUT/(venue+'-conditional-labels.json'), labels)
    write_json(OUT/'conditional-funding.json', models)
    quality = {'errors': errors, 'bars': {}, 'funding': {}}
    for key, rows in bars.items():
        quality['bars']['/'.join(key)] = {**time_stats([r.time for r in rows], HOUR if key[2] == '1h' else 24*HOUR),
                                        'zero_volume_bars': sum(r.volume == 0 for r in rows),
                                        'available_ms_min': min(r.received for r in rows),
                                        'available_ms_max': max(r.received for r in rows)}
    for key, values in funding.items():
        quality['funding']['/'.join(key)] = time_stats(list(values), HOUR)
    vps = json.loads(gzip.open(OUT/'vps-readonly-export.json.gz', 'rt', encoding='utf-8').read())
    quality['vps'] = {'observed_ms': vps['observed_ms'], 'schema': vps['schema'], 'assets': {}}
    for symbol in ['par:EWY', 'par:HYPE']:
        rows = [r for r in vps['rows'] if r['symbol'] == symbol]
        quality['vps']['assets'][symbol] = {**time_stats([r['event_ms'] for r in rows], 60000),
                                           'received': time_stats([r['received_ms'] for r in rows]),
                                           'book_context_skew_gt2s': sum(abs(r['received_ms']-r['context_received_ms']) > 2000 for r in rows),
                                           'depth_rows': sum(bool(r.get('raw', {}).get('book', {}).get('levels')) for r in rows),
                                           'oracle_event_time_known': False,
                                           'note': 'snapshot key(symbol,event_ms) suppresses unchanged-book observations; not a complete event stream'}
    write_json(OUT/'quality.json', quality)
    print('experiments:', len(summaries), flush=True)


if __name__ == '__main__':
    run()
