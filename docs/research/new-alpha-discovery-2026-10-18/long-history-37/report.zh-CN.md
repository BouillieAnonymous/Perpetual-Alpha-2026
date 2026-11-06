# 37个PAR短期正收益组合：冻结规则长期历史复核

## 研究口径及直接回答

PAR是当前排练数据与方法开发环境，继续研究合理；PAS独立保留。本轮只检验外部市场自身的历史表现，没有迁移到PAR/PAS执行、修改VPS或启动策略。

严格按原CSV提取37项，在对应外部永续市场的长期基础成本情景中继续为正 **1项**；LONG_HISTORY_REJECTED **36项**；缺少可比/完整情景、INSUFFICIENT_DATA **0项**。正值仍是INCONCLUSIVE，不是已验证Alpha。

其中毛价格收益为负28项，毛收益为正但基础成本后不再为正8项。不能将所有失败归因于手续费；也不能将毛正转负全部归于某一成本，需同时看费用、摩擦和资金现金流。

预先定义的多时段/压力/简单方向超额门槛通过项：无。该门槛仅用于未来PAS观察优先级；即使通过也不是确认性证据。没有正式比赛可执行利润结论。

## 一、选择锁定与重复证据

协议在2026-10-18 15:33 UTC、查看本轮新回测收益前冻结；从asset-coverage/comparison.csv仅取venue=par、full_seen、base、非NO-TRADE的170行，再按原net_scenario_return>0精确得到37行。原字段全部保留，未用EWY/HYPE后续结果剔除候选。

original-170.csv、selected-37.csv及registry-170.json提供逐行追溯。原170项状态为131 REJECTED、37 INCONCLUSIVE、2 INSUFFICIENT_DATA。registry保存原收益/费用/持仓表现、原definition、方向/仓位及原始源摘要；rule-parameter-contract.json提供阈值说明。

original-direction-and-sizing.json另存170项旧PAR窗口的完整多空目标、时刻、原仓位特征与已闭合成交段，绑定原逐资产账本摘要；这些是旧模拟成交，不是实际订单。

3对结构等价：BRENT、ETH、GOLD各自Rolling Z/Bollinger。因此37行只有34个结构等价组，仍不是34份独立证据。ETH/EWY的REGIME-RANDOM是预设随机对照，保留但不能当新Alpha；EWJ压缩与压缩扩张仅在旧样本上碰巧同成交，不据此认定两条规则在所有历史结构等价。Donchian各变体、同一资产趋势/资金规则还共享收益路径。

REGIME-RANDOM在外部重放时仍以原par:资产名计算SHA随机抽样；这个par前缀仅为计算别名，raw来源、external_coin和Funding始终是对应主网/xyz，不能把别名当作PAR行情。

规则完全沿用：1h、24h回看、12h持有/再平衡、ATR1.5倍、Donchian原reset、EMA6/24、z入场2/退出0.5、压缩168期20%分位、Regime效率0.35/高波动72期80%分位/成本28bps、原随机种子和35%保留概率。Regime成本阈值28bps没有随外部费率降低而重调。风险预算0.005、原仓位最大初始资本20%，Funding固定±20%。

## 二、逐资产来源及可比性

|PAR资产|实际外部合约|小时bar数|首个闭合bar UTC|最后UTC|缺失小时桶|自身Funding条数|零量bar|
|---|---|---:|---|---|---:|---:|---:|
|BTC|Hyperliquid mainnet native BTC|4392|2026-04-18 01:00|2026-10-18 00:00|0|4392|0|
|ETH|Hyperliquid mainnet native ETH|4392|2026-04-18 01:00|2026-10-18 00:00|0|4392|0|
|HYPE|Hyperliquid mainnet native HYPE|4998|2026-03-24 09:00|2026-10-18 14:00|0|4998|0|
|EWY|Hyperliquid mainnet xyz xyz:EWY|4998|2026-03-24 09:00|2026-10-18 14:00|0|4998|2|
|EWJ|Hyperliquid mainnet xyz xyz:EWJ|4998|2026-03-24 10:00|2026-10-18 15:00|0|4998|268|
|GOLD|Hyperliquid mainnet xyz xyz:GOLD|4998|2026-03-24 10:00|2026-10-18 15:00|0|4998|0|
|BRENT|Hyperliquid mainnet xyz xyz:BRENTOIL|4998|2026-03-24 10:00|2026-10-18 15:00|0|4998|0|
|USDJPY|Hyperliquid mainnet xyz xyz:JPY|4998|2026-03-24 10:00|2026-10-18 15:00|0|4998|51|
|SP500|Hyperliquid mainnet xyz xyz:SP500|4998|2026-03-24 10:00|2026-10-18 15:00|0|4998|0|
|USDEUR|Hyperliquid mainnet xyz xyz:EUR|4998|2026-03-24 10:00|2026-10-18 15:00|0|4998|87|

BTC/ETH复用原六个月小时价格，本轮只补自身主网Funding；HYPE/EWY复用前轮4998h价格与自身Funding；其余外部HIP3小时线与Funding新补抓。附加数据曾遇429，失败响应和有界慢速重试均保留；capture-errors记录失败尝试，不等于最终小时缺口。source-quality另存接收/解码时间、精确Funding时间和元数据数量步长。Funding时间含毫秒抖动，gap_count统计不严格等于一小时的相邻间隔，不代表漏掉该数量的结算；缺失小时应读取missing_grid_points并与逐笔Funding覆盖检查共同判断。

这些是官方指引的参考标的候选，**不是完全等价的合约**：BTC/ETH/HYPE为原生主网永续，股票ETF/大宗/FX为xyz永续，各有自己的订单簿与Funding。旧PAR和PAS均不使用这些Funding填历史。所有外部价格、费率按其自身venue匹配，不跨源拼接基金现金分红、现货Funding或PAR/PAS费率。

|资产|真实标的与价格单位|交易时段/关键差异|
|---|---|---|
|BTC/ETH/HYPE|同名加密资产美元参考价；外部原生USDC永续|永续24/7；与现货不是同一个可成交市场|
|EWY/EWJ|iShares韩国/日本ETF，USD/份；xyz为衍生品而非基金份额|ETF按America/New_York交易日与DST；xyz24/7，闭市有内部定价，分红不直接发给永续持仓|
|GOLD|xyz参考USD/金衡盎司；GC=F是黄金滚动期货，不是同一现货工具|CME期货有维护停盘及到期换月；不将连续期货拼接视为无跳点现货|
|BRENT|xyz:BRENTOIL参考USD/桶；BZ=F为期货替代，非相同执行合约|交易日/维护、合约展期和基差变化均可能影响收益，不宣称与PAR完全同风险|
|USDJPY|xyz:JPY对应USD/JPY：每USD的JPY；PAR旧目录JPY/USD标签有歧义|当前两边Oracle约157.9、同尺度，禁止因标签猜测反转；真实点值/结算转换仍未验证|
|SP500/USDEUR（负对照）|xyz:SP500指数点；xyz:EUR为USD/EUR|指数现货不是可直接持有的永续；FX工作日时段与永续24/7不同|

来源核对：[运营方规格](https://docs.trade.xyz/consolidated-resources/specification-index)、[EWJ发行方](https://www.ishares.com/us/products/239665/ishares-msci-japan-etf)、[EWY发行方](https://www.ishares.com/us/products/239681/ishares-msci-south-korea-capped-etf)、[ICE Brent](https://www.ice.com/products/219/Brent-Crude-Futures)、[CME Gold](https://www.cmegroup.com/markets/metals/precious/gold.contractSpecs.html)。当前规格不证明整段历史规格/展期安排不变；该限制作为可比性边界保留。

### 更长真实市场替代资料

额外下载Yahoo免费日线/约两年小时线，作为真实标的或代理数据的质量调查，不自动代入主策略。EWY资料已在candidate-validation归档，EWJ及其他代理在本目录。价格字段保留raw OHLC、adjclose、股息/拆分原响应。

|文件/标的|有效行|起点UTC|终点UTC|时区/币种|最长连续整小时|
|---|---:|---|---|---|---:|
|context-BRENT-1d.json.gz/BZ=F|4784|2007-07-30 04:00|2026-10-16 04:00|America/New_York/USD|1|
|context-BRENT-1h.json.gz/BZ=F|11389|2024-10-20 22:00|2026-10-16 20:59|America/New_York/USD|24|
|context-BTC-1d.json.gz/BTC-USD|4415|2014-09-17 00:00|2026-10-18 00:00|UTC/USD|1|
|context-BTC-1h.json.gz/BTC-USD|17307|2024-10-19 15:00|2026-10-18 15:34|UTC/USD|7647|
|context-ETH-1d.json.gz/ETH-USD|3266|2017-11-09 00:00|2026-10-18 00:00|UTC/USD|1|
|context-ETH-1h.json.gz/ETH-USD|17307|2024-10-19 15:00|2026-10-18 15:35|UTC/USD|7647|
|context-EWJ-1d.json.gz/EWJ|6738|2000-01-03 14:30|2026-10-16 13:30|America/New_York/USD|1|
|context-EWJ-1h.json.gz/EWJ|3479|2024-10-21 13:30|2026-10-16 20:00|America/New_York/USD|7|
|context-GOLD-1d.json.gz/GC=F|6558|2000-08-30 04:00|2026-10-16 04:00|America/New_York/USD|1|
|context-GOLD-1h.json.gz/GC=F|11447|2024-10-20 22:00|2026-10-16 20:59|America/New_York/USD|47|
|context-SP500-1d.json.gz/^GSPC|6738|2000-01-03 14:30|2026-10-16 13:30|America/New_York/USD|1|
|context-SP500-1h.json.gz/^GSPC|3479|2024-10-21 13:30|2026-10-16 20:00|America/New_York/USD|7|
|context-USDEUR-1d.json.gz/EURUSD=X|5937|2003-12-01 00:00|2026-10-17 12:20|Europe/London/USD|1|
|context-USDEUR-1h.json.gz/EURUSD=X|12302|2024-10-20 23:00|2026-10-16 22:59|Europe/London/USD|119|
|context-USDJPY-1d.json.gz/JPY=X|6957|2000-01-03 00:00|2026-10-16 20:59|Europe/London/JPY|1|
|context-USDJPY-1h.json.gz/JPY=X|12216|2024-10-20 23:00|2026-10-16 20:00|Europe/London/JPY|119|

日线不能替代小时规则；ETF隔夜/周末缺口不能填造连续26h回看；原始期货与复权/滚动序列不能当作同一份永续成交价。Yahoo加密价格为聚合报价、FX为现货参考，不是已确认费级、Funding与数量步长的单一执行场所。即便小时数更长，这些替代市场的等价成本后规则测试仍为INSUFFICIENT_DATA。本轮完成的37项是对应外部永续自身的条件情景，不是所有真实基础工具均完成等价回测。约半年至七个月比原17天更有信息量，但仍不覆盖多年周期；不能据此证明跨周期稳定。

## 三、全部37项主结果（原筛选顺序，非收益排行）

资本为各实验独立10000；收益为初始资本比例，不能将37项相加成组合。资金现金流按真实历史费率及先前收盘价代理Oracle，实际结算Oracle/历史账户收入未知。完整gross、fees、spread/slippage、funding、turnover、DD、资本占用及每笔拒绝见CSV/JSON/账本。

|资产/原规则|原PAR净%|外部毛%|外部base净%|stress净%|连续正月份/总月份|状态|
|---|---:|---:|---:|---:|---:|---|
|BRENT/ROLLING_Z|0.497|-1.523|-13.400|-28.570|1/8|LONG_HISTORY_REJECTED|
|BRENT/BOLLINGER|0.497|-1.523|-13.400|-28.570|1/8|LONG_HISTORY_REJECTED|
|BTC/DONCHIAN_EXPANSION|0.149|-0.118|-5.005|-11.329|2/7|LONG_HISTORY_REJECTED|
|BTC/REGIME-TREND|0.302|0.339|-1.658|-2.228|2/7|LONG_HISTORY_REJECTED|
|BTC/REGIME-RANGE|0.069|1.852|-1.361|-5.523|3/7|LONG_HISTORY_REJECTED|
|BTC/FUNDING-LAST|0.509|-0.485|-2.612|-8.462|3/7|LONG_HISTORY_REJECTED|
|BTC/FUNDING-MEAN24|0.376|-2.211|-2.495|-1.581|3/7|LONG_HISTORY_REJECTED|
|ETH/ATR|0.523|-0.526|-5.412|-10.757|2/7|LONG_HISTORY_REJECTED|
|ETH/EMA|1.010|7.635|-4.937|-17.233|4/7|LONG_HISTORY_REJECTED|
|ETH/TSM|0.106|0.740|-6.061|-17.827|3/7|LONG_HISTORY_REJECTED|
|ETH/ROLLING_Z|0.232|-0.258|-7.150|-16.943|1/7|LONG_HISTORY_REJECTED|
|ETH/BOLLINGER|0.232|-0.258|-7.150|-16.943|1/7|LONG_HISTORY_REJECTED|
|ETH/REGIME-RANDOM|0.969|-1.106|-2.834|-6.663|2/7|LONG_HISTORY_REJECTED|
|ETH/FUNDING-LAST|0.411|4.998|3.062|0.602|4/7|INCONCLUSIVE|
|EWJ/ATR|0.095|-1.047|-11.440|-21.053|0/8|LONG_HISTORY_REJECTED|
|EWJ/DONCHIAN_COMPRESSION|0.147|-2.427|-5.654|-9.726|0/8|LONG_HISTORY_REJECTED|
|EWJ/DONCHIAN_EXPANSION|0.052|-1.242|-9.491|-18.344|0/8|LONG_HISTORY_REJECTED|
|EWJ/DONCHIAN_COMPRESSION_EXPANSION|0.147|-1.521|-3.958|-6.945|0/8|LONG_HISTORY_REJECTED|
|EWJ/SIMPLE_MR|0.116|-1.493|-12.496|-24.094|0/8|LONG_HISTORY_REJECTED|
|EWJ/FUNDING-MEAN24|0.126|-1.899|-6.624|-14.317|2/8|LONG_HISTORY_REJECTED|
|EWY/DONCHIAN|2.307|-7.115|-17.432|-27.447|1/8|LONG_HISTORY_REJECTED|
|EWY/ATR|2.856|3.040|-5.241|-17.126|5/8|LONG_HISTORY_REJECTED|
|EWY/DONCHIAN_EXPANSION|1.606|-1.446|-8.889|-16.607|3/8|LONG_HISTORY_REJECTED|
|EWY/DONCHIAN_COMPRESSION_EXPANSION|0.028|-0.956|-3.485|-7.727|2/8|LONG_HISTORY_REJECTED|
|EWY/EMA|0.730|-2.466|-22.232|-49.591|1/8|LONG_HISTORY_REJECTED|
|EWY/TSM|0.949|1.571|-9.256|-24.625|3/8|LONG_HISTORY_REJECTED|
|EWY/RISK_MOMENTUM|1.180|-0.357|-7.147|-16.436|2/8|LONG_HISTORY_REJECTED|
|EWY/REGIME-TREND|1.072|1.288|-2.798|-8.592|4/8|LONG_HISTORY_REJECTED|
|EWY/REGIME-RANGE|0.723|-3.188|-8.555|-11.807|2/8|LONG_HISTORY_REJECTED|
|EWY/REGIME-COST|1.654|-6.599|-16.567|-25.436|1/8|LONG_HISTORY_REJECTED|
|EWY/REGIME-RANDOM|2.851|-1.377|-4.869|-8.176|2/8|LONG_HISTORY_REJECTED|
|EWY/FUNDING-LAST|3.406|-5.050|-10.152|-11.522|4/8|LONG_HISTORY_REJECTED|
|EWY/FUNDING-MEAN24|1.346|-0.466|-4.086|-6.595|3/8|LONG_HISTORY_REJECTED|
|GOLD/ROLLING_Z|0.039|-0.080|-12.074|-25.668|0/8|LONG_HISTORY_REJECTED|
|GOLD/BOLLINGER|0.039|-0.080|-12.074|-25.668|0/8|LONG_HISTORY_REJECTED|
|HYPE/FUNDING-MEAN24|1.182|-16.995|-16.935|-16.966|4/8|LONG_HISTORY_REJECTED|
|USDJPY/FUNDING-LAST|0.008|0.160|-2.251|-5.910|1/8|LONG_HISTORY_REJECTED|

状态仅对应当前规则、外部窗口、成本与线性估值假设。负值不是传统量化普遍无效，正值不证明收益可持续，更不证明PAR/PAS存在利润。已见数据不能重新称为真正样本外。

## 四、时间稳定性、方向基准与失败来源

nonoverlap-amendment.json在未读取本轮新收益、主重放仍进行时单独记录必要诊断：每个候选另测PAR原筛选窗口开始之前的外部历史，截止时间直接取原CSV start_ms，不选择有利日期。保留原主计数与原门槛，不把这一诊断重新叫独立前瞻；所有37项及其简单/匹配方向对照均同规则base重放。

60/20/20按时钟顺序划分，验证与末段各隔离12h；每个分区独立冷启动持仓，信号可用此前历史做warm-up。每自然月也独立重放base，comparison.csv保留全部尝试。主表的正月份来自**全段连续账本**每日PnL按UTC月聚合，避免每月人为平仓/重开改变主策略。独立月实验之和不应等于全段收益；月初/末不完整月份保留并明确可查时间跨度。

基准包括固定20%初始资本多头、固定空头、空仓；每12h固定重平衡，不把固定方向误称为固定数量买入持有。另提供每项候选的MATCHED-LONG/SHORT，完全保留其触发时刻、退出与仓位绝对值，只改变方向。这些是因子归因对照，不是新增可交易策略；原策略风险调整仓位可能远小于固定20%，必须同时看风险/资本占用与匹配对照。

|资产/规则|筛选窗前净%|验证净%|末段净%|固定多净%|固定空净%|超额均值95%区间下界（多/空，bps/日）|
|---|---:|---:|---:|---:|---:|---:|
|BRENT/ROLLING_Z|-14.287|-2.335|-0.116|6.914|-7.332|-18.203/-11.492|
|BRENT/BOLLINGER|-14.287|-2.335|-0.116|6.914|-7.332|-18.203/-11.492|
|BTC/DONCHIAN_EXPANSION|-5.821|0.139|1.935|1.350|-1.549|-11.000/-8.708|
|BTC/REGIME-TREND|-1.833|-0.055|0.605|1.350|-1.549|-8.309/-6.277|
|BTC/REGIME-RANGE|-1.519|-0.197|2.001|1.350|-1.549|-8.627/-6.559|
|BTC/FUNDING-LAST|-5.032|-5.532|1.188|1.350|-1.549|-15.757/-4.337|
|BTC/FUNDING-MEAN24|-2.824|-3.283|-1.292|1.350|-1.549|-15.236/-3.996|
|ETH/ATR|-4.246|2.145|-2.369|0.993|-1.236|-11.285/-12.011|
|ETH/EMA|-5.172|-0.080|-1.250|0.993|-1.236|-11.991/-12.647|
|ETH/TSM|-3.790|0.702|0.571|0.993|-1.236|-11.688/-13.356|
|ETH/ROLLING_Z|-6.477|-2.073|-3.163|0.993|-1.236|-17.169/-12.206|
|ETH/BOLLINGER|-6.477|-2.073|-3.163|0.993|-1.236|-17.169/-12.206|
|ETH/REGIME-RANDOM|-2.644|-0.520|-1.209|0.993|-1.236|-12.024/-8.648|
|ETH/FUNDING-LAST|1.233|1.014|0.822|0.993|-1.236|-12.220/-4.099|
|EWJ/ATR|-10.952|-2.889|-1.402|2.554|-2.804|-10.611/-7.692|
|EWJ/DONCHIAN_COMPRESSION|-5.267|-1.238|-1.326|2.554|-2.804|-6.890/-4.314|
|EWJ/DONCHIAN_EXPANSION|-8.869|-2.266|-2.821|2.554|-2.804|-9.359/-6.696|
|EWJ/DONCHIAN_COMPRESSION_EXPANSION|-3.571|-0.648|-0.926|2.554|-2.804|-6.062/-3.518|
|EWJ/SIMPLE_MR|-10.892|-1.075|-2.443|2.554|-2.804|-10.901/-8.684|
|EWJ/FUNDING-MEAN24|-5.946|-1.112|-0.735|2.554|-2.804|-8.971/-6.478|
|EWY/DONCHIAN|-17.850|-0.382|-0.679|4.008|-4.482|-21.337/-15.069|
|EWY/ATR|-3.620|1.356|-1.360|4.008|-4.482|-14.860/-9.285|
|EWY/DONCHIAN_EXPANSION|-8.908|3.542|-1.199|4.008|-4.482|-15.286/-12.104|
|EWY/DONCHIAN_COMPRESSION_EXPANSION|-2.597|-0.793|-0.085|4.008|-4.482|-11.888/-8.935|
|EWY/EMA|-22.595|-2.467|-2.251|4.008|-4.482|-23.048/-18.936|
|EWY/TSM|-7.504|-4.386|-0.599|4.008|-4.482|-16.372/-11.805|
|EWY/RISK_MOMENTUM|-6.526|-4.403|0.301|4.008|-4.482|-14.478/-10.125|
|EWY/REGIME-TREND|-3.501|-0.923|1.186|4.008|-4.482|-12.335/-7.939|
|EWY/REGIME-RANGE|-8.702|-1.431|-0.344|4.008|-4.482|-15.610/-10.158|
|EWY/REGIME-COST|-17.939|-0.255|0.296|4.008|-4.482|-21.041/-14.547|
|EWY/REGIME-RANDOM|-6.030|1.149|0.379|4.008|-4.482|-13.690/-9.173|
|EWY/FUNDING-LAST|-14.431|-6.672|2.595|4.008|-4.482|-21.102/-11.137|
|EWY/FUNDING-MEAN24|-8.154|-5.414|3.159|4.008|-4.482|-17.639/-9.153|
|GOLD/ROLLING_Z|-11.854|-3.646|-0.737|-2.367|2.121|-8.667/-10.276|
|GOLD/BOLLINGER|-11.854|-3.646|-0.737|-2.367|2.121|-8.667/-10.276|
|HYPE/FUNDING-MEAN24|-19.995|-8.313|1.229|15.524|-15.946|-40.573/-4.143|
|USDJPY/FUNDING-LAST|-2.347|-0.356|0.270|0.340|-0.477|-2.215/-2.540|

daily-pnl与matrix的daily_excess使用同日配对固定资本PnL，循环移动5日块bootstrap2000次、种子20261018。95%区间为逐项探索性区间，未做37项家族多重校正，不能当显著发现证明；共同资产、等价规则和长期同向敞口显著降低独立信息量。bootstrap也不恢复未经历的市场状态。全样本扣除选择偏差的可交易显著性仍未知。

长期全段正值的下一步障碍逐项如下（不是收益排名）：

- ETH/FUNDING-LAST：正月份不足60%、未可靠超越固定多头和空头两个基准

  价格毛收益4.998%，手续费1.253%，价差0.835%、滑点0.557%，Funding代理收入0.708%，base净情景3.062%，压力净情景0.602%，最大回撤6.116%。正收益不是纯Funding现金流，也不是已证明的独立方向预测能力。可保留为低优先级冻结对照，但本轮不足以优先投入PAS实盘或宣称具有稳定优势。

state-attribution按入场前25期效率>0.35分趋势/震荡，保留每组gross/fees/funding/net；仅归因，不把结果较好状态再新增为交易过滤。部分减仓段不等于独立交易；Funding频繁再平衡信号也不等于数百个独立下注。

失败需要区分：毛负代表价格信号在该市场/窗口本身不佳；毛正净负由费用、价差/滑点和Funding共同影响；长期正但劣于固定方向可能只是资产趋势敞口；开发好/验证或末段差说明不稳定；零信号或Funding缺口则是数据/触发不足，不用零收益充当失败或成功证据。

### 原170项的长期负收益对照

long-universe-170逐项保留原状态和长期全段base。其余133项只做预先指定的长期全段基础对照，没有因它们在长期偶然变好而扩展调参、替代原37项或将其升级为发现。

|原筛选组|长期base负|长期base正|无交易/不完整|
|---|---:|---:|---:|
|原37正|36|1|0|
|原133非正/无信号|127|6|0|

原正组和原负组并非随机分配，不能用这个表计算因果选择优势；不同资产/时期与规则依赖仍需控制。

## 五、执行成本及结论边界

native base每边4.5bps、HIP3每边9bps；base完整价差6bps、每边额外滑点2bps、1h延迟。fee_only保留费率且摩擦为0；stress费率倍增、价差18bps、每边滑点4bps、2h延迟。费用按真实情景成交名义金额逐笔计，不从资本收益扣一次固定往返bps。

spread_cost_attributed与slippage_cost_attributed分开报告：在固定每笔价差/滑点bps下，分别按参考成交名义金额×半价差和×额外滑点归因；两者之和与原冻结账本摩擦一致，验证器逐笔独立重算。

[官方费用](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)与[Funding公式](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding)为机制依据。历史用户折扣、xyz fee-scale/growth参数、合约点值变更和可成交深度未重建，所以仍是情景。报告费率使用已返回的小时FundingRate，不再次乘运营方Funding multiplier；Oracle代理不是实际结算金额。

小时OHLC不提供真实买卖边、订单排队、部分成交、盘口冲击与盘中强平路径；低/零成交量bar尤其不能视为深度可用。归档回撤是小时线性估值账本的回撤，不是完整可执行账户回撤。所有真实net_return/executable_net_return保持null。

外部自身测试、外部信号迁移PAR/PAS、真正前瞻验证是三个不同问题。本轮仅完成第一项；第二项仍参见原external-transfer的独立证据，第三项对37项全部INSUFFICIENT_DATA。PAR现阶段继续用于开发；未来PAS观察须使用其自身真实可用时间、费率、报价和深度，不能搬用这些历史收益。

若有探索性门槛通过项，只能作为下一轮冻结名单观察，不启用；若没有，则现阶段没有应凭本轮收益优先投入PAS的候选。已见历史即使在代码冻结后下载仍是Retrospective；数据接收/可得时间在raw保留，但历史回测使用闭合bar即已知、下一小时开盘的假设。

## 六、数据、版本与复现

共1892个完整实验账本；每资产单独压缩存储全部信号、成交、结算代理、净值与拒绝原因。comparison.csv/summary.json包括全部尝试；matrix-37提供主结论与每项归因；输入摘要与代码摘要见manifest/hash。

```powershell
$env:PYTHONPATH = "$PWD/src"
# 使用已归档输入，完全离线，不重新抓取或筛选
.venv/Scripts/python.exe scripts/run_long_history.py
.venv/Scripts/python.exe scripts/run_long_nonoverlap.py
.venv/Scripts/python.exe scripts/analyze_long_history.py
.venv/Scripts/python.exe scripts/report_long_history.py
.venv/Scripts/python.exe scripts/validate_long_history.py
```

重算请先复制当前目录或使用独立checkout，以保留原结果；将副本中的nonoverlap-summary.json移至备份后再运行，因为该诊断默认拒绝覆盖已有汇总。long_history_protocol只允许创建新协议目录；capture_long_history拒绝覆盖已有抓取，retry仅对相同失败请求慢速有界重试。没有新指标、参数搜索、LLM、VPS修改、生产风控/交易开关更改或订单。
