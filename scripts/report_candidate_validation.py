"""Generate the Chinese candidate report and bind all evidence/source hashes."""
import json
from datetime import datetime, timezone

from run_candidate_validation import OUT, ROOT

from perpetual_alpha.offline_quant.data import source_hash
from perpetual_alpha.offline_quant.run import write_json


def date(value):
    return 'unknown' if value is None else datetime.fromtimestamp(value/1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def pct(value):
    return 'unknown' if value is None else f'{100*value:.3f}'


def report():
    rows = json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
    quality = json.loads((OUT/'quality.json').read_text(encoding='utf-8'))
    etf = json.loads((OUT/'etf-quality.json').read_text(encoding='utf-8'))
    model = json.loads((OUT/'conditional-funding.json').read_text(encoding='utf-8'))
    protocol = json.loads((OUT/'protocol.json').read_text(encoding='utf-8'))
    prior = json.loads((OUT/'prior-source-inventory.json').read_text(encoding='utf-8'))
    look = {(r['venue'], r['symbol'], r['rule'], r['split'], r['scenario']): r for r in rows}
    lines = ['# EWY / HYPE 冻结候选复核与信息源审计', '',
             '## 当前结论', '',
             '没有候选获得PROMISING或启用批准。长历史使两个候选均降温：xyz:EWY ATR毛收益为正但成本后为负，'
             'Donchian毛收益已经为负；主网HYPE FUNDING-MEAN24显著落后于固定做多，控制价格趋势后没有稳定增量证据。'
             '旧par正情景不等于正式比赛盈利，且HYPE固定做空对照高于Funding规则。', '',
             '**关键市场身份差异：**当前[官方指南](https://perpetual-alpha26.github.io/docs/)明确使用pas/USDP。'
             '本次官方perpDexs返回par=Perpetual Alpha Rehearsal、pas=Perpetual Alpha；spotMeta区分USDPR(index3032)与USDP(index3307)，'
             'pas元数据collateralToken=3307。不能把旧par研究称作已验证的正式比赛pas策略，更不能拼接两者历史。'
             '本次保留用户指定par研究原样；pas仅作信息源可用性审计，没有改Collector或策略配置。', '',
             f"研究冻结时间 **{date(protocol['freeze_ms'])} UTC**，基于PR15 c69f76a；规则在新结果计算前写入protocol.json。"
             '过去研究窗口已参与资产选择；所有补下载历史仍为Retrospective。分区名称test也不代表本次真正未见的前瞻样本。', '',
             '## 一、信息源实际可用性', '',
             '审查PR15 c69f76a、PR16 5584806、PR17 0e100aa及本地分支归档；PR16配置不被视作VPS数据。'
             '本次通过已验证SSH主机指纹，在远端SQLite mode=ro、query_only、BEGIN事务中导出，不热复制活跃主文件、不修改表或服务。'
             'VPS仅有snapshots/runtime表，不能假定PR16新的事件存储已部署。', '',
             '|来源/资产|数据|条数|起点UTC|终点UTC|适用性|', '|---|---|---:|---|---|---|']
    for r in prior:
        lines.append(f"|{r['source']} {r['symbol']}|{r['type']}|{r['count']}|{date(r['first_ms'])}|{date(r['last_ms'])}|{r['supports']}|")
    for s, r in quality['vps']['assets'].items():
        lines.append(f"|本次VPS {s}|bid/ask/20档book、Oracle/Mark、indicative funding、premium/OI|{r['count']}|"
                     f"{date(r['first_ms'])}|{date(r['last_ms'])}|短窗口真实报价；不足原26h回看及完整12h退出|")
    lines += ['', 'PR16 observations与PR17 ten-asset-probe是同一段观察的归档，不能重复计算样本量；表内为原始观察数，包含未通过质量检查的记录。'
              '本次VPS每资产6132条、约12.79小时，book事件间隔中位约7.47秒、最大低于10秒、无>60秒间隙；'
              '这是该短窗口的核查，不是数周连续覆盖。', '',
              '旧snapshots主键(symbol,event_ms)会抑制book时间未变时的新Oracle观察；不存在“每次消息都保存”的保证。'
              '本次VPS保存raw.context与raw.book，Oracle/Mark有本地context_received_ms，Oracle上游事件时间仍未知；'
              '不能把book.event_ms当Oracle的源时间。EWY有1行book/context接收偏差超过2秒，HYPE为0行。', '',
              'VPS库中没有历史OHLCV表，也没有完整逐笔市场成交表。Funding字段是当时指示费率，不是已结算资金账；'
              '只有官方fundingHistory可补对应历史结算费率。OI/premium在VPS为短快照序列，不能扩展成主网长期历史。', '',
              '### 官方接口补抓与新采集', '',
              '|类型|本次可得|历史限制/实际可用时间|能否真实交易回放|',
              '|---|---|---|---|',
              '|K线|主网HYPE/xyz:EWY各4998根1h；PAR HYPE430、EWY429；日线另存|最新5000根/周期；响应下载时间已保存，不能回填为历史可得时间|仅OHLC价格/成本情景|',
              '|Funding+premium|主网各4998条小时结算、PAR HYPE429/EWY452|480h分页，保留精确毫秒，按小时桶无缺口；premium是fundingHistory组件，不是实时全路径|费率可复算，结算Oracle/账户现金流仍缺|',
              '|Oracle/Mark/OI|当前assetCtx + VPS约12.79h及旧短探测|公开info无已验证的长期全市场Oracle/Mark/OI历史序列；当前值不可后填|短域报价研究，不能长期完整回放|',
              '|bid/ask/depth|本次四市场各24张book，旧PR16/17及VPS见上表|l2Book是当前快照；保留服务器book时间、开始/收到/解码就绪时间与session|可检查报价边/深度；没有订单队列及真实成交确认|',
              '|成交|本次每市场recentTrades各10笔，已删除对手方地址|只有近期数据；成交时间与下载时间分开；不能假定为完整历史成交带|不能证明限价排队或订单可成交|',
              '|官方S3归档|未下载|官方说明包含部分L2/asset_ctx档案但不保证完整，requester-pays需要付费授权；本轮不用|不能以“存在S3”冒充已取得可用档案|', '',
              '接口依据：[官方info](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)、'
              '[perpetuals](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals)、'
              '[历史归档](https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data)。缺失资金费绝不取主网填PAR。', '',
              '|新历史序列|条数|首个已闭合bar/结算UTC|最后UTC|小时/日历网格缺口|零成交量bar|',
              '|---|---:|---|---|---:|---:|']
    for key, r in quality['bars'].items():
        lines.append(f"|{key}|{r['count']}|{date(r['first_ms'])}|{date(r['last_ms'])}|{r['missing_grid_points']}|{r['zero_volume_bars']}|")
    for key, r in quality['funding'].items():
        lines.append(f"|{key} funding|{r['count']}|{date(r['first_ms'])}|{date(r['last_ms'])}|{r['missing_grid_points']}|—|")
    lines += ['', 'Funding毫秒抖动会导致原始间隔略大于1h；quality.gap_count记录这种原始间隔，'
              'missing_grid_points才是缺失小时桶，不能混为上千个缺失小时。OHLC无缺口也不代表有连续成交或真实盘口。', '',
              'pas-discovery只探测可用性：本次pas:EWY历史请求返回1根小时bar，pas:HYPE为0根；'
              '48h Funding请求各返回48条。结算费率存在并不能代替价格/可成交深度，未对pas生成PnL。', '',
              '仍未知：历史合约乘数/真实USDPR计价转换与抵押品兑美元价值；账户真实费级/折扣、每笔feeToken；'
              '结算Oracle精确值和上游源时间；订单到达、撤单、排队、成交确认与持有期内强平路径。'
              '本轮不读取账户账单来伪装假想持仓的实际PnL。小时净值回撤不涵盖全部盘中极值和保证金清算，'
              '所有net_return/executable_net_return仍为null。', '',
              '## 二、EWY三类价格与长期数据质量', '',
              '真实EWY ETF是NYSE Arca上市基金份额；xyz:EWY是USDC永续、par:EWY是USDPR rehearsal合约，'
              '正式pas:EWY另属USDP市场。相关标的不代表可互换的交易、资金费或持仓收益。'
              '[运营方规格](https://docs.trade.xyz/perpetuals/specifications-and-schedules/specification-index.md)将EWY映射为一份ETF的美元参考价，'
              '同时区分外部与内部定价时段；衍生品非ETF份额，不将ETF股息现金直接计入永续持仓。', '',
              '真实ETF免费Yahoo Chart原始响应已归档，发行方页面同期收盘150.44与Yahoo最新150.440002一致，'
              '但这只交叉核实一个最新收盘点，不证明整段供应商历史无误。'
              '[发行方信息](https://www.ishares.com/us/products/239681/ishares-msci-south-korea-capped-etf)与'
              '[NYSE交易日历](https://www.nyse.com/trade/hours-calendars)为标的和时段依据。', '',
              '|ETF数据|有效条数|起点UTC（供应商bar起点）|终点UTC|说明|', '|---|---:|---|---|---|']
    for key, r in etf.items():
        lines.append(f"|{key}|{r['count']}|{date(r['first_ms'])}|{date(r['last_ms'])}|null OHLCV {r['null_ohlcv_rows']}；分红事件{r['dividend_events']}；最长连续整小时{r['max_consecutive_exact_hours']}|")
    lines += ['', 'ETF时区America/New_York（保留DST偏移），常规交易09:30–16:00 ET；周末、假日和提前收市不能当缺K线填造。'
              'Yahoo日线时间戳是session起点而不是日收盘实际发布日期；小时数据每个交易日最多7根，最长只有7个连续整小时，'
              '不足冻结代码需要的26个连续小时。因此ETF原小时规则状态INSUFFICIENT_DATA。', '',
              '保留raw OHLC、adjclose、dividends/splits原字段，禁止将复权close与未复权high/low混用。'
              '本次日线含29个股息事件、0个split事件；这是供应商返回的记录，不是对所有公司行动完整性的认证。'
              '发行方列示2025-12-16除息2.037071/份。复权历史可被事后修订，非point-in-time特征。'
              '日线仅用于长期涨跌环境诊断，etf-quality.annual_context保留各年首末价格及复权变化，'
              '没有把24小时回看改成24日，也没有把12小时持仓改成12日来制造所谓相同规则回测。', '',
              '### 固定ATR/Donchian回测', '',
              'ATR仍为单小时变动超过过去24期ATR的1.5倍；Donchian仍突破过去24期高低点并沿用reset规则；'
              '持仓12h、既有风险仓位上限20%、初始资本10000。费用按每次成交名义金额；'
              'xyz/PAR基础9bps、主网原生HYPE4.5bps；base价差6bps+每边滑点2bps+1h延迟，'
              'fee_only无摩擦，stress费用加倍、价差18bps+每边滑点4bps+2h延迟。历史账户费级、xyz历史deployer参数均未重建。', '',
              '基础费率与HIP-3公式依据[官方费用文档](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)；'
              '本轮9bps和4.5bps均为明确情景，并非已取得历史用户实际费率。', '',
              '|市场/规则|毛价格%|funding现金代理|费用|摩擦|base净情景%|stress净情景%|成交段|',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        if row['split'] != 'full_retrospective' or row['scenario'] != 'base' or 'EWY' not in row['symbol'] or row['rule'] == 'no-trade':
            continue
        stress = look[row['venue'], row['symbol'], row['rule'], row['split'], 'stress']
        lines.append(f"|{row['venue']}/{row['symbol']}/{row['rule']}|{pct(row['gross_return'])}|{row['funding_scenario']:.2f}|"
                     f"{row['fees']:.2f}|{row['spread_slippage_cost']:.2f}|{pct(row['net_scenario_return'])}|"
                     f"{pct(stress['net_scenario_return'])}|{row['closed_segments']}|")
    lines += ['', '所有金额是各自场所假设线性账本单位；不可将USDC、USDPR及USDP相加。'
              '外部ATR毛正转净负显示成本障碍；Donchian毛负不能归因于成本。旧par全段正值与外部长期负值说明'
              '近期行情/市场微观差异可能影响结果，尚不能把差异归因于单一原因。', '',
              '|市场/规则|开发净情景%|验证净情景%|末段净情景%|', '|---|---:|---:|---:|']
    for venue, symbol, rule in [('mainnet', 'xyz:EWY', 'atr'), ('mainnet', 'xyz:EWY', 'donchian'),
                                ('par', 'par:EWY', 'atr'), ('par', 'par:EWY', 'donchian')]:
        values = [look[venue, symbol, rule, s+'_retrospective', 'base']['net_scenario_return'] for s in ['development', 'validation', 'test']]
        lines.append(f'|{venue}/{rule}|'+'|'.join(pct(v) for v in values)+'|')
    lines += ['', '完整预设自然月结果全部在comparison.csv；不选最佳月份。ewy-trend-attribution.json按入场前既有efficiency>0.35分趋势/震荡，'
              '只是收益归因，不新增过滤条件。主网ATR趋势组40成交段净约+4.47/10000、震荡组114段约−528.57/10000；'
              '不能据这点微小趋势组正值再调参或宣称稳定趋势Alpha。', '',
              '## 三、HYPE：价格、Funding与趋势控制', '',
              '原规则精确复用funding_orders(mode=mean24,rebalance=12)：(t−24h,t]至少24条已结算费率，'
              '最新费率不超过2h，UTC每12h取−0.2×sign(mean)。保留真实毫秒结算时间，不把下一小时费率提前纳入。'
              '当时费率恰已公开是历史情景假设；抓取响应只证明本次下载时可得。', '',
              '反向Funding、同资金预算动量24h、固定多/空均使用相同可用Funding决策时刻，独立重放成交与资金费，'
              '不是将净收益简单取负。固定多空每12h维持初始资本20%名义目标，因取整/再平衡可有部分减仓；成交段不是独立完整交易数。', '',
              '|市场/规则|毛价格%|Funding现金代理|费用|摩擦|净情景%|最大回撤%|成交段|',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        if row['split'] != 'full_retrospective' or row['scenario'] != 'base' or 'HYPE' not in row['symbol']:
            continue
        lines.append(f"|{row['venue']}/{row['rule']}|{pct(row['gross_return'])}|{row['funding_scenario']:.2f}|"
                     f"{row['fees']:.2f}|{row['spread_slippage_cost']:.2f}|{pct(row['net_scenario_return'])}|"
                     f"{pct(row['max_drawdown_scenario'])}|{row['closed_segments']}|")
    lines += ['', '主网Funding方向407个成熟标签中390次做空、17次做多，几乎是空头敞口；'
              '其约+102.35的资金现金代理不足覆盖约−1699.50的价格损益。固定做多约+15.416%高于反向Funding约+15.008%，'
              '不支持将反向对照的正值当作新发现。旧par原Funding约+1.249%，低于固定做空约+2.106%；'
              '因此原积极结果不能据此归因为独立Funding预测优势。', '',
              '**“实际Funding”边界：**费率来自实际官方fundingHistory，但现金使用持仓数量×先前收盘价代理Oracle；'
              '[官方结算公式](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding)需要结算Oracle。'
              '我们没有历史精确结算Oracle或该假想持仓对应的真实账户userFunding，故actual_funding_cashflow仍unknown，'
              '表内绝不是已发生账户收入。资金费不年化替代持仓账本，不以实时premium/OI后填历史。', '',
              '### 控制过去趋势后的增量检验', '',
              '固定传统模型：下一小时open到12h后open的对数收益；基础OLS为常数+过去24h对数动量，'
              '增强OLS增加Funding方向。仅开发段拟合，HAC lag2；预测验证/末段，12h隔离并删除跨区间标签。'
              '没有调参、试多个标签周期或重新选择方向；下载前协议固定了这一检验。', '',
              f"主网{model['mainnet']['total_labels']}个标签，开发{model['mainnet']['train_labels']}；"
              f"开发Funding方向系数{model['mainnet']['train_funding_coefficient']:.6f}，HAC2 p={model['mainnet']['train_HAC2_pvalue']:.3f}。"
              '这未显示统计上可靠的增量；p值不是概率证明，也不是未见数据检验。', '',
              '|已见分段|标签数|基础MSE|加Funding MSE|基础方向正确率|加Funding方向正确率|', '|---|---:|---:|---:|---:|---:|']
    for split in ['validation', 'test']:
        r = model['mainnet'][split]
        lines.append(f"|{split}|{r['labels']}|{r['MSE_base']:.8f}|{r['MSE_augmented']:.8f}|"
                     f"{pct(r['direction_accuracy_base'])}%|{pct(r['direction_accuracy_augmented'])}%|")
    lines += ['', '验证段加入Funding更差，末段只微小改善，方向正确率仍低于50%；没有跨分段稳定增量。'
              '按历史动量负/非负固定分层的Funding方向调整均值约−37.52/+1.76bps，保留在conditional-funding.json，'
              '不能挑选有利分层包装Alpha。PAR只有33个成熟标签，模型检验直接INSUFFICIENT_DATA。', '',
              '## 四、前瞻观察与下一轮最小证据', '',
              '补抓的PAR小时数据只比旧档案多14小时，且都早于本次freeze，不是独立前瞻。'
              '本次120秒探测覆盖每场所/资产24张book；PAR两资产book时间年龄约2.30–2.83秒，24/24均未通过预先使用的2秒门槛。'
              '这里2秒是本轮附加管线诊断，不是原策略参数。旧迁移模块使用10秒book年龄及2秒跨源接收偏差；'
              '按其10秒年龄单项，两资产各24张都通过。两者不能混淆，也不能仅由2秒诊断失败认定不可成交。'
              '前瞻证据不足的结论不依赖这项严格诊断：后冻结有效决策和完整12h退出均为0。', '',
              'prospective.json明确postfreeze可用的12h决策数=0、成熟入场退出对=0、状态INSUFFICIENT_DATA。'
              'next-quote-diagnostics.json记录下一张实际收到的报价时间和买卖边，raw probe保留全20档；'
              '它们是管线诊断，不是假策略成交。没有造出“未成交=零收益”或“无法退出=已平仓”的收益。', '',
              '最小技术窗口：冻结EWY需要约26h连续已闭合bar回看，再等待真实信号并完成12h持仓及延迟退出；'
              'Funding需要完整24h结算+等待至多12h调度+约13h持仓/执行，约37–49h才可能有首个成熟样本。'
              '有数据不保证出现信号。正式pas当前0/1根小时K线远不足；必须先单独积累，不把par当warm-up。', '',
              '研究性前瞻建议下限：至少90个日历日、每个候选至少30个非重叠成熟持仓、覆盖多种趋势和至少两个自然月；'
              'EWY须跨正常交易、周末/假日、开盘跳空。这个下限仅用于是否值得评估，不是盈利证明；'
              '置信区间按日/持仓块处理依赖，继续与空仓、固定方向和动量比较。条件不足继续INSUFFICIENT_DATA。', '',
              '必须采集字段：venue/network/dex/coin、collateral token index/name、meta及费率公式vintage、数量步长/价格精度；'
              'bar开闭合与真实收盘确认/received/decoded/durable-available；Funding精确结算/公布接收/费率/premium和结算Oracle；'
              'book服务器事件/收到/就绪、序号/重连/缺口/session、全bid/ask价格数量；Oracle/Mark/premium/OI各自源时间（未知写null）及各自接收时间；'
              '成交event/receive/price/size/side/trade id；时钟偏差、RTT、决策与模拟下单延迟、每笔费用/摩擦/资本占用。', '',
              '回放必须选择决策后下一张真实可用报价，多买ask/平bid、空卖bid/平ask，逐档消耗深度，记录量不足、陈旧、缺口、'
              '未成交、部分成交、无法退出及开放风险；限价单没有队列模型不可默认maker成交。正式pas先独立核对估值与费用。'
              '本轮没有运行持续采集服务、修改VPS、真实下单或启用策略。', '',
              '## 五、复现与文件', '',
              f'实际完成{len(rows)}组固定规则/时间分段/成本情景。summary.json/comparison.csv含全部尝试，ledgers.jsonl.gz含信号、成交、资金和净值；'
              '最大回撤/换手/仓位/拒绝及缺失状态完整保留。ewy-trend-attribution、conditional-labels和ETF CSV提供独立归因/统计。'
              'quality和prior-source-inventory为资产/来源的日期、频率、缺口和可用时间证据；protocol、manifest及hashes绑定输入和代码。', '',
              '```powershell', '$env:PYTHONPATH = "$PWD/src;$PWD/.cache/quant-libs"',
              '# quant-libs来自仓库已固定的config/requirements-quant-research.txt，非生产依赖',
              '.venv/Scripts/python.exe scripts/run_candidate_validation.py',
              '.venv/Scripts/python.exe scripts/validate_candidate_validation.py',
              '# 新抓取必须使用新的目录，不覆盖当前vintage；纯公开且有界',
              '.venv/Scripts/python.exe scripts/capture_candidate_validation.py --output .cache/new-candidate-capture',
              '.venv/Scripts/python.exe scripts/capture_candidate_validation.py --venue-audit --output .cache/new-venue-audit',
              '```', '',
              'run默认从归档输入重算本目录；需保留既有结果时在独立checkout运行，或先复制研究目录。'
              'audit_candidate_sources.py为只读VPS/ETF原始获取实现，inventory脚本初次抽取PR16/17需对应本地worktree；'
              '验证和回测本身仅依赖PR15内已归档公开输入。没有付费数据请求。', '']
    (OUT/'report.zh-CN.md').write_text('\n'.join(lines), encoding='utf-8', newline='\n')
    sources = ['scripts/capture_candidate_validation.py', 'scripts/audit_candidate_sources.py',
               'scripts/run_candidate_validation.py', 'scripts/inventory_candidate_validation.py', 'scripts/report_candidate_validation.py']
    write_json(OUT/'manifest.json', {'schema': 'frozen-candidate-validation/1', 'parent_commit': protocol['parent_commit'],
                                   'runner_sources_normalized_sha256': {p: __import__('hashlib').sha256((ROOT/p).read_text(encoding='utf-8').encode()).hexdigest() for p in sources},
                                   'experiment_count': len(rows), 'actual_funding_cashflow': None,
                                   'actual_executable_PnL': None, 'frozen_signals': 'unchanged offline_quant; source checked by existing validator',
                                   'selection_bias': 'EWY/HYPE selected on prior results; all downloaded history is retrospective',
                                   'market_identity': 'par rehearsal/USDPR is NOT current official pas/USDP',
                                   'source_urls': ['https://perpetual-alpha26.github.io/docs/',
                                                   'https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint',
                                                   'https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding',
                                                   'https://docs.trade.xyz/perpetuals/specifications-and-schedules/specification-index.md',
                                                   'https://www.nyse.com/trade/hours-calendars'],
                                   'input_sha256': {p.name: source_hash(p) for p in sorted(OUT.iterdir())
                                                    if p.name in {'public-history.jsonl.gz', 'protocol.json', 'vps-readonly-export.json.gz',
                                                                  'quote-probe.jsonl.gz', 'prior-pr-inputs.jsonl.gz', 'venue-identity-audit.jsonl.gz',
                                                                  'pas-discovery.jsonl.gz', 'yahoo-daily.response.gz', 'yahoo-hourly.response.gz'}}})
    write_json(OUT/'hashes.json', {p.name: source_hash(p) for p in sorted(OUT.iterdir()) if p.is_file() and p.name != 'hashes.json'})


if __name__ == '__main__':
    report()
