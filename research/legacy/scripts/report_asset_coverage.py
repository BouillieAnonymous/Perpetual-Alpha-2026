"""Derive comparison tables from ALL frozen coverage runs, without choosing parameters."""
import json
from collections import Counter

from run_asset_coverage import RESEARCH, pct

from perpetual_alpha.offline_quant.data import source_hash
from perpetual_alpha.offline_quant.run import write_json


def report():
    output = RESEARCH/'asset-coverage'
    rows = json.loads((output/'summary.json').read_text(encoding='utf-8'))
    keys = {(r['venue'], r['asset'], r['strategy_id'], r['split'], r['scenario']): r for r in rows}
    par = [r for r in rows if r['venue'] == 'par' and r['split'] == 'full_seen'
           and r['scenario'] == 'base' and r['strategy_id'] != 'NO-TRADE']
    counts = Counter(r['status'] for r in par)
    out = ['# 十资产固定规则横向筛查与外部六个月回测', '',
           '## 口径与可复现性', '',
           '这是补齐覆盖，不是新策略开发或调参。全部沿用冻结的 offline_quant 信号与账本；'
           '11种价格规则、4种Regime、2种单资产Funding及不交易，共18规则×10资产×4时间区间×3成本=2160组。'
           '外部BTC/ETH不构造缺失的Funding信号，16规则×2资产×4区间×3成本=384组。合计2544组。', '',
           '主实验保持1h、既有12h持仓/再平衡、24h回看、168h压缩窗口；均值回归保留原提前退出规则。'
           '没有新增24h持仓搜索。各次实验资本10000、单资产目标不超过初始资本20%，不是十份资本合成组合。', '',
           'PAR统一使用原共同409h窗口与原60/20/20分区、12h隔离。已有最长438根与最短410根K线均保留；'
           '压缩等长回看策略在历史开头缺少warm-up，不能把无信号当失效。外部4392根/资产独立六个月窗口。'
           '所有分区都已见，仅是时间顺序描述，末段不得称为新的样本外验证。', '',
           'PAR base每次成交9bps费用、完整价差6bps、每边滑点2bps、延迟1h；fee_only保留9bps并将摩擦置零；'
           'stress费用18bps、完整价差18bps、每边滑点4bps、延迟2h。实际费按每笔成交名义金额收取，不能直接从资本收益扣固定28bps。', '',
           '外部原生主网BTC/ETH base使用公开基础Taker费4.5bps，fee_only无摩擦，stress9bps；'
           '其余摩擦/延迟与PAR同名情景一致。费率不是历史账户等级证据，盘口深度与真实滑点也未知。'
           '[官方费用来源](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)。', '',
           'PAR资金现金流依旧用已归档小时费率及先前收盘价代理结算估值，缺失时不补零。'
           '外部没有归档历史资金费，因此仅能给出价格损益减交易成本，完整净收益unknown。'
           '外部缺资金费时回撤是已知价格/成本现金流的回撤。', '',
           '非BTC/ETH必须额外核实单位：USDJPY为JPY/USD价格；其余具体单位、数量步长、历史覆盖见manifest.par_coverage。'
           '全部十资产均未确认真实USDPR估值转换与历史可执行深度，假设线性数量×价格的结果只能作为情景。'
           '原始价格序列不能自行假定等于1美元面值的合约；真实net_return和executable_net_return全部null。', '',
           '## 十资产×规则完整比较矩阵', '',
           '下表为full_seen/base的含已知资金费净情景收益%，正数也只是INCONCLUSIVE。'
           '每个格子的交易数、毛收益、费用/摩擦/资金分解、回撤和失败原因见各资产报告及comparison.csv。', '',
           '|规则|'+'|'.join(sorted({r['asset'] for r in par}))+'|',
           '|---|'+'---:|'*10]
    assets = sorted({r['asset'] for r in par})
    for kind in dict.fromkeys(r['strategy_id'] for r in par):
        out.append('|'+kind+'|'+'|'.join(pct(keys['par', a, kind, 'full_seen', 'base']['net_scenario_return'])
                                       for a in assets)+'|')
    out += ['', '不交易基线十个资产均为0；它包含在完整CSV/JSON内。', '',
            '## 按资产及失败来源汇总', '',
            f'170个非空仓对照组合中状态分布：{dict(counts)}。这些是相关的多重比较，不是170次独立检验。', '',
            '|资产|负净情景数|正净情景数|无完整结果数|毛收益为负数|毛正但净非正数|',
            '|---|---:|---:|---:|---:|---:|']
    for asset in assets:
        subset = [r for r in par if r['asset'] == asset]
        neg = sum(r['net_scenario_return'] is not None and r['net_scenario_return'] < 0 for r in subset)
        pos = sum(r['net_scenario_return'] is not None and r['net_scenario_return'] > 0 for r in subset)
        missing = sum(r['net_scenario_return'] is None or not r['fill_count'] for r in subset)
        grossneg = sum(r['gross_return'] is not None and r['gross_return'] < 0 for r in subset)
        erased = sum(r['gross_return'] is not None and r['gross_return'] > 0
                     and r['net_scenario_return'] is not None and r['net_scenario_return'] <= 0 for r in subset)
        out.append(f'|{asset}|{neg}|{pos}|{missing}|{grossneg}|{erased}|')
    out += ['', '正收益格子全部保留如下，不能事后挑资产作为已验证投资组合；Bollinger与Rolling Z等价，不能重复计证据。', '',
            '|资产/规则|全段base%|stress%|开发%|验证%|末段%|入场信号/成交段|',
            '|---|---:|---:|---:|---:|---:|---:|']
    for row in par:
        if row['net_scenario_return'] is None or row['net_scenario_return'] <= 0:
            continue
        venue, asset, kind = 'par', row['asset'], row['strategy_id']
        values = [keys[venue, asset, kind, s, c]['net_scenario_return'] for s, c in
                  [('full_seen', 'base'), ('full_seen', 'stress'), ('development_seen', 'base'),
                   ('validation_seen', 'base'), ('holdout_seen', 'base')]]
        out.append(f"|{asset}/{kind}|"+'|'.join(pct(v) for v in values)+
                   f"|{row['entry_signals']}/{row['closed_segments']}|")
    out += ['', '## 外部成熟市场：完整仓位与成本账本，缺失Funding', '',
            '这里不同于此前external-long-price.json的四小时方向诊断：重放持仓、反向/退出、仓位预算、数量取整、'
            '费用、摩擦、延迟与终止平仓，逐笔与净值均已归档。数量步长来自外部主网metadata，未套用PAR步长。'
            '但Funding缺失，故完整策略收益状态仍为INSUFFICIENT_DATA。', '',
            '|外部资产/规则|毛收益%|fee_only后且不含Funding%|base后且不含Funding%|stress后且不含Funding%|入场信号/成交段|',
            '|---|---:|---:|---:|---:|---:|']
    for row in rows:
        if row['venue'] != 'external' or row['split'] != 'full_seen' or row['scenario'] != 'base':
            continue
        asset, kind = row['asset'], row['strategy_id']
        values = [keys['external', asset, kind, 'full_seen', c]['price_after_cost_excluding_funding_return']
                  for c in ['fee_only', 'base', 'stress']]
        out.append(f"|{asset}/{kind}|{pct(row['gross_return'])}|"+'|'.join(pct(v) for v in values)+
                   f"|{row['entry_signals']}/{row['closed_segments']}|")
    out += ['', '## 结论边界与下一步', '',
            '最显著的资产差异出现在EWY：17个规则中13个base为正，例如ATR +2.856%、stress +1.345%，'
            '但ATR末段为−0.404%，Donchian末段−1.410%；不能据全段正收益批准交易。'
            'SP500、USDEUR各17个规则全部为负，说明部分失败并不局限BTC/ETH。', '',
            'HYPE FUNDING-MEAN24全段+1.182%、stress+0.845%，三个分段均正，是值得在新数据复核的探索线索；'
            '但只有15个成交段，毛价格收益约143.88、资金现金流仅+9.34（资本10000情景单位），'
            '不能把主要价格收益归因于稳定Carry。EWY FUNDING-LAST的价格收益约390.30、资金现金流仅+7.35，解释同样成立。', '',
            '外部30个非空仓规则中，base扣交易成本但未含Funding只有ETH REGIME-TREND为正（+1.092%），'
            '其stress为−2.046%。其余29项base为负；部分在fee_only下正收益，说明不能笼统断言所有毛空间都被手续费单独覆盖，'
            '摩擦假设和Funding仍需证据。上述数据并未证明任何外部规则的可执行净优势。', '',
            '不能再说其余八资产没有逐策略测试；也不能由XSM组合亏损推出每个单资产亏损。'
            '本矩阵直接回答这段历史上各固定规则的异同。毛收益已为负的组合不能只归因于成本；'
            '毛正转净负的组合则显示交易摩擦是实际障碍。正情景不能说明可交易盈利空间已获证明。', '',
            '本阶段没有PROMISING：17天、相关交易、未知估值/深度以及已见数据不足以支持确认性结论。'
            'REJECTED只是否定指定规则在指定窗口及费用假设下的表现，不是否定传统量化普遍有效性。'
            'INCONCLUSIVE不批准交易；INSUFFICIENT_DATA表示信号、资金或执行证据缺失。', '',
            '外部自身预测/持仓结果是本目录external-*；外部自身完整净收益因Funding与执行证据缺失未知；'
            '外部信号迁移PAR结果仍在../external-transfer/results，原六组全段外部迁移策略为负。'
            '不能混淆六个月主网成本情景与17天PAR迁移，也不能直接比较不同窗口来认定场所因果。', '',
            '接下来保留参数与全部资产名单，补采真正未见的比赛历史、可用时间/盘口和合约估值证据；'
            '外部补齐同期Funding及历史费用等级后再检验净表现。若研究资产选择，应重新预注册选择规则并在新数据验证，'
            '不得把本表正格子重新包装成样本外策略。本轮未引入Agent、真实下单或修改部署/风险配置。', '',
            '## 文件与复现', '',
            '`manifest.json`绑定协议、输入摘要、参数、成本、覆盖及主网单位；`comparison.csv`/`summary.json`包含全部2544组；'
            '`par-*.json`及`external-*.json`按资产分组；同名中文报告与details.jsonl.gz分别提供读表和完整信号/成交/资金/净值。'
            '`portfolio-existing.json`和`portfolio.zh-CN.md`独立保留原组合与配对结果，不覆盖PR #17。', '',
            '```powershell', '$env:PYTHONPATH = "$PWD/src"',
            '.venv/Scripts/python.exe scripts/run_asset_coverage.py --output .cache/asset-coverage-reproduction',
            '.venv/Scripts/python.exe scripts/report_asset_coverage.py',
            '.venv/Scripts/python.exe scripts/validate_asset_coverage.py', '```', '',
            'runner要求新输出目录，不覆盖既有数据；report/validator默认核对已归档结果。'
            '验证器检查2544账本、Decimal费用/资金恒等式、旧BTC/ETH单资产结果逐项一致，以及新增资产和外部长历史实际重放。', '']
    (output/'results.zh-CN.md').write_text('\n'.join(out), encoding='utf-8', newline='\n')
    portfolio = json.loads((output/'portfolio-existing.json').read_text(encoding='utf-8'))
    text = ['# 组合与配对独立报告：保留原实验', '',
            '来源 ../experiments/summary.json，portfolio-existing.json原样保留其全部分段和压力情景。'
            '没有重选配对、调参或把组合结果当单资产结果。', '',
            '|组合/配对|base净情景%|stress净情景%|入场信号/成交段|状态|',
            '|---|---:|---:|---:|---|']
    for row in portfolio:
        if row['split'] != 'full_seen' or row['scenario'] != 'base':
            continue
        stress = next(x for x in portfolio if x['strategy_id'] == row['strategy_id']
                      and x['split'] == 'full_seen' and x['scenario'] == 'stress')
        text.append(f"|{row['strategy_id']}|{pct(row['net_scenario_return'])}|{pct(stress['net_scenario_return'])}|"
                    f"{row['entry_signals']}/{row['closed_segments']}|{row['status']}|")
    text += ['', 'XSM为六种预设排序/仓位对照，分别在十资产和BTC/ETH子集运行；组合风险预算按一份资本计算。'
             '完整差异和估值限制见原manifest；不能将等权、反向或多头对照当作独立发现。', '',
             'BTC/ETH配对具有共同加密风险因子的经济动机，但相关性不代表价差平稳。原开发段EG p约0.446、'
             'KPSS p约0.01，协整门槛未通过；PAIR_COINT不交易，REJECTED是门槛拒绝，不是亏损交易。'
             '比例价差仅3次入场，全段微正而压力转负，不足证明套利。'
             '其他标的尚无确认的事先经济配对依据，本轮不机械扫描45对；Oracle-Basis仍保留PR #17独立研究。', '']
    (output/'portfolio.zh-CN.md').write_text('\n'.join(text), encoding='utf-8', newline='\n')
    write_json(output/'artifact-hashes.json', {p.name: source_hash(p) for p in sorted(output.iterdir())
                                               if p.is_file() and p.name != 'artifact-hashes.json'})


if __name__ == '__main__':
    report()
