"""Fixed retrospective single-asset screen; imports frozen strategies and ledger."""
import argparse
import csv
import gzip
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from perpetual_alpha.market_transfer import load_external
from perpetual_alpha.offline_quant.data import HOUR, coverage, read_archive, source_hash
from perpetual_alpha.offline_quant.replay import Costs, replay
from perpetual_alpha.offline_quant.run import orders_for, strategy_definitions, write_json

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "docs/research/new-alpha-discovery-2026-10-18"


def definitions(symbol):
    """No sensitivity search: original 11 rules, four regimes, two funding, control."""
    selected = strategy_definitions([symbol])[:16]
    selected += [d for d in strategy_definitions([symbol])
                 if d['id'] in {'FUNDING-LAST', 'FUNDING-MEAN24'}]
    return [{**d, 'symbols': [symbol]} for d in selected]


def status(summary, control=False):
    if not control and not summary['fill_count']:
        return 'INSUFFICIENT_DATA', 'no-filled-signal'
    if not summary['scenario_complete']:
        return 'INSUFFICIENT_DATA', 'funding-or-mark-or-liquidation-unknown'
    if summary['net_scenario_return'] < 0:
        return 'REJECTED', 'negative-under-this-cost-scenario'
    return 'INCONCLUSIVE', 'seen-short-history-and-multiple-comparisons'


def external_specs(path):
    """Quantity increments from archived MAINNET metadata, never PAR increments."""
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if row['request']['type'] == 'meta' and row.get('venue') == 'external':
                raw = row['payload']
                digest = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                if digest != row['payload_sha256']:
                    raise ValueError('Metadata response digest mismatch')
                return {'par:' + x['name']: {'quantity_step': 10 ** -x['szDecimals'],
                                            'valuation_supported': False}
                        for x in raw['universe'] if x['name'] in {'BTC', 'ETH'}}
    raise ValueError('No archived native mainnet quantity metadata')


def splits(bars):
    first = max(x[0].time for x in bars.values())
    end = min(x[-1].time for x in bars.values())
    hours = (end-first)//HOUR
    a, b = first + int(.6*hours)*HOUR, first + int(.8*hours)*HOUR
    return {'full_seen': [first, end], 'development_seen': [first, a],
            'validation_seen': [a+12*HOUR, b], 'holdout_seen': [b+12*HOUR, end]}


def pct(x):
    return 'unknown' if x is None else f'{100*x:.3f}'


def execute(output):
    if output.exists():
        raise FileExistsError('Preserve old experiments; choose a fresh output directory')
    archive = RESEARCH / 'official-hourly.jsonl.gz'
    extpath = RESEARCH / 'external-transfer/external-history.jsonl.gz'
    bars, funding, specs, errors = read_archive(archive)
    external, _ = load_external(extpath)
    ext_specs = external_specs(extpath)
    par_splits = json.loads((RESEARCH/'experiments/manifest.json').read_text(encoding='utf-8'))['splits_ms']
    costs = {'base': Costs(), 'fee_only': Costs(slippage_bps=0, spread_bps=0),
             'stress': Costs(fee_bps=18, slippage_bps=4, spread_bps=18, delay_hours=2)}
    extcosts = {'base': Costs(fee_bps=4.5),
                'fee_only': Costs(fee_bps=4.5, slippage_bps=0, spread_bps=0),
                'stress': Costs(fee_bps=9, slippage_bps=4, spread_bps=18, delay_hours=2)}
    output.mkdir(parents=True)
    manifest = {'schema': 'fixed-single-asset-coverage/1',
                'source_commit': '28d321f899b984f46945f2e9ee5f1d278c4c2019',
                'input_sha256': {p.relative_to(ROOT).as_posix(): source_hash(p) for p in [archive, extpath]},
                'runner_sha256': hashlib.sha256(Path(__file__).read_text(encoding='utf-8').encode()).hexdigest(),
                'definitions': definitions('par:BTC'), 'hold_hours': 12, 'lookback_hours': 24,
                'compression_hours': 168, 'split_gap_hours': 12, 'no_secondary_hold_search': True,
                'par_splits_ms': par_splits, 'external_splits_ms': splits(external),
                'par_costs': {k: asdict(v) for k, v in costs.items()},
                'external_costs': {k: asdict(v) for k, v in extcosts.items()},
                'par_coverage': coverage(bars, funding, specs), 'capture_errors': errors,
                'external_specs': ext_specs, 'external_funding': 'NOT_AVAILABLE; never imputed as observed zero',
                'external_fee_source': 'https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees',
                'vintage': 'ALL SEEN; chronological descriptive partitions, NOT new out-of-sample',
                'decision_availability': 'hypothetical completed bar; delayed next-hour open; NOT historical receive replay',
                'capital': '10000 per independent experiment; at most 20% initial capital per asset',
                'execution_status': 'INSUFFICIENT_DATA: unknown historical depth and verified USDPR valuation',
                'classification': 'negative complete PAR scenario REJECTED; positive INCONCLUSIVE; missing/no fills INSUFFICIENT_DATA; no confirmatory PROMISING',
                'external_classification': 'total-return status INSUFFICIENT_DATA without funding; price-only sign separately reported',
                'external_mapping': 'native mainnet BTC/ETH; par: prefix in loader is a computational alias only'}
    # Protocol is written before ANY new replay outcomes are calculated.
    write_json(output/'manifest.json', manifest)
    allrows = []
    for venue, data, rates, units, windows, scenarios in [
        ('par', bars, funding, specs, par_splits, costs),
        ('external', external, {}, ext_specs, splits(external), extcosts),
    ]:
        for symbol in sorted(data):
            rows = []
            label = symbol.split(':')[-1]
            with gzip.open(output/f'{venue}-{label}-details.jsonl.gz', 'wt', encoding='utf-8', newline='\n') as stream:
                for definition in definitions(symbol):
                    if venue == 'external' and definition['kind'] == 'funding':
                        continue  # Missing external funding is not a funding signal.
                    for split, (start, end) in windows.items():
                        orders = orders_for(definition, {symbol: data[symbol]}, rates, {}, start, end)
                        for scenario, cost in scenarios.items():
                            result = replay({symbol: data[symbol]}, rates, units, orders, start, end, cost)
                            summary = result['summary']
                            state, reason = status(summary, definition['kind'] == 'none')
                            price_only = ((summary['gross_price_pnl']-summary['fees']-summary['spread_slippage_cost'])
                                          / cost.capital if not summary['unclosed_positions'] else None)
                            row = {'venue': venue, 'asset': label, 'strategy_id': definition['id'],
                                   'split': split, 'scenario': scenario, 'start_ms': start, 'end_ms': end,
                                   'hours': (end-start)//HOUR, 'status': state, 'failure_reason': reason,
                                   'price_after_cost_excluding_funding_return': price_only,
                                   'price_only_sign': 'negative' if price_only is not None and price_only < 0 else 'nonnegative',
                                   'executable_status': 'INSUFFICIENT_DATA', **summary}
                            rows.append(row)
                            stream.write(json.dumps({'identity': {k: row[k] for k in
                                         ['venue', 'asset', 'strategy_id', 'split', 'scenario']}, **result}, allow_nan=False)+'\n')
            allrows.extend(rows)
            write_json(output/f'{venue}-{label}.json', rows)
            report = [f'# {venue} {label}：固定规则单资产实验', '',
                      '全部历史已见；初始资本10000，单资产目标上限20%，不同资产实验的收益不能相加为组合收益。',
                      '真实可执行净收益全部 unknown。费用/摩擦/资金现金流为线性估值情景；外部缺失 funding。', '',
                      '|规则|入场信号/成交段|毛收益%|扣成本且不含资金费%|含已知资金净情景%|费用/摩擦/资金现金流|回撤%|状态|',
                      '|---|---:|---:|---:|---:|---:|---:|---|']
            for row in rows:
                if row['split'] == 'full_seen' and row['scenario'] == 'base':
                    report.append(f"|{row['strategy_id']}|{row['entry_signals']}/{row['closed_segments']}|"
                                  f"{pct(row['gross_return'])}|{pct(row['price_after_cost_excluding_funding_return'])}|"
                                  f"{pct(row['net_scenario_return'])}|{row['fees']:.2f}/{row['spread_slippage_cost']:.2f}/{row['funding_scenario']:.2f}|"
                                  f"{pct(row['max_drawdown_scenario'])}|{row['status']}|")
            report += ['', '回撤是已知现金流下的情景回撤；缺失 funding 时不是完整净回撤。',
                       '同名 JSON 含全部时间分区/压力情景、失败原因、换手、资本占用、缺失 funding 小时；details 为完整账本。',
                       '成交段包括部分减仓，不能直接当成独立交易样本；Bollinger 与 Rolling Z 是既有等价实现，不能视为两份独立证据。', '']
            (output/f'{venue}-{label}.zh-CN.md').write_text('\n'.join(report), encoding='utf-8', newline='\n')
            print(f'{venue} {label}: {len(rows)} runs', flush=True)
    write_json(output/'summary.json', allrows)
    columns = [k for k in allrows[0] if k != 'costs']
    with (output/'comparison.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(allrows)
    original = json.loads((RESEARCH/'experiments/summary.json').read_text(encoding='utf-8'))
    portfolio = [r for r in original if r['family'] in {'PAIR', 'XSM'}]
    write_json(output/'portfolio-existing.json', portfolio)
    (output/'portfolio.zh-CN.md').write_text(
        '# 组合与配对：独立保留原实验\n\n'
        'portfolio-existing.json 原样提取既有 XSM/PAIR 结果，来源 ../experiments/summary.json；没有重选配对或调参。\n\n'
        'XSM 十资产组合与 BTC/ETH 子集各六种固定对照，按组合资本而非十个单资产表现解读。'
        'BTC/ETH 有共同加密风险因子但不保证协整；原协整门槛未通过，比例回归少量交易不能证明收益。'
        '其余资产无事先确认的经济配对依据，本次不扫描45对。Oracle-Basis 保留 PR #17 独立成果。\n',
        encoding='utf-8', newline='\n')
    write_json(output/'artifact-hashes.json', {p.name: source_hash(p) for p in sorted(output.iterdir()) if p.is_file()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    execute(parser.parse_args().output)
