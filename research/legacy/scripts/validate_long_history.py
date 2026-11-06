"""Exact cohort identity, independent ledger cash checks and original-study consistency."""
import csv
import gzip
import hashlib
import json
from decimal import Decimal

from long_history_protocol import OUT, RESEARCH, ROOT
from run_candidate_validation import windows
from run_long_history import load_sources

from perpetual_alpha.offline_quant.data import source_hash
from perpetual_alpha.offline_quant.replay import Costs, replay
from perpetual_alpha.offline_quant.run import orders_for, source_digest


def validate():
    manifest = json.loads((OUT/'manifest.json').read_text(encoding='utf-8'))
    for name, expected in json.loads((OUT/'hashes.json').read_text(encoding='utf-8')).items():
        assert source_hash(OUT/name) == expected, name
    for path, expected in manifest['source_hashes_normalized_LF'].items():
        assert '\\' not in path and not path.startswith('/') and '..' not in path
        assert hashlib.sha256((ROOT/path).read_text(encoding='utf-8').encode()).hexdigest() == expected, path
    for path, expected in manifest['input_hashes'].items():
        assert source_hash(RESEARCH/path) == expected
    assert source_digest() == manifest['protocol']['frozen_engine_sha256']
    with (RESEARCH/'asset-coverage/comparison.csv').open(encoding='utf-8-sig') as stream:
        original = [r for r in csv.DictReader(stream) if r['venue'] == 'par' and r['split'] == 'full_seen'
                    and r['scenario'] == 'base' and r['strategy_id'] != 'NO-TRADE']
    selected = [r for r in original if r['net_scenario_return'] and Decimal(r['net_scenario_return']) > 0]
    for name, expected in [('original-170.csv', original), ('selected-37.csv', selected)]:
        with (OUT/name).open(encoding='utf-8-sig') as stream:
            assert list(csv.DictReader(stream)) == expected, name
    assert len(original) == 170 and len(selected) == 37
    directions = json.loads((OUT/'original-direction-and-sizing.json').read_text(encoding='utf-8'))
    assert len(directions) == 170 and sum(r['selected_original'] for r in directions) == 37
    assert {(r['asset'], r['rule']) for r in directions} == {(r['asset'], r['strategy_id']) for r in original}
    for row in directions:
        assert source_hash(RESEARCH/'asset-coverage'/row['old_source_file']) == row['old_source_sha256']
        assert all(set(s['targets']) == {'par:'+row['asset']} for s in row['signal_target_and_feature_history'])
    rows = json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
    keys = {(r['asset'], r['rule'], r['split'], r['scenario']): r for r in rows}
    assert len(keys) == len(rows) == manifest['experiment_count']
    amendment = json.loads((OUT/'nonoverlap-amendment.json').read_text(encoding='utf-8'))
    nonoverlap = [r for r in rows if r['split'] == 'pre_PAR_selection_retrospective']
    assert len(nonoverlap) == 141
    assert all(r['end_ms'] == amendment['cutoffs_ms'][r['asset']] for r in nonoverlap)
    assert sum(r['rule'] in {s['strategy_id'] for s in selected if s['asset'] == r['asset']} for r in nonoverlap) == 37
    seen = set()
    for path in sorted(OUT.glob('*-ledgers.jsonl.gz')):
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for line in stream:
                detail = json.loads(line)
                key = tuple(detail['identity'][k] for k in ['asset', 'rule', 'split', 'scenario'])
                assert key not in seen
                seen.add(key)
                row, summary = keys[key], detail['summary']
                assert all(row[k] == value for k, value in summary.items())
                assert row['net_return'] is None and row['executable_net_return'] is None
                def dec(v):
                    return Decimal(str(v))
                fees = sum((abs(dec(f['delta'])*dec(f['scenario_price'])) for f in detail['fills']), Decimal(0))
                fees *= dec(summary['costs']['fee_bps'])/10000
                funding = sum((-dec(f['quantity'])*dec(f['price_proxy'])*dec(f['rate']) for f in detail['funding']), Decimal(0))
                assert abs(fees-dec(row['fees'])) < Decimal('1e-7')
                assert abs(funding-dec(row['funding_scenario'])) < Decimal('1e-7')
                reference_notional = sum((abs(dec(f['delta'])*dec(f['reference'])) for f in detail['fills']), Decimal(0))
                spread = reference_notional*dec(row['costs']['spread_bps'])/20000
                slippage = reference_notional*dec(row['costs']['slippage_bps'])/10000
                assert abs(spread-dec(row['spread_cost_attributed'])) < Decimal('1e-6')
                assert abs(slippage-dec(row['slippage_cost_attributed'])) < Decimal('1e-6')
                assert all(f['symbol'] == 'par:'+row['asset'] for f in detail['fills'])
                assert all(f['price_proxy_time_ms'] <= f['time_ms'] for f in detail['funding'])
                if row['scenario_complete']:
                    pnl = dec(row['gross_price_pnl'])-fees-dec(row['spread_slippage_cost'])+funding
                    assert abs(float(pnl/10000)-row['net_scenario_return']) < 1e-9
    assert seen == set(keys)
    matrix = json.loads((OUT/'matrix-37.json').read_text(encoding='utf-8'))
    assert {(r['asset'], r['rule']) for r in matrix} == {(r['asset'], r['strategy_id']) for r in selected}
    assert len(matrix) == 37
    assert len(json.loads((OUT/'long-universe-170.json').read_text(encoding='utf-8'))) == 170
    # Reused BTC/ETH prices must retain original gross/fees/friction despite newly known own funding.
    old = json.loads((RESEARCH/'asset-coverage/summary.json').read_text(encoding='utf-8'))
    for r in old:
        if r['venue'] != 'external' or r['split'] != 'full_seen' or r['scenario'] != 'base':
            continue
        key = r['asset'], r['strategy_id'], 'full_retrospective', 'base'
        if key not in keys:
            continue
        for field in ['gross_return', 'fees', 'spread_slippage_cost']:
            assert keys[key][field] == r[field], (key, field)
    # Prior EWY/HYPE results must match: same archived source, parameters and cost ledger.
    old_candidates = json.loads((RESEARCH/'candidate-validation/summary.json').read_text(encoding='utf-8'))
    translation = {'atr': 'ATR', 'donchian': 'DONCHIAN', 'funding-mean24': 'FUNDING-MEAN24'}
    for r in old_candidates:
        if r['venue'] != 'mainnet' or r['rule'] not in translation:
            continue
        asset = 'EWY' if r['symbol'] == 'xyz:EWY' else 'HYPE'
        key = asset, translation[r['rule']], r['split'], r['scenario']
        if key in keys:
            for field in ['gross_return', 'fees', 'spread_slippage_cost', 'funding_scenario', 'net_scenario_return']:
                assert keys[key][field] == r[field], (key, field)
    # Verify true structural equivalence, rather than inflating independent evidence.
    for asset in ['BRENT', 'ETH', 'GOLD']:
        a = keys[asset, 'ROLLING_Z', 'full_retrospective', 'base']
        b = keys[asset, 'BOLLINGER', 'full_retrospective', 'base']
        for field in ['gross_return', 'net_scenario_return', 'fees', 'funding_scenario']:
            assert a[field] == b[field]
    bars, rates, specs, _, _ = load_sources()
    registry = json.loads((OUT/'registry-170.json').read_text(encoding='utf-8'))
    for asset, rule in [('GOLD', 'ROLLING_Z'), ('EWJ', 'ATR')]:
        row = keys[asset, rule, 'calendar-2026-10', 'base']
        start, end = windows(bars[asset])['calendar-2026-10']
        definition = next(r['definition'] for r in registry if r['asset'] == asset and r['rule'] == rule)
        symbol = 'par:'+asset
        own_rates = {symbol: rates[asset]}
        signals = orders_for(definition, {symbol: bars[asset]}, own_rates, {}, start, end)
        result = replay({symbol: bars[asset]}, own_rates, {symbol: specs[asset]}, signals, start, end, Costs(**row['costs']))
        assert all(row[k] == value for k, value in result['summary'].items())
    print(f'{len(rows)} long-history ledgers reconciled; exact37/170 cohorts, prior-study identity and two actual replays verified')


if __name__ == '__main__':
    validate()
