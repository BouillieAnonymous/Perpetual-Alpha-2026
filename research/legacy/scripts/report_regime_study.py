"""Evidence report and equal-sleeve portfolio, never a conditional-PnL leaderboard."""
import gzip
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone

from analyze_long_history import interval
from regime_protocol import DAY, FAMILIES, OUT, PRIOR, ROOT, STATES

from perpetual_alpha.offline_quant.data import source_hash
from perpetual_alpha.offline_quant.run import write_json


def pct(x):
    return 'unknown' if x is None else f'{100*x:.3f}'


def date(t):
    return datetime.fromtimestamp(t/1000, timezone.utc).strftime('%Y-%m-%d %H:%M')


def report():
    protocol = json.loads((OUT/'protocol.json').read_text())
    runs = json.loads((OUT/'runs.json').read_text())
    cells = json.loads((OUT/'conditional.json').read_text())
    audits = json.loads((OUT/'selection-audit.json').read_text())
    registry = json.loads((PRIOR/'registry-170.json').read_text())
    ids = {(r['asset'], r['rule']) for r in registry}
    equivalence = {(r['asset'], r['rule']): r['structural_equivalence_group'] for r in registry}
    full = [r for r in cells if r['split']=='full_retrospective' and r['state'] in STATES and (r['asset'],r['rule']) in ids]
    assert len(full)==680
    groups, hourly = defaultdict(list), defaultdict(lambda: defaultdict(float))
    with gzip.open(OUT/'forward-ledgers.jsonl.gz', 'rt', encoding='utf-8') as stream:
        for line in stream:
            d = json.loads(line)
            i = d['identity']
            horizon = 'locked28d' if i['fold']=='final_locked28d' else 'rolling70d'
            key = horizon, i['mode'], i['scenario']
            previous = 10000.
            for row in d['equity']:
                hourly[key][row['time_ms']] += (row['equity_scenario']-previous)/100000
                previous = row['equity_scenario']
    for row in runs:
        horizon = 'locked28d' if row['fold']=='final_locked28d' else 'rolling70d'
        groups[horizon, row['mode'], row['scenario']].append(row)
    portfolios = []
    for key, rows in groups.items():
        hours = hourly[key]
        daily = defaultdict(float)
        cumulative, peak, dd = 1., 1., 0.
        for t, value in sorted(hours.items()):
            daily[str((t-1)//DAY)] += value
            cumulative += value
            peak = max(peak, cumulative)
            dd = max(dd, (peak-cumulative)/peak)
        complete = all(r['scenario_complete'] for r in rows)
        net = sum(r['known_components_return'] for r in rows)/10
        assert abs(net-sum(hours.values()))<1e-10
        s = {'horizon': key[0], 'mode': key[1], 'scenario': key[2], 'initial_capital': 100000,
             'net_scenario_return': net if complete else None, 'net_return': None, 'executable_net_return': None,
             'max_drawdown_scenario': dd, 'daily_vol_on_initial': statistics.stdev(daily.values()) if len(daily)>1 else None,
             'days': len(daily), 'hours': len(hours), 'daily': dict(daily),
             'scenario_complete': complete, 'family_switches_including_flat': sum(r['family_switches_including_flat'] for r in rows),
             'fills': sum(r['fill_count'] for r in rows),
             'mean_gross_fraction': sum(r['mean_gross_fraction']*r['return_days'] for r in rows)/sum(r['return_days'] for r in rows),
             **{k: sum(r[k] for r in rows) for k in ['gross_price_pnl','fees','spread_slippage_cost','funding_scenario','turnover_notional']}}
        s['spread_cost_attributed'] = s['spread_slippage_cost']*(.6 if key[2]=='base' else 9/13)
        s['slippage_cost_attributed'] = s['spread_slippage_cost']-s['spread_cost_attributed']
        s['months'] = dict(sum_months(daily))
        s['status'] = 'INSUFFICIENT_DATA' if not complete or not s['fills'] else 'REJECTED' if net<=0 else 'INCONCLUSIVE'
        portfolios.append(s)
    lookup = {(r['horizon'],r['mode'],r['scenario']):r for r in portfolios}
    for row in portfolios:
        comparisons = {}
        for mode in ['FLAT','FIXED-LONG','FIXED-SHORT']+['FIXED:'+r for r in FAMILIES]:
            other = lookup[row['horizon'],mode,row['scenario']]
            days = sorted(set(row['daily']) & set(other['daily']),key=int)
            comparisons[mode] = interval([row['daily'][t]-other['daily'][t] for t in days])
        row['paired_daily_excess'] = comparisons
    # Prospective claims remain disallowed even if an exploratory gate passed.
    snapshots = []
    for r in full:
        parts = [next(x for x in cells if x['asset']==r['asset'] and x['rule']==r['rule']
                      and x['state']==r['state'] and x['split']==split)
                 for split in ['development_retrospective','validation_retrospective','test_retrospective']]
        snapshots.append({'asset':r['asset'],'rule':r['rule'],'state':r['state'],
                          'equivalence_group':equivalence[r['asset'],r['rule']], 'full_status':r['status'],
                          'full_net':r['net'], 'adequate_full_sample':r['status']!='INSUFFICIENT_DATA',
                          'positive_all3_parts':all(x['net']>0 for x in parts),
                          'at_least5_episodes_each_part':all(x['entry_episodes']>=5 for x in parts),
                          'parts':[{'net':x['net'],'episodes':x['entry_episodes'],'status':x['status']} for x in parts]})
    selected_audit = [r for a in audits for r in a['audit'] if 'train_eligible' in r]
    maps = [v for a in audits for v in a['mapping'].values()]
    selector = [r for r in portfolios if r['mode']=='SELECTOR']
    summary = {'conditional_full_cells':680, 'positive_attribution_cells':sum(r['net']>0 for r in full),
               'conditional_status_counts':dict(Counter(r['status'] for r in full)),
               'adequate_positive_all3_with5episodes':sum(r['adequate_full_sample'] and r['positive_all3_parts']
                                                         and r['at_least5_episodes_each_part'] for r in snapshots),
               'train_family_state_attempts':len(selected_audit),
               'train_min_episode_and_days_pass':sum(r['train']['entry_episodes']>=30 and r['train']['entry_days']>=10 for r in selected_audit),
               'supported_train_positive_net':sum(r['train']['entry_episodes']>=30 and r['train']['entry_days']>=10 and r['train']['net']>0 for r in selected_audit),
               'train_eligible':sum(r['train_eligible'] for r in selected_audit),
               'mapped_active_state_cells':sum(v!='FLAT' for v in maps),'mapped_total_cells':len(maps),
               'selector_fills_base':sum(r['fills'] for r in selector if r['scenario']=='base'),
               'prospective_status':'INSUFFICIENT_DATA','all_outcomes_retrospective':True}
    write_json(OUT/'portfolio.json', portfolios)
    write_json(OUT/'conditional-stability.json', snapshots)
    write_json(OUT/'conclusions.json', summary)
    incremental = []
    for row in portfolios:
        refs = (['FIXED:'+row['mode'].split(':')[1]] if row['mode'].startswith('FILTER:') else
                ['FIXED:'+r for r in FAMILIES] if row['mode']=='SELECTOR' else [])
        for mode in refs:
            other = lookup[row['horizon'],mode,row['scenario']]
            incremental.append({'horizon':row['horizon'],'scenario':row['scenario'],'overlay':row['mode'],'reference':mode,
                                'net_difference':row['net_scenario_return']-other['net_scenario_return'],
                                **{k+'_difference':row[k]-other[k] for k in
                                   ['gross_price_pnl','fees','spread_cost_attributed','slippage_cost_attributed','funding_scenario','turnover_notional']}})
    write_json(OUT/'incremental-costs.json',incremental)
    fold_portfolios = []
    for f in protocol['folds']:
        horizon = 'locked28d' if f['name']=='final_locked28d' else 'rolling70d'
        for p in portfolios:
            if p['horizon']!=horizon:
                continue
            rows = [r for r in runs if r['fold']==f['name'] and r['mode']==p['mode'] and r['scenario']==p['scenario']]
            increments = {t:v for t,v in hourly[horizon,p['mode'],p['scenario']].items() if f['test'][0]<t<=f['test'][1]}
            value,peak,dd = 1.,1.,0.
            for t,v in sorted(increments.items()):
                value+=v
                peak=max(peak,value)
                dd=max(dd,(peak-value)/peak)
            fold_portfolios.append({'fold':f['name'],'mode':p['mode'],'scenario':p['scenario'],
                                    'net_scenario_return':sum(increments.values()),'max_drawdown_scenario':dd,
                                    'start_ms':f['test'][0],'end_ms':f['test'][1],
                                    **{k:sum(r[k] for r in rows) for k in ['fees','spread_slippage_cost','funding_scenario','turnover_notional']}})
    write_json(OUT/'fold-portfolios.json',fold_portfolios)
    active_fills = summary['selector_fills_base']
    verdict = ('选择器在预先规定的支持与验证门槛下全程空仓，未产生可检验的正交易收益；'
               '本轮Regime Switching Alpha可行性结论为INSUFFICIENT_DATA，而不是盈利策略。'
               if not active_fills else
               '选择器有历史情景成交；须结合下列滚动/锁定段与方向基准评估，正值也仅INCONCLUSIVE。')
    filters_negative = all(lookup[h,'FILTER:'+r,'base']['net_scenario_return']<0
                           for h in ['rolling70d','locked28d'] for r in FAMILIES)
    out = ['# Regime Switching / Conditional Alpha：独立可行性研究', '',
           '## 直接结论及证据边界', '',
           f"170个固定组合×4状态共680个全段归因单元，{summary['positive_attribution_cells']}个账面条件净值为正；"
           f"状态计数：{summary['conditional_status_counts']}。这不是对应数量的独立Alpha。"
           f"全段样本足够且三个顺序分段都正、每段至少5个完整持仓的单元为{summary['adequate_positive_all3_with5episodes']}个；"
           '这仍是多重筛选后的事后归因，不是显著发现。', '',
           f"冻结选择器共{len(audits)}个资产/时间块、{len(maps)}个状态映射；"
           f"{summary['train_family_state_attempts']}项训练尝试中，{summary['train_min_episode_and_days_pass']}项达到最低持仓/天数，"
           f"其中{summary['supported_train_positive_net']}项条件净正，但没有一项训练收益区间下界高于零；"
           f"{summary['train_eligible']}项通过训练正收益与区间门槛，验证后{summary['mapped_active_state_cells']}个映射非空仓。"
           f"选择器base成交{summary['selector_fills_base']}笔。空仓或缺少独立支持不能证明条件Alpha不存在。", '',
           verdict, '',
           ('三个预先定义过滤器在70日滚动和28日锁定两段均为净负；改善亏损幅度没有形成相对空仓的正收益。'
            if filters_negative else '过滤器不同时间段结果见下表，不用单段正值重新映射。'), '',
           '所有历史已由PR #15研究过。这里的“训练/验证/留出”是算法时间隔离，不是真正此前无人看过的数据。'
           '末28天在本次新归因收益产生前锁定，期间不再训练；仍必须标Retrospective。真正独立前瞻：INSUFFICIENT_DATA。', '',
           '## 一、数据、状态与冻结规则', '',
           '不下载新数据，复用PR #15提交6832145的native BTC/ETH/HYPE及xyz七资产自身小时价格和Funding。'
           'BTC/ETH4392小时、其余4998小时；对应真实标的/交易时段/股息/单位与零成交量问题继承原source-quality及原报告。'
           '外部场所自身的线性成本情景，不是PAR/PAS迁移或实际USDP/USDPR收益。没有历史价差/深度观测；摩擦始终是假设。', '',
           '四状态：25个连续收盘的价格效率>0.35为TREND，否则RANGE；24个对数收益的样本波动率高于严格之前168个RV的中位数为HIGH，否则LOW。'
           '当前闭合bar可用、波动参照排除当前RV；连续历史不足或参照为0则UNKNOWN并不交易。'
           '0.35沿用既有研究，168小时参照与中位数是一组事前固定经济定义，没有分位/阈值搜索。'
           '真实历史接收延迟未知，沿用闭合bar可知、下一小时开盘情景；不能解释为毫秒可交易信号。', '',
           '全部17条旧规则参数、12h及原提前退出/再平衡逻辑不变。全部170项都做归因，'
           'Z/Bollinger结构等价、Donchian与Regime变体高度相关、REGIME-RANDOM只是对照；不增加独立证据数。'
           '选择器只允许TSM（趋势）、ATR（突破）、ROLLING_Z（均值回归）与FLAT，选择代表不依赖这轮收益。'
           'Funding仅在原17规则归因及现金流中诊断，不进入新的状态特征或选择器。', '',
           '## 二、条件归因与可运行过滤的区别', '',
           'conditional.csv/JSON包括每资产/规则/状态的完整全段、60/20/20及自然月。'
           '每小时损益归给该持仓小时开始前一小时已可知的状态，手续费、资金、摩擦、名义换手及净值变化完整分配并对总账。'
           '独立持仓按同一开仓时间合并全部部分减仓段；另列原信号触发数、入场状态下完成的持仓数/天数、状态时长及实际占仓时长。'
           '占仓小时和资本占用沿用原账本小时末敞口标记，终端退出小时及盘中敞口不能完整恢复；不冒称逐笔实测持仓积分。'
           '这些计数不是同一统计单位：持仓内经历多个状态，小时条件损益不是入场状态的整笔交易收益。'
           '独立持仓指不重叠的持仓生命周期，不意味着统计上IID。', '',
           '归因DD与日波动是把状态外小时收益置零的标记损益路径；它保留在conditional中，仅作诊断，'
           '**不能称为可成交过滤器的回撤或收益**。每条归因同时提供固定多/空/空仓在相同状态小时的收益，仓位风险差异另见资本占用。'
           '全段至少30完整持仓、10入场日、至少3个月各5持仓才满足基本支持；不足统一INSUFFICIENT_DATA，'
           '充分且净非正REJECTED，正仍INCONCLUSIVE。没有PROMISING。', '',
           '真正可运行对照另行重放：TSM仅TREND_LOW，ATR仅TREND_HIGH，Z仅RANGE_LOW；'
           '状态不合时请求平仓，重新符合时可按原shadow目标入场。它可能进入原shadow持仓中途，只持有其剩余生命周期；'
           '这是明确的新过滤覆盖层，不冒称原完整持仓归因。所有平仓、再入和切换都重新付费。', '',
           '## 三、滚动训练、验证与确定性选择器', '',
           f"协议冻结UTC：{date(protocol['frozen_ms'])}。共同数据窗口：{date(protocol['common_start_ms'])}—{date(protocol['common_end_ms'])}。", '',
           '|时间块|训练60d UTC|验证14d UTC|评估UTC|', '|---|---|---|---|']
    for f in protocol['folds']:
        out.append(f"|{f['name']}|{date(f['train'][0])}—{date(f['train'][1])}|{date(f['validation'][0])}—{date(f['validation'][1])}|{date(f['test'][0])}—{date(f['test'][1])}|")
    out += ['', '每资产/状态在训练期比较三个代表。要求该状态入场的已完成独立持仓>=30且>=10入场日，'
            '状态小时净收益>0，60天日PnL循环移动5日块bootstrap2000次、种子20261018的95%下界>0。'
            '仅取训练得分最高项；在隔离12h后的14d验证，要求>=5完整持仓、>=3入场日且条件净正。失败就FLAT，不尝试次优。'
            '训练得分=固定资本日条件PnL的均值，等长区间排序等价于净额；并列按固定家族顺序。'
            '所有标签截至各区间末端已成熟，完整持仓计数排除跨界未平仓；验证标签也不能越过验证末端。', '',
            '训练/验证标签使用旧固定策略当时已成熟的状态小时标记收益，含可在线延续的既有仓位；'
            '不是假设整个策略在验证开始重新启动的执行回测。新评估段则所有方法冷启动，按原规则重新生成shadow并独立结算。'
            '每12h根据当前状态选择家族，持有家族身份至下次12h；中间遵守其shadow的退出/目标。'
            '映射在评估段开始前冻结；末28d全段不再训练。后续滚动折可以使用更早评估期已发生的历史，符合在线学习时间序。', '',
            '## 四、可执行情景对照与组合结果', '',
            '每资产每块10方法×base/stress，共1200账本。方法为FLAT、固定多/空、三个固定规则、三个简单过滤器及SELECTOR。'
            '每个袖套资本10000，总资本100000、十袖套等权，不按历史优化资产权重；原单资产仓位上限为袖套初始资本20%。'
            '组合按同步小时损益相加计算DD与日波动，含资产相关性；不把单资产DD平均。'
            '各块结尾强制退出并付成本，下块冷启动，块间利润不复投，所有方法一致。', '',
            'base：native单边4.5bps、HIP3单边9bps，完整价差6bps、每边滑点2bps、延迟1h；'
            'stress费率加倍、价差18bps、每边滑点4bps、延迟2h。选择映射始终按base历史确定，不以stress结果重选。'
            'Funding为自身真实费率×先前收盘Oracle代理；没有真实盘口/成交/结算Oracle，实际净收益保持null。', '',
            '|评估|方法|base净%|stress净%|base DD%|base日波动%|base换手/资本|家族切换含空仓|成交笔数|',
            '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in portfolios:
        if row['scenario']!='base':
            continue
        stress = lookup[row['horizon'],row['mode'],'stress']
        out.append(f"|{row['horizon']}|{row['mode']}|{pct(row['net_scenario_return'])}|{pct(stress['net_scenario_return'])}|"
                   f"{pct(row['max_drawdown_scenario'])}|{pct(row['daily_vol_on_initial'])}|{row['turnover_notional']/100000:.3f}|"
                   f"{row['family_switches_including_flat']}|{row['fills']}|")
    out += ['', 'portfolio.json另列每项毛收益、费用、价差、滑点、Funding、自然月和资本占用。'
            'fold-portfolios.json另列全部6个非重叠评估段×10方法×2成本的120个组合结果，含逐段DD与成本；'
            'switches是家族/空仓身份改变，不等于订单数；同向切换可能净额执行，实际成本以逐笔名义成交计。'
            '固定方向没有家族选择，因此身份切换记0；初次由FLAT选家族计一次，不能当实际换仓次数。', '',
            'incremental-costs.json逐项给出三个过滤器相对对应固定规则、选择器相对三个固定代表的价格、费用、'
            '价差、滑点、Funding、换手与净收益差额；差额包含交易频率及敞口变化，不能全部称为纯切换成本。'
            '过滤有时减少而非增加费用；避免亏损的成本节省也不等于相对空仓赚到正收益。', '',
            '### 选择器相对基准的统计不确定性', '',
            '|评估/base|基准|平均每日超额bps|95%下界|95%上界|天数|', '|---|---|---:|---:|---:|---:|']
    for row in selector:
        if row['scenario']!='base':
            continue
        for mode, ci in row['paired_daily_excess'].items():
            def fmt(x):
                return 'unknown' if x is None else f'{x*10000:.3f}'
            out.append(f"|{row['horizon']}|{mode}|{fmt(ci['mean'])}|{fmt(ci['lower95'])}|{fmt(ci['upper95'])}|{ci['days']}|")
    out += ['', '区间为配对日固定资本PnL的5日块bootstrap，至少60日才给区间；末28d明确不提供不可靠区间。'
            '70日滚动也只有五个14d块，跨资产/家族/状态相关，且全部历史已见；未做家族多重校正，不是确认性p值。', '',
            '## 五、可行性判断与停止条件', '',
            '1. 状态净收益为正的归因单元不能证明稳定条件优势；应同时查看足够独立持仓、顺序分段、月度以及固定方向对照。'
            'conditional-stability逐项保留全部680单元，不按收益排行选阈值。', '',
            '2. Classifier只用过去价格，因而时间上可识别；是否预测有利条件必须由真正覆盖层的留出表现回答，'
            '不能由全段状态归因回答。', '',
            '3. Selector若主要空仓，最多证明当前支持门槛无法选出有证据的规则；空仓避免固定策略亏损不是新增盈利Alpha。'
            '若有交易，仍须在滚动与锁定段都超越空仓和全部固定代表，且扣掉切换费用后保持优势，才能考虑新前瞻观察。'
            '本轮不得凭单个过滤器事后正值重新映射。', '',
            '4. 六七个月切成四状态后独立样本明显收缩，长期单向持仓不能按小时数当成成千独立交易。'
            '缺少多年跨状态循环、真实盘口与全新留出，结论仅限可行性筛查，PAS/PAR可执行收益均INSUFFICIENT_DATA。', '',
            '5. 现阶段不开发运行时Selector，也不引入ML/LLM：传统小模型尚需稳定的新数据证据，复杂模型会增加选择自由度。'
            '停止本数据上的阈值/映射扩搜；如以后研究，先冻结同一规则，积累未参与开发的新样本和真实可用时间/报价/深度，'
            '再按独立持仓与市场周期评估，不用更多指标补救。', '',
            '## 六、归档及复现', '',
            '原PR #15的研究文件与offline_quant源码保持字节不变。本PR只新增scripts/tests/独立研究目录和CI步骤；'
            '没有VPS、生产风控、执行、订单开关、新闻API或LLM调用。', '',
            '```powershell', '$env:PYTHONPATH="$PWD/src"', '# 在保留原结果的副本中，先将runs.json移至备份；无需网络',
            'python scripts/run_regime_study.py', 'python scripts/report_regime_study.py', 'python scripts/validate_regime_study.py', '```', '',
            'protocol只在新目录创建时运行；不覆盖声明。classifier、conditional、selection-audit、forward-ledgers、runs、portfolio、'
            'daily、全部状态与拒绝路径、数据质量、manifest/hashes和回归验证器一起提交。', '']
    stable = [s for s in snapshots if s['adequate_full_sample'] and s['positive_all3_parts'] and s['at_least5_episodes_each_part']]
    block = ['### 两项分段同正的描述性线索', '',
             '|资产/规则/状态|全段归因净%|完成持仓|三分段持仓数|同状态固定多净%|同状态固定空净%|',
             '|---|---:|---:|---|---:|---:|']
    for s in stable:
        r = next(r for r in full if (r['asset'],r['rule'],r['state'])==(s['asset'],s['rule'],s['state']))
        block.append(f"|{s['asset']}/{s['rule']}/{s['state']}|{pct(r['return_on_initial'])}|{r['entry_episodes']}|"
                     f"{'/'.join(str(p['episodes']) for p in s['parts'])}|{pct(r['fixed_direction_state_returns']['FIXED-LONG'])}|"
                     f"{pct(r['fixed_direction_state_returns']['FIXED-SHORT'])}|")
    block += ['', 'HYPE/EMA/TREND_HIGH的归因净收益仍低于同状态固定多头，未显示超过简单方向敞口的优势。'
              'HYPE/SIMPLE_MR/RANGE_HIGH的描述性数值更高，但它是在680单元中事后筛出，后两段分别只有15/19个完整持仓，'
              '不足各自30的支持标准；小时归因又没有模拟按该状态进出造成的额外成本。两者都是INCONCLUSIVE。'
              '它们不属于预先允许的三个代表，不在看到结果后加入选择器或在本轮反复测试。', '',
              '若保留下一轮线索，只将HYPE/SIMPLE_MR/RANGE_HIGH登记为未来新样本的只读假设；'
              '需要全新时间段至少30个不重叠持仓、10入场日及跨月支持，保留固定方向和空仓对照，'
              '日区间分析至少60天且未必足够积累上述状态持仓。当前数据不足以批准运行时开发。', '',
              f"680单元按结构等价合并后仅{len({(equivalence[r['asset'],r['rule']],r['state']) for r in full})}组，"
              '仍受资产、共享信号与状态重叠影响，不是对应数量的独立检验。', '']
    i = out.index('## 三、滚动训练、验证与确定性选择器')
    out[i:i] = block
    block = ['### 过滤器改善与成本变化', '',
             '|时间段/base|过滤器|净收益差额%|手续费+摩擦差额%|价格+Funding差额%|',
             '|---|---|---:|---:|---:|']
    for r in incremental:
        if r['scenario']=='base' and r['overlay'].startswith('FILTER:'):
            cost = r['fees_difference']+r['spread_cost_attributed_difference']+r['slippage_cost_attributed_difference']
            economic = r['gross_price_pnl_difference']+r['funding_scenario_difference']
            assert abs(r['net_difference']-(economic-cost)/100000)<1e-9
            block.append(f"|{r['horizon']}|{r['overlay']}|{pct(r['net_difference'])}|{pct(cost/100000)}|{pct(economic/100000)}|")
    block += ['', '均相对同家族固定规则，负成本差额代表减少摩擦，不代表新获利。选择器未成交，'
              '没有观察到可评估的实际切换成本，不能推断“切换成本覆盖了其Alpha”。', '']
    i = out.index('## 五、可行性判断与停止条件')
    out[i:i] = block
    (OUT/'report.zh-CN.md').write_text('\n'.join(out),encoding='utf-8',newline='\n')
    sources = ['scripts/'+s for s in ['regime_protocol.py','regime_core.py','run_regime_study.py','report_regime_study.py','validate_regime_study.py']]
    write_json(OUT/'manifest.json', {'source_hashes_normalized_LF':{p:hashlib.sha256((ROOT/p).read_text(encoding='utf-8').encode()).hexdigest() for p in sources},
        'protocol':protocol,'experiment_count':len(runs),'conditional_count':len(cells),
        'prior_inputs':{p.name:source_hash(p) for p in PRIOR.iterdir() if p.is_file() and (p.name.endswith('-ledgers.jsonl.gz') or p.name in {'registry-170.json','hashes.json'})}})
    write_json(OUT/'hashes.json',{p.name:source_hash(p) for p in OUT.iterdir() if p.is_file() and p.name!='hashes.json'})
    print(summary,flush=True)


def sum_months(daily):
    result = defaultdict(float)
    for day, value in daily.items():
        result[date(int(day)*DAY)[:7]] += value
    return result


if __name__=='__main__':
    report()
