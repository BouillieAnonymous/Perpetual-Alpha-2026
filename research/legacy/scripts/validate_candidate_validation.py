"""Portable offline evidence, Decimal ledger and actual fixed-rule replay checks."""
import gzip
import hashlib
import json
from decimal import Decimal

from run_candidate_validation import OUT, ROOT, load, orders

from perpetual_alpha.offline_quant.data import source_hash
from perpetual_alpha.offline_quant.replay import Costs, replay


def validate():
    manifest = json.loads((OUT/'manifest.json').read_text(encoding='utf-8'))
    for path, expected in manifest['runner_sources_normalized_sha256'].items():
        assert '\\' not in path and not path.startswith('/') and '..' not in path
        assert hashlib.sha256((ROOT/path).read_text(encoding='utf-8').encode()).hexdigest() == expected, path
    for name, expected in json.loads((OUT/'hashes.json').read_text(encoding='utf-8')).items():
        assert source_hash(OUT/name) == expected, name
    rows = json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
    keys = {(r['venue'], r['symbol'], r['rule'], r['split'], r['scenario']): r for r in rows}
    assert len(keys) == len(rows) == manifest['experiment_count'] == 459
    observed = set()
    with gzip.open(OUT/'ledgers.jsonl.gz', 'rt', encoding='utf-8') as stream:
        for line in stream:
            detail = json.loads(line)
            key = tuple(detail['identity'][k] for k in ['venue', 'symbol', 'rule', 'split', 'scenario'])
            assert key not in observed
            observed.add(key)
            row, summary = keys[key], detail['summary']
            assert all(row[k] == v for k, v in summary.items())
            assert summary['executable_net_return'] is None and summary['net_return'] is None
            def dec(v):
                return Decimal(str(v))
            fee = sum((abs(dec(f['delta'])*dec(f['scenario_price'])) for f in detail['fills']), Decimal(0))
            fee *= dec(summary['costs']['fee_bps'])/10000
            funding = sum((-dec(f['quantity'])*dec(f['price_proxy'])*dec(f['rate']) for f in detail['funding']), Decimal(0))
            assert abs(fee-dec(summary['fees'])) < Decimal('1e-7')
            assert abs(funding-dec(summary['funding_scenario'])) < Decimal('1e-7')
            assert all(f['price_proxy_time_ms'] <= f['time_ms'] for f in detail['funding'])
            assert all(f['symbol'] == row['symbol'] for f in detail['fills'])
            if summary['scenario_complete']:
                cash = (dec(summary['gross_price_pnl'])-fee-dec(summary['spread_slippage_cost'])+funding)/10000
                assert abs(float(cash)-summary['net_scenario_return']) < 1e-9
    assert observed == set(keys)
    # Reproduce an actual recent month on each venue, including funding and nonzero fees.
    bars, rates, specs, errors = load()
    assert not errors
    for venue, symbol, kind in [('mainnet', 'HYPE', 'funding-mean24'), ('mainnet', 'xyz:EWY', 'atr'),
                                ('par', 'par:HYPE', 'funding-mean24'), ('par', 'par:EWY', 'donchian')]:
        row = keys[venue, symbol, kind, 'calendar-2026-10', 'base']
        # Start/end are recoverable from the original signal/equity ledger window function.
        from run_candidate_validation import windows
        start, end = windows(bars[venue, symbol, '1h'])['calendar-2026-10']
        series = bars[venue, symbol, '1h']
        signals = orders([r for r in series if r.time < end], rates[venue, symbol], kind, start)
        result = replay({symbol: series}, {symbol: rates[venue, symbol]}, {symbol: specs[venue, symbol]},
                        signals, start, end, Costs(**row['costs']))
        assert all(row[k] == v for k, v in result['summary'].items())
    # Source identity prevents native/PAR/PAS funding substitution.
    assert {k[0] for k in bars} == {'mainnet', 'par'}
    for row in json.loads((OUT/'prospective.json').read_text(encoding='utf-8')):
        assert row['complete_entry_exit_pairs'] == 0 and row['status'] == 'INSUFFICIENT_DATA'
    for venue in ['mainnet', 'par']:
        labels = json.loads((OUT/(venue+'-conditional-labels.json')).read_text(encoding='utf-8'))
        assert all(r['label_end_ms'] == r['time_ms']+13*3600000 for r in labels)
    print('459 ledgers and Decimal fees/funding checked; four actual venue-specific replays match; no prospective PnL invented')


if __name__ == '__main__':
    validate()
