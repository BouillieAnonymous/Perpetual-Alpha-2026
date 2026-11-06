"""Read archived evidence; allocate all170, then strictly past-fit causal overlays."""
import csv
import gzip
import json
import statistics
from collections import Counter, defaultdict

from regime_core import choose, classify, extract, metrics, month_of, overlay
from regime_protocol import DAY, FAMILIES, OUT, PRIOR, STATES
from run_candidate_validation import windows
from run_long_history import baseline, daily_pnl, load_sources

from perpetual_alpha.offline_quant.data import source_hash
from perpetual_alpha.offline_quant.replay import Costs, replay
from perpetual_alpha.offline_quant.run import orders_for, write_json


def read_full(asset):
    with gzip.open(PRIOR/(asset+'-ledgers.jsonl.gz'), 'rt', encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            ident = row['identity']
            if ident['split'] == 'full_retrospective' and ident['scenario'] == 'base' and '::' not in ident['rule']:
                yield ident['rule'], row


def run():
    if (OUT/'runs.json').exists():
        raise FileExistsError('Preserve completed experiment; rebuild in an archived copy')
    protocol = json.loads((OUT/'protocol.json').read_text())
    bars, funding, specs, quality, _ = load_sources()
    registry = json.loads((PRIOR/'registry-170.json').read_text())
    write_json(OUT/'implementation-note.json', {
        'written_before_new_results': True,
        'forward_shadow': 'each test block regenerates unchanged three rules with active_start=test_start and end=test_end, warmup uses earlier bars; zero initial holdings',
        'training_labels': 'original full-strategy, state-hour past PnL, all labels matured before training/validation end; completed position episodes counted separately',
        'attribution_drawdown': 'mark-PnL attributed-hour path zeroed outside state, NOT executable filtered-strategy drawdown',
        'baseline_identity': 'FIXED orders same regenerated original rule; overlay zero initial order harmless, executable PnL verified independently',
        'inputs': {p.name: source_hash(p) for p in PRIOR.glob('*-ledgers.jsonl.gz') if p.name!='nonoverlap-ledgers.jsonl.gz'}})
    write_json(OUT/'source-quality.json', quality)
    conditional, audits, runs, daily, feature_counts = [], [], [], {}, {}
    with gzip.open(OUT/'classifier.jsonl.gz', 'wt', encoding='utf-8', newline='\n') as features, \
            gzip.open(OUT/'forward-ledgers.jsonl.gz', 'wt', encoding='utf-8', newline='\n') as ledger:
        for asset, series in bars.items():
            states = classify(series)
            feature_counts[asset] = dict(Counter(v['state'] for v in states.values()))
            for t, row in states.items():
                features.write(json.dumps({'asset': asset, 'time_ms': t, **row}, allow_nan=False)+'\n')
            rules = {r['rule']: r['definition'] for r in registry if r['asset'] == asset}
            extracted = {}
            for rule, detail in read_full(asset):
                h, e, triggers = extract(detail, states)
                extracted[rule] = h, e, triggers
                for split, (start, end) in windows(series).items():
                    for state in STATES+['UNKNOWN']:
                        m = metrics(h, e, triggers, state, start, end)
                        m.pop('daily')
                        conditional.append({'asset': asset, 'rule': rule, 'state': state, 'split': split,
                                            'start_ms': start, 'end_ms': end, **m})
            assert set(rules) <= set(extracted)
            for fold in protocol['folds']:
                mapping, audit = choose(extracted, fold)
                audits.append({'asset': asset, 'fold': fold['name'], 'mapping': mapping, 'audit': audit, **fold})
                start, end = fold['test']
                symbol = 'par:'+asset
                own = {symbol: funding[asset]}
                shadows = {rule: [{'time': o.time, 'targets': o.targets} for o in orders_for(
                    rules[rule], {symbol: series}, own, {}, start, end)] for rule in FAMILIES}
                modes = ['FLAT', 'FIXED-LONG', 'FIXED-SHORT']
                modes += ['FIXED:'+r for r in FAMILIES]+['FILTER:'+r for r in FAMILIES]+['SELECTOR']
                for mode in modes:
                    if mode in {'FIXED-LONG', 'FIXED-SHORT'}:
                        signal = baseline(series, start, end, 1 if mode == 'FIXED-LONG' else -1)
                        metadata = {'family_switches_including_flat': 0, 'decisions': []}
                    else:
                        signal, metadata = overlay(shadows, states, symbol, start, end, mode, mapping)
                    fee = 4.5 if asset in {'BTC', 'ETH', 'HYPE'} else 9
                    for scenario, cost in [('base', Costs(fee_bps=fee)),
                                           ('stress', Costs(fee_bps=2*fee, spread_bps=18, slippage_bps=4, delay_hours=2))]:
                        result = replay({symbol: series}, own, {symbol: specs[asset]}, signal, start, end, cost)
                        if mode.startswith('FIXED:'):
                            original_orders = orders_for(rules[mode.split(':')[1]], {symbol: series}, own, {}, start, end)
                            check = replay({symbol: series}, own, {symbol: specs[asset]}, original_orders, start, end, cost)
                            assert result['summary']['known_components_return'] == check['summary']['known_components_return']
                        identity = {'asset': asset, 'fold': fold['name'], 'mode': mode, 'scenario': scenario,
                                    'start_ms': start, 'end_ms': end}
                        s = result['summary']
                        d = daily_pnl(result)
                        row = {**identity, **s, 'family_switches_including_flat': metadata['family_switches_including_flat'],
                               'daily_vol_on_initial': statistics.stdev(d.values()) if len(d)>1 else None,
                               'months': dict(sum_months(d)),
                               'status': 'INSUFFICIENT_DATA' if not s['scenario_complete'] or not s['fill_count'] else
                                         'REJECTED' if s['net_scenario_return']<=0 else 'INCONCLUSIVE'}
                        runs.append(row)
                        daily['|'.join([asset, fold['name'], mode, scenario])] = d
                        ledger.write(json.dumps({'identity': identity, 'selector_metadata': metadata, **result}, allow_nan=False)+'\n')
                print(asset, fold['name'], 'complete', mapping, flush=True)
    # Same-venue fixed-direction conditional benchmarks, same time-state clock.
    full_lookup = {(r['asset'], r['rule'], r['state'], r['split']): r for r in conditional}
    for row in conditional:
        row['fixed_direction_state_returns'] = {side: full_lookup[row['asset'], side, row['state'], row['split']]['return_on_initial']
                                                for side in ['FIXED-LONG', 'FIXED-SHORT', 'NO-TRADE']}
    write_json(OUT/'conditional.json', conditional)
    csv_rows = [{k: v for k, v in r.items() if not isinstance(v, dict)} for r in conditional]
    with (OUT/'conditional.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(csv_rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(csv_rows)
    write_json(OUT/'selection-audit.json', audits)
    write_json(OUT/'runs.json', runs)
    write_json(OUT/'daily.json', daily)
    write_json(OUT/'state-coverage.json', feature_counts)
    print(len(conditional), 'conditional cells;', len(runs), 'forward ledgers', flush=True)


def sum_months(daily):
    months = defaultdict(float)
    for day, value in daily.items():
        months[month_of(int(day)*DAY)] += value
    return months


if __name__ == '__main__':
    run()
