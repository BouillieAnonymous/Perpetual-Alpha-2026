"""Freeze the exact 37/170 CSV selection before downloading new history."""
import csv
import gzip
import json
import time
from pathlib import Path

from run_asset_coverage import definitions

from perpetual_alpha.offline_quant.data import source_hash
from perpetual_alpha.offline_quant.run import source_digest, write_json

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT/'docs/research/new-alpha-discovery-2026-10-18'
OUT = RESEARCH/'long-history-37'
MAPPING = {'BTC': ('native', 'BTC'), 'ETH': ('native', 'ETH'), 'HYPE': ('native', 'HYPE'),
           'EWY': ('xyz', 'xyz:EWY'), 'EWJ': ('xyz', 'xyz:EWJ'), 'GOLD': ('xyz', 'xyz:GOLD'),
           'BRENT': ('xyz', 'xyz:BRENTOIL'), 'USDJPY': ('xyz', 'xyz:JPY'),
           'SP500': ('xyz', 'xyz:SP500'), 'USDEUR': ('xyz', 'xyz:EUR')}


def archive_original_directions():
    result = []
    registry = json.loads((OUT/'registry-170.json').read_text(encoding='utf-8'))
    identities = {(r['asset'], r['rule']): r for r in registry}
    for path in sorted((RESEARCH/'asset-coverage').glob('par-*-details.jsonl.gz')):
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for line in stream:
                detail = json.loads(line)
                identity = detail['identity']
                if identity['split'] != 'full_seen' or identity['scenario'] != 'base':
                    continue
                key = identity['asset'], identity['strategy_id']
                if key not in identities:
                    continue
                signals = detail['signals']
                result.append({'asset': key[0], 'rule': key[1], 'selected_original': identities[key]['selected'],
                               'old_source_file': path.name, 'old_source_sha256': source_hash(path),
                               'long_target_count': sum(any(v > 0 for v in r['targets'].values()) for r in signals),
                               'short_target_count': sum(any(v < 0 for v in r['targets'].values()) for r in signals),
                               'zero_target_count': sum(not any(r['targets'].values()) for r in signals),
                               'signal_target_and_feature_history': signals,
                               'original_closed_segments': detail['trades'],
                               'historical_fills_are': 'original frozen PAR OHLC cost scenario, not actual exchange fills'})
    assert len(result) == 170 and sum(r['selected_original'] for r in result) == 37
    write_json(OUT/'original-direction-and-sizing.json', result)


def freeze():
    if OUT.exists():
        raise FileExistsError('Preserve the frozen protocol')
    OUT.mkdir()
    path = RESEARCH/'asset-coverage/comparison.csv'
    with path.open(encoding='utf-8-sig') as stream:
        universe = [r for r in csv.DictReader(stream) if r['venue'] == 'par' and r['split'] == 'full_seen'
                    and r['scenario'] == 'base' and r['strategy_id'] != 'NO-TRADE']
    selected = [r for r in universe if r['net_scenario_return'] and float(r['net_scenario_return']) > 0]
    assert len(universe) == 170 and len(selected) == 37
    assert len({(r['asset'], r['strategy_id']) for r in universe}) == 170
    for name, rows in [('original-170.csv', universe), ('selected-37.csv', selected)]:
        with (OUT/name).open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
    registry = []
    for row in universe:
        definition = next(d for d in definitions('par:'+row['asset']) if d['id'] == row['strategy_id'])
        equivalence = row['asset']+':'+('ROLLING_Z=BOLLINGER' if row['strategy_id'] in {'ROLLING_Z', 'BOLLINGER'} else row['strategy_id'])
        registry.append({'asset': row['asset'], 'rule': row['strategy_id'], 'selected': row in selected,
                         'original': row, 'definition': definition, 'structural_equivalence_group': equivalence,
                         'overlap_group': row['asset']+':'+definition['family'],
                         'negative_control_rule': row['strategy_id'] == 'REGIME-RANDOM',
                         'directions': 'long/short from frozen signal, never inferred from ex-post profit',
                         'position': 'original volatility sizing capped .2 initial capital; funding fixed .2; initial capital10000',
                         'hold': 'original12h; Z/Bollinger early exit abs(z)<=.5; Funding rebalance12h; original Donchian reset',
                         'lookback': 24, 'compression': 168, 'random_seed': 20261018})
    write_json(OUT/'registry-170.json', registry)
    now = time.time_ns()//1000000
    write_json(OUT/'protocol.json', {'schema': 'long-history-37/1', 'frozen_ms': now,
        'parent_commit': '9137e6e8abc279b0e91e7bca39a46032024a7ff8', 'original_csv_sha256': source_hash(path),
        'frozen_engine_sha256': source_digest(), 'selected_count': 37, 'universe_count': 170,
        'mapping_candidates': MAPPING, 'history_end_ms': now//3600000*3600000,
        'signal_alias': 'PAR symbol retained ONLY inside offline engine to preserve random SHA seed; real venue/coin never relabelled in provenance',
        'primary_sources': 'BTC/ETH archived six months; HYPE/EWY archived4998h; other HIP3 public latest up to4998h; own-venue funding only',
        'all_past_is_retrospective': True, 'parameter_search': False,
        'windows': 'full +60/20/20 chronological12h gaps; each complete/partial calendar month>=24h',
        'costs': 'native base4.5bps/HIP3 base9bps; spread6bps+slip2bps each fill+delay1h; fee_only zero friction; stress double fee/spread18/slip4/delay2h',
        'run_plan': 'all170 full/base;37 selected full/splits all3 costs+calendar base; all10 assets long/short/flat same windows;37 matched-absolute-exposure direction controls full/splits base',
        'state': 'past-only efficiency25>0.35 trending else range, attribution only, no new gate',
        'statistics': 'daily fixed-capital PnL; moving5day block bootstrap2000 samples seed20261018; descriptive after selection, not confirmatory',
        'reject': 'complete own-venue cost scenario<=0 and >=90days =>LONG_HISTORY_REJECTED; positive =>INCONCLUSIVE; missing equivalence/history/funding or<90days =>INSUFFICIENT_DATA',
        'future_followup_gate': 'base/stress positive, validation/test positive,>=60%positive months,>=30entrysignals, daily excess95% lower bound>0 vs BOTH fixed directions; still exploratory',
        'no_transfer_claim': 'external own-market test only; PAR/PAS execution and prospective remain unvalidated'})
    print('Frozen exact CSV selection:37; full universe:170')
    archive_original_directions()


if __name__ == '__main__':
    freeze()
