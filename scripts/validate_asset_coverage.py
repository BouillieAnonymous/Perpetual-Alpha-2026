"""Verify coverage, independent cash arithmetic, original-rule identity and replays."""
import gzip
import hashlib
import json
from decimal import Decimal

from run_asset_coverage import RESEARCH, ROOT, definitions, external_specs

from perpetual_alpha.market_transfer import load_external
from perpetual_alpha.offline_quant.data import read_archive, source_hash
from perpetual_alpha.offline_quant.replay import Costs, replay
from perpetual_alpha.offline_quant.run import orders_for


def validate():
    output = RESEARCH/'asset-coverage'
    manifest = json.loads((output/'manifest.json').read_text(encoding='utf-8'))
    runner = ROOT/'scripts/run_asset_coverage.py'
    assert hashlib.sha256(runner.read_text(encoding='utf-8').encode()).hexdigest() == manifest['runner_sha256']
    for path, expected in manifest['input_sha256'].items():
        assert '\\' not in path and '..' not in path and not path.startswith('/')
        assert source_hash(ROOT/path) == expected
    for path, expected in json.loads((output/'artifact-hashes.json').read_text(encoding='utf-8')).items():
        assert source_hash(output/path) == expected, path
    rows = json.loads((output/'summary.json').read_text(encoding='utf-8'))
    keys = {(r['venue'], r['asset'], r['strategy_id'], r['split'], r['scenario']): r for r in rows}
    assert len(keys) == len(rows) == 2544
    assert sum(r['venue'] == 'par' for r in rows) == 2160
    for asset in {r['asset'] for r in rows if r['venue'] == 'par'}:
        assert len([r for r in rows if r['asset'] == asset and r['venue'] == 'par']) == 216
    count = 0
    for path in sorted(output.glob('*-details.jsonl.gz')):
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for line in stream:
                detail = json.loads(line)
                identity = detail['identity']
                key = tuple(identity[k] for k in ['venue', 'asset', 'strategy_id', 'split', 'scenario'])
                row, summary = keys[key], detail['summary']
                assert all(row[k] == v for k, v in summary.items())
                assert row['net_return'] is None and row['executable_net_return'] is None
                def dec(x):
                    return Decimal(str(x))
                fees = sum((abs(dec(f['delta'])*dec(f['scenario_price'])) for f in detail['fills']), Decimal(0))
                fees *= dec(summary['costs']['fee_bps'])/10000
                fund = sum((-dec(f['quantity'])*dec(f['price_proxy'])*dec(f['rate'])
                            for f in detail['funding']), Decimal(0))
                assert abs(fees-dec(summary['fees'])) < Decimal('1e-7')
                assert abs(fund-dec(summary['funding_scenario'])) < Decimal('1e-7')
                assert all(f['price_proxy_time_ms'] <= f['time_ms'] for f in detail['funding'])
                assert all(f['symbol'] == 'par:'+identity['asset'] for f in detail['fills'])
                if not summary['unclosed_positions']:
                    price = (dec(summary['gross_price_pnl'])-fees-dec(summary['spread_slippage_cost']))/10000
                    assert abs(float(price)-row['price_after_cost_excluding_funding_return']) < 1e-9
                    if summary['scenario_complete']:
                        assert abs(float(price+fund/10000)-summary['net_scenario_return']) < 1e-9
                if identity['venue'] == 'external' and summary['fill_count']:
                    assert row['net_scenario_return'] is None
                    assert row['status'] == 'INSUFFICIENT_DATA'
                count += 1
    assert count == len(rows)
    # Existing BTC/ETH individual experiments MUST reproduce without parameter changes.
    old = json.loads((RESEARCH/'experiments/summary.json').read_text(encoding='utf-8'))
    for row in old:
        if row['family'] != 'ASSET_DIAGNOSTIC':
            continue
        kind, asset = row['strategy_id'].rsplit('-', 1)
        reference = keys['par', asset, kind, row['split'], row['scenario']]
        for field in ['gross_return', 'net_scenario_return', 'fees', 'spread_slippage_cost', 'funding_scenario']:
            assert reference[field] == row[field], (asset, kind, field)
    # Real replay on an untested commodity and on six-month external data.
    par, funding, specs, _ = read_archive(RESEARCH/'official-hourly.jsonl.gz')
    path = RESEARCH/'external-transfer/external-history.jsonl.gz'
    ext, _ = load_external(path)
    for venue, data, rates, units, symbol, kind in [
        ('par', par, funding, specs, 'par:BRENT', 'ATR'),
        ('external', ext, {}, external_specs(path), 'par:ETH', 'TSM')
    ]:
        start, end = manifest[venue+'_splits_ms']['holdout_seen']
        definition = next(d for d in definitions(symbol) if d['id'] == kind)
        orders = orders_for(definition, {symbol: data[symbol]}, rates, {}, start, end)
        result = replay({symbol: data[symbol]}, rates, units, orders, start, end,
                        Costs(**manifest[venue+'_costs']['base']))
        row = keys[venue, symbol.split(':')[1], kind, 'holdout_seen', 'base']
        assert all(row[k] == v for k, v in result['summary'].items())
    print(f'{count} ledgers reconciled; original BTC/ETH identity and two actual replays match')


if __name__ == '__main__':
    validate()
