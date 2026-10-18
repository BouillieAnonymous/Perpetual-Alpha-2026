"""Readable evidence narrative, not a historical profit leaderboard."""
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone

from long_history_protocol import OUT, RESEARCH, ROOT

from perpetual_alpha.offline_quant.data import source_hash
from perpetual_alpha.offline_quant.run import write_json


def pct(v):
    return 'unknown' if v is None else f'{100*v:.3f}'


def date(t):
    return 'unknown' if t is None else datetime.fromtimestamp(t/1000, timezone.utc).strftime('%Y-%m-%d %H:%M')


def report():
    matrix = json.loads((OUT/'matrix-37.json').read_text(encoding='utf-8'))
    conclusion = json.loads((OUT/'conclusions.json').read_text(encoding='utf-8'))
    quality = json.loads((OUT/'source-quality.json').read_text(encoding='utf-8'))
    context = json.loads((OUT/'context-quality.json').read_text(encoding='utf-8'))
    universe = json.loads((OUT/'long-universe-170.json').read_text(encoding='utf-8'))
    protocol = json.loads((OUT/'protocol.json').read_text(encoding='utf-8'))
    summaries = json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
    counts = conclusion['counts37']
    out = ['# 37个PAR短期正收益组合：冻结规则长期历史复核', '',
           '## 研究口径及直接回答', '',
           'PAR是当前排练数据与方法开发环境，继续研究合理；PAS独立保留。本轮只检验外部市场自身的历史表现，'
           '没有迁移到PAR/PAS执行、修改VPS或启动策略。', '',
           f"严格按原CSV提取37项，在对应外部永续市场的长期基础成本情景中继续为正 **{conclusion['positive_after_base_costs']}项**；"
           f"LONG_HISTORY_REJECTED **{counts.get('LONG_HISTORY_REJECTED', 0)}项**；"
           f"缺少可比/完整情景、INSUFFICIENT_DATA **{counts.get('INSUFFICIENT_DATA', 0)}项**。"
           '正值仍是INCONCLUSIVE，不是已验证Alpha。', '',
           f"其中毛价格收益为负{conclusion['negative_gross']}项，毛收益为正但基础成本后不再为正{conclusion['positive_gross_erased_by_base_costs']}项。"
           '不能将所有失败归因于手续费；也不能将毛正转负全部归于某一成本，需同时看费用、摩擦和资金现金流。', '',
           f"预先定义的多时段/压力/简单方向超额门槛通过项：{conclusion['followup_candidates'] or '无'}。"
           '该门槛仅用于未来PAS观察优先级；即使通过也不是确认性证据。没有正式比赛可执行利润结论。', '',
           '## 一、选择锁定与重复证据', '',
           f"协议在{date(protocol['frozen_ms'])} UTC、查看本轮新回测收益前冻结；"
           '从asset-coverage/comparison.csv仅取venue=par、full_seen、base、非NO-TRADE的170行，'
           '再按原net_scenario_return>0精确得到37行。原字段全部保留，未用EWY/HYPE后续结果剔除候选。', '',
           'original-170.csv、selected-37.csv及registry-170.json提供逐行追溯。原170项状态为131 REJECTED、37 INCONCLUSIVE、2 INSUFFICIENT_DATA。'
           'registry保存原收益/费用/持仓表现、原definition、方向/仓位及原始源摘要；rule-parameter-contract.json提供阈值说明。', '',
           'original-direction-and-sizing.json另存170项旧PAR窗口的完整多空目标、时刻、原仓位特征与已闭合成交段，'
           '绑定原逐资产账本摘要；这些是旧模拟成交，不是实际订单。', '',
           '3对结构等价：BRENT、ETH、GOLD各自Rolling Z/Bollinger。因此37行只有34个结构等价组，仍不是34份独立证据。'
           'ETH/EWY的REGIME-RANDOM是预设随机对照，保留但不能当新Alpha；EWJ压缩与压缩扩张仅在旧样本上碰巧同成交，'
           '不据此认定两条规则在所有历史结构等价。Donchian各变体、同一资产趋势/资金规则还共享收益路径。', '',
           'REGIME-RANDOM在外部重放时仍以原par:资产名计算SHA随机抽样；这个par前缀仅为计算别名，'
           'raw来源、external_coin和Funding始终是对应主网/xyz，不能把别名当作PAR行情。', '',
           '规则完全沿用：1h、24h回看、12h持有/再平衡、ATR1.5倍、Donchian原reset、EMA6/24、z入场2/退出0.5、'
           '压缩168期20%分位、Regime效率0.35/高波动72期80%分位/成本28bps、原随机种子和35%保留概率。'
           'Regime成本阈值28bps没有随外部费率降低而重调。风险预算0.005、原仓位最大初始资本20%，Funding固定±20%。', '',
           '## 二、逐资产来源及可比性', '',
           '|PAR资产|实际外部合约|小时bar数|首个闭合bar UTC|最后UTC|缺失小时桶|自身Funding条数|零量bar|',
           '|---|---|---:|---|---|---:|---:|---:|']
    for asset, q in quality.items():
        b, f = q['bars'], q['funding']
        out.append(f"|{asset}|{q['venue']} {q['real_coin']}|{b['count']}|{date(b['first_ms'])}|{date(b['last_ms'])}|"
                   f"{b['missing_grid_points']}|{f['count']}|{q['zero_volume_bars']}|")
    out += ['', 'BTC/ETH复用原六个月小时价格，本轮只补自身主网Funding；HYPE/EWY复用前轮4998h价格与自身Funding；'
            '其余外部HIP3小时线与Funding新补抓。附加数据曾遇429，失败响应和有界慢速重试均保留；'
            'capture-errors记录失败尝试，不等于最终小时缺口。source-quality另存接收/解码时间、精确Funding时间和元数据数量步长。'
            'Funding时间含毫秒抖动，gap_count统计不严格等于一小时的相邻间隔，不代表漏掉该数量的结算；'
            '缺失小时应读取missing_grid_points并与逐笔Funding覆盖检查共同判断。', '',
            '这些是官方指引的参考标的候选，**不是完全等价的合约**：BTC/ETH/HYPE为原生主网永续，'
            '股票ETF/大宗/FX为xyz永续，各有自己的订单簿与Funding。旧PAR和PAS均不使用这些Funding填历史。'
            '所有外部价格、费率按其自身venue匹配，不跨源拼接基金现金分红、现货Funding或PAR/PAS费率。', '',
            '|资产|真实标的与价格单位|交易时段/关键差异|', '|---|---|---|',
            '|BTC/ETH/HYPE|同名加密资产美元参考价；外部原生USDC永续|永续24/7；与现货不是同一个可成交市场|',
            '|EWY/EWJ|iShares韩国/日本ETF，USD/份；xyz为衍生品而非基金份额|ETF按America/New_York交易日与DST；xyz24/7，闭市有内部定价，分红不直接发给永续持仓|',
            '|GOLD|xyz参考USD/金衡盎司；GC=F是黄金滚动期货，不是同一现货工具|CME期货有维护停盘及到期换月；不将连续期货拼接视为无跳点现货|',
            '|BRENT|xyz:BRENTOIL参考USD/桶；BZ=F为期货替代，非相同执行合约|交易日/维护、合约展期和基差变化均可能影响收益，不宣称与PAR完全同风险|',
            '|USDJPY|xyz:JPY对应USD/JPY：每USD的JPY；PAR旧目录JPY/USD标签有歧义|当前两边Oracle约157.9、同尺度，禁止因标签猜测反转；真实点值/结算转换仍未验证|',
            '|SP500/USDEUR（负对照）|xyz:SP500指数点；xyz:EUR为USD/EUR|指数现货不是可直接持有的永续；FX工作日时段与永续24/7不同|', '',
            '来源核对：[运营方规格](https://docs.trade.xyz/consolidated-resources/specification-index)、'
            '[EWJ发行方](https://www.ishares.com/us/products/239665/ishares-msci-japan-etf)、'
            '[EWY发行方](https://www.ishares.com/us/products/239681/ishares-msci-south-korea-capped-etf)、'
            '[ICE Brent](https://www.ice.com/products/219/Brent-Crude-Futures)、'
            '[CME Gold](https://www.cmegroup.com/markets/metals/precious/gold.contractSpecs.html)。'
            '当前规格不证明整段历史规格/展期安排不变；该限制作为可比性边界保留。', '',
            '### 更长真实市场替代资料', '',
            '额外下载Yahoo免费日线/约两年小时线，作为真实标的或代理数据的质量调查，不自动代入主策略。'
            'EWY资料已在candidate-validation归档，EWJ及其他代理在本目录。价格字段保留raw OHLC、adjclose、股息/拆分原响应。', '',
            '|文件/标的|有效行|起点UTC|终点UTC|时区/币种|最长连续整小时|', '|---|---:|---|---|---|---:|']
    for name, q in context.items():
        if 'count' in q:
            out.append(f"|{name}/{q['symbol']}|{q['count']}|{date(q['first_ms'])}|{date(q['last_ms'])}|"
                       f"{q['timezone']}/{q['currency']}|{q['max_consecutive_hours']}|")
    out += ['', '日线不能替代小时规则；ETF隔夜/周末缺口不能填造连续26h回看；'
            '原始期货与复权/滚动序列不能当作同一份永续成交价。Yahoo加密价格为聚合报价、FX为现货参考，'
            '不是已确认费级、Funding与数量步长的单一执行场所。即便小时数更长，这些替代市场的等价成本后规则测试仍为INSUFFICIENT_DATA。'
            '本轮完成的37项是对应外部永续自身的条件情景，不是所有真实基础工具均完成等价回测。'
            '约半年至七个月比原17天更有信息量，但仍不覆盖多年周期；不能据此证明跨周期稳定。', '',
            '## 三、全部37项主结果（原筛选顺序，非收益排行）', '',
            '资本为各实验独立10000；收益为初始资本比例，不能将37项相加成组合。'
            '资金现金流按真实历史费率及先前收盘价代理Oracle，实际结算Oracle/历史账户收入未知。'
            '完整gross、fees、spread/slippage、funding、turnover、DD、资本占用及每笔拒绝见CSV/JSON/账本。', '',
            '|资产/原规则|原PAR净%|外部毛%|外部base净%|stress净%|连续正月份/总月份|状态|',
            '|---|---:|---:|---:|---:|---:|---|']
    for r in matrix:
        b, s = r['full_base'] or {}, r.get('full_stress') or {}
        out.append(f"|{r['asset']}/{r['rule']}|{pct(r['original_PAR_net'])}|{pct(b.get('gross_return'))}|"
                   f"{pct(b.get('net_scenario_return'))}|{pct(s.get('net_scenario_return'))}|"
                   f"{r.get('positive_months', 0)}/{r.get('month_count', 0)}|{r['long_status']}|")
    out += ['', '状态仅对应当前规则、外部窗口、成本与线性估值假设。负值不是传统量化普遍无效，'
            '正值不证明收益可持续，更不证明PAR/PAS存在利润。已见数据不能重新称为真正样本外。', '',
            '## 四、时间稳定性、方向基准与失败来源', '',
            'nonoverlap-amendment.json在未读取本轮新收益、主重放仍进行时单独记录必要诊断：'
            '每个候选另测PAR原筛选窗口开始之前的外部历史，截止时间直接取原CSV start_ms，不选择有利日期。'
            '保留原主计数与原门槛，不把这一诊断重新叫独立前瞻；所有37项及其简单/匹配方向对照均同规则base重放。', '',
            '60/20/20按时钟顺序划分，验证与末段各隔离12h；每个分区独立冷启动持仓，信号可用此前历史做warm-up。'
            '每自然月也独立重放base，comparison.csv保留全部尝试。主表的正月份来自**全段连续账本**每日PnL按UTC月聚合，'
            '避免每月人为平仓/重开改变主策略。独立月实验之和不应等于全段收益；月初/末不完整月份保留并明确可查时间跨度。', '',
            '基准包括固定20%初始资本多头、固定空头、空仓；每12h固定重平衡，不把固定方向误称为固定数量买入持有。'
            '另提供每项候选的MATCHED-LONG/SHORT，完全保留其触发时刻、退出与仓位绝对值，只改变方向。'
            '这些是因子归因对照，不是新增可交易策略；原策略风险调整仓位可能远小于固定20%，必须同时看风险/资本占用与匹配对照。', '',
            '|资产/规则|筛选窗前净%|验证净%|末段净%|固定多净%|固定空净%|超额均值95%区间下界（多/空，bps/日）|',
            '|---|---:|---:|---:|---:|---:|---:|']
    for r in matrix:
        if not r.get('full_base'):
            continue
        benches = r['simple_benchmarks']
        a = r['daily_excess']['FIXED-LONG']['lower95']
        b = r['daily_excess']['FIXED-SHORT']['lower95']
        ci = ('unknown' if a is None else f'{a*10000:.3f}')+'/'+('unknown' if b is None else f'{b*10000:.3f}')
        out.append(f"|{r['asset']}/{r['rule']}|{pct((r['pre_selection'] or {}).get('net_scenario_return'))}|"
                   f"{pct((r['validation'] or {}).get('net_scenario_return'))}|"
                   f"{pct((r['test'] or {}).get('net_scenario_return'))}|{pct((benches['FIXED-LONG'] or {}).get('net_scenario_return'))}|"
                   f"{pct((benches['FIXED-SHORT'] or {}).get('net_scenario_return'))}|{ci}|")
    out += ['', 'daily-pnl与matrix的daily_excess使用同日配对固定资本PnL，循环移动5日块bootstrap2000次、种子20261018。'
            '95%区间为逐项探索性区间，未做37项家族多重校正，不能当显著发现证明；共同资产、等价规则和长期同向敞口显著降低独立信息量。'
            'bootstrap也不恢复未经历的市场状态。全样本扣除选择偏差的可交易显著性仍未知。', '',
            '长期全段正值的下一步障碍逐项如下（不是收益排名）：']
    positive = [r for r in matrix if r['full_base'] is not None
                and r['full_base']['net_scenario_return'] is not None and r['full_base']['net_scenario_return'] > 0]
    if not positive:
        out += ['', '没有长期全段base正值，因此没有仅凭收益进入PAS观察优先队列的项目。']
    failure_labels = {
        'positive_full_base_complete': '基础情景未完整为正',
        'not_random_control': '属于随机对照',
        'positive_stress': '压力情景未为正',
        'positive_validation': '验证时段未为正',
        'positive_test': '末段未为正',
        'at_least60pct_positive_months': '正月份不足60%',
        'at_least30_entry_signals': '入场信号不足30',
        'excess_CI_lower_gt0_both_directions': '未可靠超越固定多头和空头两个基准'}
    for r in positive:
        out += ['', f"- {r['asset']}/{r['rule']}："+('、'.join(failure_labels[k] for k in r.get('future_followup_failed_checks', [])) or
                  '探索性门槛通过，仍须PAS独立前瞻与多重比较控制')]
        b, s = r['full_base'], r['full_stress']
        out += ['', f"  价格毛收益{pct(b['gross_return'])}%，手续费{pct(b['fees']/b['initial_capital'])}%，"
                f"价差{pct(b['spread_cost_attributed']/b['initial_capital'])}%、滑点{pct(b['slippage_cost_attributed']/b['initial_capital'])}%，"
                f"Funding代理收入{pct(b['funding_scenario']/b['initial_capital'])}%，base净情景{pct(b['net_scenario_return'])}%，"
                f"压力净情景{pct(s['net_scenario_return'])}%，最大回撤{pct(b['max_drawdown_scenario'])}%。"
                '正收益不是纯Funding现金流，也不是已证明的独立方向预测能力。'
                '可保留为低优先级冻结对照，但本轮不足以优先投入PAS实盘或宣称具有稳定优势。']
    out += ['',
            'state-attribution按入场前25期效率>0.35分趋势/震荡，保留每组gross/fees/funding/net；'
            '仅归因，不把结果较好状态再新增为交易过滤。部分减仓段不等于独立交易；Funding频繁再平衡信号也不等于数百个独立下注。', '',
            '失败需要区分：毛负代表价格信号在该市场/窗口本身不佳；毛正净负由费用、价差/滑点和Funding共同影响；'
            '长期正但劣于固定方向可能只是资产趋势敞口；开发好/验证或末段差说明不稳定；'
            '零信号或Funding缺口则是数据/触发不足，不用零收益充当失败或成功证据。', '',
            '### 原170项的长期负收益对照', '',
            'long-universe-170逐项保留原状态和长期全段base。其余133项只做预先指定的长期全段基础对照，'
            '没有因它们在长期偶然变好而扩展调参、替代原37项或将其升级为发现。', '',
            '|原筛选组|长期base负|长期base正|无交易/不完整|', '|---|---:|---:|---:|']
    for selected, title in [(True, '原37正'), (False, '原133非正/无信号')]:
        group = [r for r in universe if r['selected_original'] == selected]
        negative = sum(r['long_full_base'] is not None and r['long_full_base']['fill_count']
                       and r['long_full_base']['net_scenario_return'] is not None
                       and r['long_full_base']['net_scenario_return'] < 0 for r in group)
        positive = sum(r['long_full_base'] is not None and r['long_full_base']['fill_count']
                       and r['long_full_base']['net_scenario_return'] is not None
                       and r['long_full_base']['net_scenario_return'] > 0 for r in group)
        out.append(f'|{title}|{negative}|{positive}|{len(group)-negative-positive}|')
    out += ['', '原正组和原负组并非随机分配，不能用这个表计算因果选择优势；不同资产/时期与规则依赖仍需控制。', '',
            '## 五、执行成本及结论边界', '',
            'native base每边4.5bps、HIP3每边9bps；base完整价差6bps、每边额外滑点2bps、1h延迟。'
            'fee_only保留费率且摩擦为0；stress费率倍增、价差18bps、每边滑点4bps、2h延迟。'
            '费用按真实情景成交名义金额逐笔计，不从资本收益扣一次固定往返bps。', '',
            'spread_cost_attributed与slippage_cost_attributed分开报告：在固定每笔价差/滑点bps下，'
            '分别按参考成交名义金额×半价差和×额外滑点归因；两者之和与原冻结账本摩擦一致，验证器逐笔独立重算。', '',
            '[官方费用](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)与'
            '[Funding公式](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding)为机制依据。'
            '历史用户折扣、xyz fee-scale/growth参数、合约点值变更和可成交深度未重建，所以仍是情景。'
            '报告费率使用已返回的小时FundingRate，不再次乘运营方Funding multiplier；Oracle代理不是实际结算金额。', '',
            '小时OHLC不提供真实买卖边、订单排队、部分成交、盘口冲击与盘中强平路径；低/零成交量bar尤其不能视为深度可用。'
            '归档回撤是小时线性估值账本的回撤，不是完整可执行账户回撤。所有真实net_return/executable_net_return保持null。', '',
            '外部自身测试、外部信号迁移PAR/PAS、真正前瞻验证是三个不同问题。本轮仅完成第一项；'
            '第二项仍参见原external-transfer的独立证据，第三项对37项全部INSUFFICIENT_DATA。'
            'PAR现阶段继续用于开发；未来PAS观察须使用其自身真实可用时间、费率、报价和深度，不能搬用这些历史收益。', '',
            '若有探索性门槛通过项，只能作为下一轮冻结名单观察，不启用；若没有，则现阶段没有应凭本轮收益优先投入PAS的候选。'
            '已见历史即使在代码冻结后下载仍是Retrospective；数据接收/可得时间在raw保留，但历史回测使用闭合bar即已知、下一小时开盘的假设。', '',
            '## 六、数据、版本与复现', '',
            f'共{len(summaries)}个完整实验账本；每资产单独压缩存储全部信号、成交、结算代理、净值与拒绝原因。'
            'comparison.csv/summary.json包括全部尝试；matrix-37提供主结论与每项归因；输入摘要与代码摘要见manifest/hash。', '',
            '```powershell', '$env:PYTHONPATH = "$PWD/src"',
            '# 使用已归档输入，完全离线，不重新抓取或筛选',
            '.venv/Scripts/python.exe scripts/run_long_history.py',
            '.venv/Scripts/python.exe scripts/run_long_nonoverlap.py',
            '.venv/Scripts/python.exe scripts/analyze_long_history.py',
            '.venv/Scripts/python.exe scripts/report_long_history.py',
            '.venv/Scripts/python.exe scripts/validate_long_history.py', '```', '',
            '重算请先复制当前目录或使用独立checkout，以保留原结果；将副本中的nonoverlap-summary.json移至备份后再运行，'
            '因为该诊断默认拒绝覆盖已有汇总。long_history_protocol只允许创建新协议目录；'
            'capture_long_history拒绝覆盖已有抓取，retry仅对相同失败请求慢速有界重试。'
            '没有新指标、参数搜索、LLM、VPS修改、生产风控/交易开关更改或订单。', '']
    (OUT/'report.zh-CN.md').write_text('\n'.join(out), encoding='utf-8', newline='\n')
    source_files = ['scripts/'+s for s in ['long_history_protocol.py', 'capture_long_history.py',
                    'retry_long_history_capture.py', 'run_long_history.py', 'run_long_nonoverlap.py',
                    'analyze_long_history.py', 'report_long_history.py', 'validate_long_history.py']]
    inputs = ['asset-coverage/comparison.csv', 'external-transfer/external-history.jsonl.gz',
              'candidate-validation/public-history.jsonl.gz']
    inputs += [p.relative_to(RESEARCH).as_posix() for p in sorted((RESEARCH/'asset-coverage').glob('par-*-details.jsonl.gz'))]
    write_json(OUT/'manifest.json', {'schema': 'long-history-37-evidence/1', 'protocol': protocol,
        'source_hashes_normalized_LF': {p: hashlib.sha256((ROOT/p).read_text(encoding='utf-8').encode()).hexdigest() for p in source_files},
        'input_hashes': {p: source_hash(RESEARCH/p) for p in inputs},
        'experiment_count': len(summaries), 'status_counts': dict(Counter(r['long_status'] for r in matrix)),
        'all_execution_and_actual_funding_unknown': True})
    write_json(OUT/'hashes.json', {p.name: source_hash(p) for p in sorted(OUT.iterdir()) if p.is_file() and p.name != 'hashes.json'})


if __name__ == '__main__':
    report()
