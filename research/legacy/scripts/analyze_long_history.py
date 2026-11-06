"""All-37 evidence matrix, dependent daily excess uncertainty and source boundaries."""
import csv
import gzip
import json
import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone

from long_history_protocol import OUT
from run_candidate_validation import time_stats

from perpetual_alpha.offline_quant.data import HOUR
from perpetual_alpha.offline_quant.run import write_json


def interval(values):
    if len(values) < 60:
        return {'days': len(values), 'mean': statistics.mean(values) if values else None,
                'lower95': None, 'upper95': None}
    rng, n, samples = random.Random(20261018), len(values), []
    for _ in range(2000):
        total, count = 0., 0
        while count < n:
            start = rng.randrange(n)
            for j in range(min(5, n-count)):
                total += values[(start+j) % n]
            count += min(5, n-count)
        samples.append(total/n)
    samples.sort()
    return {'days': n, 'mean': statistics.mean(values), 'lower95': samples[49], 'upper95': samples[1949],
            'method': 'circular moving5day bootstrap2000 seed20261018; descriptive after selection'}


def context_quality():
    result = {}
    for path in sorted(OUT.glob('context-*.json.gz')):
        data = json.loads(gzip.open(path, 'rt', encoding='utf-8').read())
        chart = data.get('chart', {})
        if chart.get('error') or not chart.get('result'):
            result[path.name] = {'status': 'INSUFFICIENT_DATA', 'reason': chart.get('error')}
            continue
        row = chart['result'][0]
        q = row['indicators']['quote'][0]
        times = [t*1000 for i, t in enumerate(row['timestamp'])
                 if all(q[k][i] is not None for k in ['open', 'high', 'low', 'close', 'volume'])]
        run, maximum, previous = 0, 0, None
        for t in times:
            run = run+1 if previous is not None and t-previous == HOUR else 1
            maximum, previous = max(maximum, run), t
        result[path.name] = {**time_stats(times), 'null_rows': len(row['timestamp'])-len(times),
                             'symbol': row['meta']['symbol'], 'currency': row['meta'].get('currency'),
                             'timezone': row['meta'].get('exchangeTimezoneName'), 'max_consecutive_hours': maximum,
                             'has_adjclose': 'adjclose' in row['indicators'],
                             'dividends': len(row.get('events', {}).get('dividends', {})),
                             'splits': len(row.get('events', {}).get('splits', {})),
                             'role': 'context only: aggregate/ETF/rolling-future/spot is not the chosen perpetual execution/funding venue'}
    return result


def analyze():
    summaries = [r for r in json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
                 if r['split'] != 'pre_PAR_selection_retrospective']
    summaries += json.loads((OUT/'nonoverlap-summary.json').read_text(encoding='utf-8'))
    for row in summaries:
        spread, slip = row['costs']['spread_bps']/2, row['costs']['slippage_bps']
        total = spread+slip
        row['spread_cost_attributed'] = row['spread_slippage_cost']*spread/total if total else 0.
        row['slippage_cost_attributed'] = row['spread_slippage_cost']*slip/total if total else 0.
    write_json(OUT/'summary.json', summaries)
    with (OUT/'comparison.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=[k for k in summaries[0] if k not in {'costs', 'unclosed_positions'}],
                                extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(summaries)
    registry = json.loads((OUT/'registry-170.json').read_text(encoding='utf-8'))
    daily = json.loads((OUT/'daily-pnl.json').read_text(encoding='utf-8'))
    quality = json.loads((OUT/'source-quality.json').read_text(encoding='utf-8'))
    keys = {(r['asset'], r['rule'], r['split'], r['scenario']): r for r in summaries}
    matrix, full_universe = [], []
    for original in registry:
        asset, rule = original['asset'], original['rule']
        row = keys.get((asset, rule, 'full_retrospective', 'base'))
        full_universe.append({'asset': asset, 'rule': rule, 'original_status': original['original']['status'],
                              'selected_original': original['selected'], 'long_full_base': row,
                              'missing_reason': None if row else 'no-comparable-long-source'})
        if not original['selected']:
            continue
        q = quality[asset]
        days = ((q['bars']['last_ms']-q['bars']['first_ms'])/(24*HOUR)) if q['bars']['first_ms'] else 0
        if row is None or row['net_scenario_return'] is None or not row['fill_count'] or days < 90:
            status, reason = 'INSUFFICIENT_DATA', 'missing-or-incomplete-ledger-or-insufficient-history'
        elif row['net_scenario_return'] <= 0:
            status, reason = 'LONG_HISTORY_REJECTED', 'negative-full-external-base-cost-scenario'
        else:
            status, reason = 'INCONCLUSIVE', 'positive-retrospective-scenario-not-independent-validation'
        old = original['original']
        result = {'asset': asset, 'rule': rule, 'structural_equivalence_group': original['structural_equivalence_group'],
                  'random_control': original['negative_control_rule'], 'original_PAR_net': float(old['net_scenario_return']),
                  'external_coin': q['real_coin'], 'external_history_days': days, 'long_status': status, 'reason': reason,
                  'full_base': row, 'future_PAS_status': 'INSUFFICIENT_DATA:no-transfer-or-prospective-validation'}
        if row:
            result['full_stress'] = keys.get((asset, rule, 'full_retrospective', 'stress'))
            result['full_fee_only'] = keys.get((asset, rule, 'full_retrospective', 'fee_only'))
            result['validation'] = keys.get((asset, rule, 'validation_retrospective', 'base'))
            result['test'] = keys.get((asset, rule, 'test_retrospective', 'base'))
            result['pre_selection'] = keys.get((asset, rule, 'pre_PAR_selection_retrospective', 'base'))
            strategy_daily = daily.get(asset+'|'+rule, {})
            months = defaultdict(float)
            for d, value in strategy_daily.items():
                month = datetime.fromtimestamp(int(d)*86400, timezone.utc).strftime('%Y-%m')
                months[month] += value
            result['continuous_monthly_return_on_initial_capital'] = dict(months)
            result['positive_months'] = sum(v > 0 for v in months.values())
            result['month_count'] = len(months)
            result['simple_benchmarks'] = {}
            for side in ['FIXED-LONG', 'FIXED-SHORT', 'NO-TRADE']:
                result['simple_benchmarks'][side] = keys.get((asset, side, 'full_retrospective', 'base'))
            result['daily_excess'] = {}
            for side in ['FIXED-LONG', 'FIXED-SHORT', rule+'::MATCHED-LONG', rule+'::MATCHED-SHORT']:
                benchmark = daily.get(asset+'|'+side, {})
                common = sorted(set(strategy_daily) & set(benchmark), key=int)
                result['daily_excess'][side] = interval([strategy_daily[t]-benchmark[t] for t in common])
            stress = result['full_stress']
            valid, test = result['validation'], result['test']
            result['future_followup_gate_pass'] = bool(
                status == 'INCONCLUSIVE' and not result['random_control']
                and stress and stress['net_scenario_return'] is not None and stress['net_scenario_return'] > 0
                and valid and valid['net_scenario_return'] is not None and valid['net_scenario_return'] > 0
                and test and test['net_scenario_return'] is not None and test['net_scenario_return'] > 0
                and result['month_count'] and result['positive_months']/result['month_count'] >= .6
                and row['entry_signals'] >= 30
                and all(result['daily_excess'][s]['lower95'] is not None and result['daily_excess'][s]['lower95'] > 0
                        for s in ['FIXED-LONG', 'FIXED-SHORT']))
            checks = {
                'positive_full_base_complete': status == 'INCONCLUSIVE',
                'not_random_control': not result['random_control'],
                'positive_stress': bool(stress and stress['net_scenario_return'] is not None and stress['net_scenario_return'] > 0),
                'positive_validation': bool(valid and valid['net_scenario_return'] is not None and valid['net_scenario_return'] > 0),
                'positive_test': bool(test and test['net_scenario_return'] is not None and test['net_scenario_return'] > 0),
                'at_least60pct_positive_months': bool(result['month_count'] and result['positive_months']/result['month_count'] >= .6),
                'at_least30_entry_signals': row['entry_signals'] >= 30,
                'excess_CI_lower_gt0_both_directions': all(result['daily_excess'][s]['lower95'] is not None
                    and result['daily_excess'][s]['lower95'] > 0 for s in ['FIXED-LONG', 'FIXED-SHORT'])}
            assert result['future_followup_gate_pass'] == all(checks.values())
            result['future_followup_failed_checks'] = [k for k, value in checks.items() if not value]
        else:
            result['future_followup_gate_pass'] = False
        matrix.append(result)
    assert len(matrix) == 37 and len(full_universe) == 170
    write_json(OUT/'matrix-37.json', matrix)
    write_json(OUT/'long-universe-170.json', full_universe)
    write_json(OUT/'context-quality.json', context_quality())
    flat = []
    for r in matrix:
        row = r['full_base'] or {}
        flat.append({'asset': r['asset'], 'rule': r['rule'], 'equivalence_group': r['structural_equivalence_group'],
                     'old_PAR_net': r['original_PAR_net'], 'coin': r['external_coin'], 'days': r['external_history_days'],
                     'long_status': r['long_status'], 'reason': r['reason'], 'future_followup_gate_pass': r['future_followup_gate_pass'],
                     'future_followup_failed_checks': ';'.join(r.get('future_followup_failed_checks', ['missing-comparable-data'])),
                     **{k: row.get(k) for k in ['gross_return', 'fees', 'spread_slippage_cost', 'spread_cost_attributed',
                                                'slippage_cost_attributed', 'funding_scenario',
                                                'net_scenario_return', 'turnover_notional', 'max_drawdown_scenario',
                                                'entry_signals', 'closed_segments', 'mean_gross_fraction']}})
    with (OUT/'matrix-37.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(flat[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(flat)
    counts = Counter(r['long_status'] for r in matrix)
    write_json(OUT/'conclusions.json', {'counts37': dict(counts),
               'structural_equivalence_groups': len({r['structural_equivalence_group'] for r in matrix}),
               'positive_after_base_costs': sum(r['full_base'] is not None and r['full_base']['net_scenario_return'] is not None
                                                and r['full_base']['net_scenario_return'] > 0 for r in matrix),
               'positive_gross_erased_by_base_costs': sum(r['full_base'] is not None and r['full_base']['gross_return'] is not None
                    and r['full_base']['gross_return'] > 0 and r['full_base']['net_scenario_return'] is not None
                    and r['full_base']['net_scenario_return'] <= 0 for r in matrix),
               'negative_gross': sum(r['full_base'] is not None and r['full_base']['gross_return'] is not None
                                      and r['full_base']['gross_return'] < 0 for r in matrix),
               'followup_candidates': [r['asset']+'|'+r['rule'] for r in matrix if r['future_followup_gate_pass']],
               'not_confirmatory': True})
    print(dict(counts), flush=True)


if __name__ == '__main__':
    analyze()
