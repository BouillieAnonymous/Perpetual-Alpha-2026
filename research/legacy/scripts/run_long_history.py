"""Replay frozen rules on separately identified long-history external markets."""
import csv
import gzip
import hashlib
import json
from collections import defaultdict
from dataclasses import replace

from long_history_protocol import MAPPING, OUT, RESEARCH
from run_asset_coverage import external_specs
from run_candidate_validation import records, time_stats, windows

from perpetual_alpha.historical.contracts import candle
from perpetual_alpha.market_transfer import load_external
from perpetual_alpha.offline_quant.data import HOUR, Bar
from perpetual_alpha.offline_quant.replay import Costs, replay
from perpetual_alpha.offline_quant.run import orders_for, write_json
from perpetual_alpha.offline_quant.signals import Order, efficiency


def load_sources():
    bars, rates, specs, evidence, contexts = {}, defaultdict(dict), {}, [], {}
    path = RESEARCH/'external-transfer/external-history.jsonl.gz'
    native, _ = load_external(path)
    for symbol, rows in native.items():
        asset = symbol.split(':')[1]
        bars[asset] = rows
        specs[asset] = external_specs(path)[symbol]
    expected = {coin: asset for asset, (_, coin) in MAPPING.items()}
    sources = [('candidate-validation/public-history.jsonl.gz', {'HYPE', 'xyz:EWY'}),
               ('long-history-37/additional-public-history.jsonl.gz', set(expected)-{'HYPE', 'xyz:EWY'})]
    for name, allowed in sources:
        for row in records(RESEARCH/name):
            req = row['request']
            if row['venue'] != 'mainnet':
                if req['type'] == 'metaAndAssetCtxs' and row.get('payload'):
                    contexts['par'] = {s['name']: c for s, c in zip(row['payload'][0]['universe'], row['payload'][1])}
                continue
            if row.get('error'):
                evidence.append({'source': name, 'request': req, 'error': row['error']})
                continue
            data = row['payload']
            digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            if digest != row['payload_sha256']:
                raise ValueError('Source response digest mismatch')
            if req['type'] == 'metaAndAssetCtxs':
                for raw, ctx in zip(data[0]['universe'], data[1]):
                    if raw['name'] in expected:
                        asset = expected[raw['name']]
                        specs[asset] = {'quantity_step': 10**-raw['szDecimals'], 'valuation_supported': False}
                        contexts[raw['name']] = ctx
            elif req['type'] == 'candleSnapshot' and req['req']['coin'] in allowed and req['req']['interval'] == '1h':
                coin = req['req']['coin']
                asset = expected[coin]
                rows = []
                for raw in data:
                    item = candle(raw, coin, '1h')
                    if not req['req']['startTime'] <= item['open_ms'] <= req['req']['endTime']:
                        raise ValueError('Candle request window mismatch')
                    if item['close_ms'] <= row['received_ms']:
                        rows.append(Bar('par:'+asset, item['close_ms'], *[float(item[k]) for k in
                                        ['open', 'high', 'low', 'close', 'volume']], row['available_ms']))
                if asset in bars:
                    raise ValueError('Two price archives cannot silently replace one another')
                bars[asset] = sorted(rows, key=lambda r: r.time)
            elif req['type'] == 'fundingHistory' and req['coin'] in allowed:
                coin = req['coin']
                asset = expected[coin]
                for raw in data:
                    if raw['coin'] != coin or not req['startTime'] <= raw['time'] <= req['endTime']:
                        raise ValueError('Funding venue/instrument/window mismatch')
                    t, value = raw['time'], float(raw['fundingRate'])
                    if t in rates[asset] and rates[asset][t] != value:
                        raise ValueError('Conflicting funding vintage')
                    rates[asset][t] = value
    quality = {}
    for asset in MAPPING:
        rows = bars.get(asset, [])
        quality[asset] = {'real_coin': MAPPING[asset][1], 'venue': 'Hyperliquid mainnet '+MAPPING[asset][0],
                          'bars': time_stats([r.time for r in rows], HOUR), 'funding': time_stats(list(rates[asset]), HOUR),
                          'quantity_step': specs.get(asset, {}).get('quantity_step'),
                          'zero_volume_bars': sum(r.volume == 0 for r in rows),
                          'available_ms_min': min((r.received for r in rows), default=None),
                          'mapping_status': 'conditional reference counterpart, not same execution venue',
                          'unit_warning': 'old PAR USDJPY catalog says JPY/USD; do not infer inversion from name alone' if asset == 'USDJPY' else None}
    # Retain actual same-time current oracle comparison, not an assumed FX inversion.
    if 'xyz:JPY' in contexts and 'par:USDJPY' in contexts.get('par', {}):
        p = float(contexts['par']['par:USDJPY']['oraclePx'])
        x = float(contexts['xyz:JPY']['oraclePx'])
        quality['USDJPY']['current_oracle_unit_check'] = {'par': p, 'xyz': x, 'ratio': p/x,
                                                         'inverse_product': p*x,
                                                         'same_scale': .5 < p/x < 2}
    return bars, rates, specs, quality, evidence


def baseline(rows, start, end, side):
    return [Order(r.time, {r.symbol: side*.2}, 'fixed-direction-12h') for i, r in enumerate(rows)
            if i >= 25 and start <= r.time < end and r.time//HOUR % 12 == 0]


def matched(signal, side):
    return [replace(o, targets={s: side*abs(v) for s, v in o.targets.items()}, reason='matched-direction') for o in signal]


def daily_pnl(result):
    closes = {}
    for row in result['equity']:
        closes[(row['time_ms']-1)//(24*HOUR)] = row['equity_scenario']
    result, previous = {}, 10000.
    for day, value in sorted(closes.items()):
        result[str(day)] = (value-previous)/10000
        previous = value
    return result


def run():
    bars, funding, specs, quality, errors = load_sources()
    write_json(OUT/'source-quality.json', quality)
    write_json(OUT/'capture-errors.json', errors)
    registry = json.loads((OUT/'registry-170.json').read_text(encoding='utf-8'))
    summaries, states, daily = [], [], {}
    for asset in MAPPING:
        symbol, real_coin = 'par:'+asset, MAPPING[asset][1]
        rows = bars.get(asset, [])
        if len(rows) < 100 or asset not in specs:
            print(asset, 'no sufficient price history', flush=True)
            continue
        own_rates = {symbol: funding[asset]}
        fee = 4.5 if MAPPING[asset][0] == 'native' else 9
        costs = {'base': Costs(fee_bps=fee), 'fee_only': Costs(fee_bps=fee, spread_bps=0, slippage_bps=0),
                 'stress': Costs(fee_bps=2*fee, spread_bps=18, slippage_bps=4, delay_hours=2)}
        groups = [r for r in registry if r['asset'] == asset]
        definitions = [(r['rule'], r['definition'], r['selected']) for r in groups]
        definitions += [(name, None, False) for name in ['FIXED-LONG', 'FIXED-SHORT', 'NO-TRADE']]
        archive = OUT/(asset+'-ledgers.jsonl.gz')
        count = 0
        with gzip.open(archive, 'wt', encoding='utf-8', newline='\n') as stream:
            for name, definition, selected in definitions:
                # Cache stateless funding decisions; preserve every original timestamp and target.
                cached = (orders_for(definition, {symbol: rows}, own_rates, {}, rows[0].time, rows[-1].time)
                          if definition and definition['kind'] == 'funding' else None)
                for split, (start, end) in windows(rows).items():
                    full = split == 'full_retrospective'
                    benchmark = definition is None
                    if not (selected or benchmark or full):
                        continue  # full/base negative/control rows retained for ALL170
                    if definition is None:
                        side = {'FIXED-LONG': 1, 'FIXED-SHORT': -1, 'NO-TRADE': 0}[name]
                        signal = baseline(rows, start, end, side) if side else []
                    elif cached is not None:
                        signal = [o for o in cached if o.time < end]
                    else:
                        signal = orders_for(definition, {symbol: rows}, own_rates, {}, start, end)
                    scenarios = ['base'] if split.startswith('calendar') or not (selected or benchmark) else list(costs)
                    plans = [(name, signal, c) for c in scenarios]
                    if selected and not split.startswith('calendar'):
                        plans += [(name+'::MATCHED-LONG', matched(signal, 1), 'base'),
                                  (name+'::MATCHED-SHORT', matched(signal, -1), 'base')]
                    for label, signals, scenario in plans:
                        cost = costs[scenario]
                        result = replay({symbol: rows}, own_rates, {symbol: specs[asset]}, signals, start, end, cost)
                        summary = result['summary']
                        identity = {'asset': asset, 'rule': label, 'split': split, 'scenario': scenario,
                                    'external_coin': real_coin, 'venue': 'mainnet', 'selected_original': selected}
                        status = ('INSUFFICIENT_DATA' if not summary['scenario_complete'] or not summary['fill_count']
                                  else 'LONG_HISTORY_REJECTED' if summary['net_scenario_return'] <= 0 else 'INCONCLUSIVE')
                        if label == 'NO-TRADE':
                            status = 'INCONCLUSIVE'
                        summaries.append({**identity, 'start_ms': start, 'end_ms': end, 'hours': (end-start)//HOUR,
                                          'window_status': status, **summary})
                        stream.write(json.dumps({'identity': identity, **result}, allow_nan=False)+'\n')
                        count += 1
                        if full and scenario == 'base':
                            daily[asset+'|'+label] = daily_pnl(result)
                            if selected and '::' not in label:
                                by_time = {r.time: i for i, r in enumerate(rows)}
                                buckets = defaultdict(list)
                                for trade in result['trades']:
                                    i = by_time.get(trade['entry_ms']-HOUR)
                                    state = ('unknown' if i is None or i < 24 else
                                             'trending' if efficiency(rows[i-24:i+1]) > .35 else 'range')
                                    buckets[state].append(trade)
                                for state, trades in buckets.items():
                                    states.append({'asset': asset, 'rule': label, 'state': state, 'closed_segments': len(trades),
                                                   **{k: sum(t[k] for t in trades) for k in
                                                      ['gross_price_pnl', 'fees', 'funding_scenario', 'net_scenario']}})
                print(asset, name, 'done', flush=True)
        print(asset, count, 'ledger runs', flush=True)
    write_json(OUT/'summary.json', summaries)
    write_json(OUT/'state-attribution.json', states)
    write_json(OUT/'daily-pnl.json', daily)
    with (OUT/'comparison.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=[k for k in summaries[0] if k not in {'costs', 'unclosed_positions'}],
                                extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(summaries)
    print('Total fixed experimental ledgers', len(summaries), flush=True)


if __name__ == '__main__':
    run()
