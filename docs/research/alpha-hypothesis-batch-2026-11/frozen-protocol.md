# Frozen protocol — batch 2026-11, v1

此文件在本轮候选关系/收益计算之前创建。freeze.json以UTC时刻和SHA256封存此协议、registry与源证据；之后不得改变数值规则。实现错误修复单独记录，不允许根据结果改参。不是此前无人看过历史的预注册，也不是部署授权。

## 数据、对齐与共同限制

主输入：PR36 Stage1 normalized-pas-btc-eth.jsonl.gz SHA256 c606ebb4b5a273f9385438fa3c3d47e64bc341ec5107aec714e4f0233d2b63db，causal-minute-panel.jsonl.gz SHA256 87bced947df20fdb6d8fe87e4655cbcfb943456478bd2147885ef7d1e01cf203。固定上限seq457619，2026-10-19 13:43至11-03 10:00 UTC。无网络下载或新行情。

补充输入：已有PAS公共完整前缀 .cache/alpha-week-2026-10-26/pas-prefix.jsonl.gz，SHA256 4baa71116daed70cbca25fa7179cf39f84ee555f7d1900079d87820efbf70a14，seq224063，截止10-26约20:24。只读取BTC/ETH、公共metadata、Funding；用于H05与覆盖期内深度审计，绝不当全15天深度。所有原始响应hash逐条核对。不因结果扩展输入。

as-of available=max(requested,received,stored)，只允许≤决策t。最新坏报价不得回退到旧好报价。book event/receipt均90秒TTL；seq与event须前进才形成分钟收益。A主口径保留新有效事件零收益；B敏感性另要求bid/ask改变且event跨度30–90秒。B的未来完整资格是事后标签选择，不是在线交易过滤。缺失不填零、不插值、不跨缺口。每个窗口要求连续完整分钟收益。单位为log return bps（乘10000），不标准化、不截尾。

固定切分沿PR36：[2026-10-20,10-27)开发、[10-27,10-31)验证、[10-31,11-03)末段，UTC00:00半开；特征/标签支持全在本split内。H03 hedge预热可来自split之前（单独标记），但残差信号/未来60m须在split内。H05补充源没有验证/末段，空行明确保留。初始不完整日及11-03尾段不进入模型。无随机拆分、全段拟合或在验证重拟合。每个配置、分段、失败都进results.csv。

非重叠不意味着IID；报告候选数、完整数、标签不重叠块、支持不交叠敏感性、不同UTC日期、可验证交易数。最多14个完整已见日期，不计算naive p值/置信区间或声称统计显著。三个核心家族为H01/H02、H03、H04，H05仅描述；共享BTC/ETH与行情机制带来依赖。完整披露A/B、锚点、方向、状态和对照，不择优。

## H01

X=BTC过去15m有符号收益，Z=ETH过去15m，Y=ETH未来30m。Baseline OLS截距+Z；Candidate截距+Z+X。开发段UTC :00/:30共同完整时点拟合一次；固定UTC90m锚点另做敏感性；分别A/B。反向交换BTC/ETH完全同法。主指标配对MSE skill=1−MSEc/MSEb；辅助MAE、方向命中（零标签不算正确、单列零数）、mean sign(pred)*Y，以及日配对MSE差。预测和误差逐点保存。训练最低60锚点/5日，每个评估30锚点/3日；不满足不拟合/不比较，不以分钟重叠样本补足。

## H02

在训练模型之前，先用开发段全部完整BTC15m特征（t−15在段内；不要求未来Y）的abs收益Q90，排序nearest-rank index=round(.9*(n−1))，一次冻结A阈值；B使用同一阈值。阈值不是独立样本分位置信结论。逐split按时间扫描，两资产15m历史特征完整且absBTC≥阈值时产生事件；接受后45m内不再接受任何方向，完全依据过去特征去重，即使未来标签缺失仍消耗冷却。45m确保15m历史+30m未来支持不交叠；未来结果不参与选事件。末端不能成熟的候选亦保留并标无效。

正/负分列ETH未来30m均值/Q05/Q50/Q95、延续/反转/零概率；控制ETH已实现15m收益：使用H01 A30开发拟合Baseline预测，保存Y−Baseline预测及sign(BTC)调整残差。B沿用同一控制作敏感性。至少每方向每评估30完整事件/3日才可判断；不足仅描述。不是H01之外的独立发现，不增加一个Alpha计数。

## H03

初始尺度固定历史60m、未来60m；UTC每60m锚点（标签不重叠），不搜索期限。beta_t=cov(BTC,ETH)/var(BTC)，使用[t−1500m,t−60m)内1440个历史分钟收益，至少1296个共同有效（90%），否则缺失；beta估计窗排除信号60m。无需该长窗完整连续，但显式报告有效数；信号/标签60m必须完整连续。无全段beta；负beta如实保留，不clip。

历史残差e=ETH60−beta*BTC60；未来残差f=ETH未来60−beta*BTC未来60，beta持有期固定。方向为−sign(e)，相对毛收益−sign(e)*f/(1+abs(beta))按两腿总绝对名义归一。只报告正/负/全部残差条件，不设最优z入场；回归斜率f~e只作描述。不把动态beta残差当固定可积累平稳价差。每分钟保存beta、残差、动态hedge-log-price=logETH−beta*logBTC，及beta变动；该动态level的变化含再估计项，不是持仓利润。锚点报告未来残差、收敛率、继续偏离概率、最差路径与Q05尾部；所有中间路径缺失则尾部unknown。

真实bid/ask诊断：t之后首条新book、≤90秒；退出t+60m之后首条新book、≤90秒，两资产分别选择，异步差显式保存。固定总gross目标25USDP，ETH权重1/(1+abs(beta))，BTC权重abs(beta)/(1+abs(beta))，BTC持仓符号=−ETH方向*sign(beta)。精度从补充metadata；每腿最小10为已有PAS研究约束（历史实际约束未证明）。入出顶层深度不足、报价event age>10秒（PR25执行门槛）、费用/资金费缺口、两腿原子成交无法保证均不删事件，标EXECUTION_UNKNOWN。已覆盖两腿最优档数量检查；不假造盘口外滑点或成交。

9bps/边是PAS配置fee预算上限，5bps/边为该配置滑点预算；可列touch、fee-only及fee+5bps滑点的显式条件诊断，费用按每腿各次名义计。不使用主网4.5bps，不从相关性推出平稳。实际账户费率、历史最小额、双腿legging、成交确认未知，可实现net始终留空；不把条件成本算式叫真实收益。至少30完整锚点/3日每评估才作方向判断；无需拟合交易模型。

## H04

不重跑旧24h/12h或5/10m规则。固定ETH历史60m有符号趋势sign(R60)，未来60m；UTC整点，开发段历史RV60中位数（同一nearest-rank规则）定义low≤median/high>median，先冻结阈值再看标签。仅A/B；不加优化幅度门槛。固定ALL、LOW_ONLY、HIGH_ONLY三条路径与固定多/空/空仓，在相同整点报告；不合状态的候选记0。比较候选相对ALL的配对毛方向收益与同状态多/空；报告每状态数量。成本后不能证明时不报告净正，状态非在线择优。训练日/评估支持门槛同H01，条件状态另需30完整锚点/3日。旧mom60 baseline已研究，不算新发现。

## H05

只从补充原始前缀提取已结算Funding，按(symbol,settlement time)去重保留首次实际available；发现同结算时间冲突则阻断。决策固定UTC00/12，只用event≤t、available≤t且年龄≤2h的最新结算费率；Funding-LAST=-sign(rate)，并列fixed long/short/flat。逐点保存未来12h有符号mid收益、Funding结算时标和首次可得时标；标签端点/全连续窗口缺失明确标记，不拿预测Funding替代。至少60个非重叠12h有效决策、30日才允许盈利研究；当前约7天显然不可能，因此只做描述，所有盈利/net/显著性字段为空，保留缺失的验证/末段行。价格方向归因不是Funding现金收入或现有生产交易回放。

## 状态与停止条件

每个假设仅一个总状态：H06/07 DUPLICATE_PRIOR_WORK；数据/数量门槛失败 INSUFFICIENT_DATA；主比较在验证/末段不一致或无正增量 NO_INCREMENTAL_EVIDENCE；只有可执行成本完整且净负才COST_REJECTED；价格证据可继续但执行未证 EXECUTION_UNKNOWN；PROMISING_EXPLORATORY仅指预测/价格，要求两评估均足量且同方向改善，H01还须MSE改善与方向命中不劣、90m敏感性足量且方向一致。若满足价格条件但执行阻断，总状态优先EXECUTION_UNKNOWN，预测层另列。H02不足不升级H01。

输入hash/来源/时间对齐不符立即停止该配置；OLS奇异停止该比较；数量不足不降低门槛；费用/深度/报价时效不能证明则停止净收益计算；负结果停止该规则，不自动反向或改参。不得改钱包、签名、Funding-LAST、Trader、Collector、Monitor、风控、仓位或任何部署。完成全部固定配置、复算验证、报告后停止。
