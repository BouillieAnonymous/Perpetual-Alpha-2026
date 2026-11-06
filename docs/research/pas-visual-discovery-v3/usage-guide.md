# V3 使用指南与数据字典

## 推荐人工顺序

1. **先看审计**：原始覆盖到10/30，共同窗口15,815分钟。比较A/B有效数、零收益与Oracle/深度/历史时钟。不要先按相关最高或收益最好选pair。
2. **单资产 / 最多四资产**：左侧按Crypto、Equity reference、Commodity reference、FX reference分组；选择最多四条共同基点=100的价格曲线。基点为2026-10-19 13:44 UTC，不随缩放重选，不直接叠不同单位美元价格。
3. **配对**：X解释、Y响应，Beta=Cov(Y,X)/Var(X)。交换方向会改变Beta，不改变对称相关。配对面板显示全前缀工程门槛；侧栏显示当前窗口配对n/coverage/缺失原因。全前缀通过不保证当前窗口有值。
4. **10×10矩阵**：row=X，column=Y；仅历史光标之前固定60/240/1440分钟。每格显示n/窗口，灰格MISSING；点击进入对应pair。移动光标后手动刷新矩阵，标题始终保留该矩阵的历史as-of时刻。它不是全选区拟合或45个策略。
5. **质量与成本**：同时看原始event/receipt年龄、quote可用/复用/静止标记和spread。费用/滑点逐市场默认为UNKNOWN；填完后点击“应用逐市场假设预算”。单腿round-trip=spread+2fee+2slip，仅冻结spread情景。双腿等名义和总gross归一化均独立标注，没有net字段。

所有时序图共享UTC范围、光标和缩放。点击画布固定/解锁光标，左右箭头移动一分钟；滑块可访问历史点。拖动缩放、双击恢复；UTC输入无效时拒绝变更。固定开发窗为[10/20,10/27)，末段只到本数据10/30，不伪造10/31–11/03。

## 数值定义

|字段|单位|定义/有效性|
|---|---|---|
|mid|各市场price_unit|最新可得且event/receipt TTL90s的盘口Mid；坏最新记录不回退|
|index|共同基点=100|100×Mid/Mid_base，不是跨缺口可交易累计收益|
|r|log-return bps|10000×log(Mid_t/Mid_t−1)，A新seq/event推进保留零，B额外changed top与30–90s|
|ret5/15/30/60|bps|完整连续w个有符号收益之和；缺一整窗缺失|
|rv5/15/30/60|bps|10000 sqrt(sum(r_raw²))，完整连续、不年化、不缩放部分覆盖|
|slope5/15/30/60|bps/min|w+1个log Mid回归UTC0…w的OLS斜率×10000，要求完整selected-mode收益|
|spread|bps|10000(ask−bid)/Mid，不是手续费或实测交易收益|
|age/receipt_age|秒|决策减原始event/receipt；过期所选book年龄保留用于质量展示|
|event_ms/available_ms|UTC Unix ms|原book事件时间 / max(requested,received,stored)；不是真实commit证明|
|quote_valid/same_event/unchanged_top|0/1|有效book / 同事件复用 / 新事件但顶层价不变；后者不等于无经济信息|
|oracle/oracle_receipt_age|price_unit/秒|独立context可得与TTL；上游生成时间UNKNOWN|
|funding_bps|bps/hour|当时已可得、2h TTL内最新settled rate；非预测Funding、非账户现金流|
|funding_event_ms/funding_available_ms|UTC ms|所选settlement事件 / 该版本receipt可得；首次可得另存component-audit|
|depth_traceable|0/1|同seq真实L2双边快照存在；不是连续队列、真实成交或执行保证|
|corr60/240/1440|无量纲|历史墙钟配对Pearson，>=90%有效配对，双方中心平方和>1e−18 raw-log²；不足无值|
|beta60/240/1440|Y/X|同窗Cov(Y,X)/Var(X)，非因果、非全段拟合|
|relative60|bps|Y60−X60，完整双方历史60m，未做hedge调整|
|lagbeta|Y/X|收益索引[i−1500,i−60)的1440分钟，至少1296配对，排除signal60；不是当前Beta240|
|residual60|bps|Y60−lagbeta×X60；无截距，非固定平稳价差或可交易pair净收益|
|z|历史SD|当前residual与[i−1440,i)先前residual的均值/样本SD比较，>=1296有效、SD>1e−6bps；排除当前|

配对图还受全前缀固定工程门槛约束（7天、90%A或B共同收益、>=30完整60窗/5UTC日、非零方差）。全量门槛见protocol。空白有明确warmup、coverage、small_variance或pair_insufficient_data原因。±1/±2只是Z参考线，不自动产生订单。

## 文件与时间

原始/normalizer/整prefix哈希见metadata；coverage.csv十行，每项完整数和真实分母；pair-eligibility.csv为45无序pair×A/B=90行，不是策略实验。矩阵是同一45个对称描述关系，点击方向只影响X/Y语义。

每个assets/*.js载入真实单市场公共数据和A/B历史特征；文件压缩只是性能封装，无外部或合成价格。数据全精度保留。main HTML约87KB，初始只加载BTC/ETH；矩阵才加载其余市场。最多缓存两组配对特征，不预计算45条Beta/残差策略。

元数据逐版本available时标和seq均保留，侧栏szDecimals只取该时刻已知版本；lot不能跨市场套用。官方2026-11-05参考/时段规则与历史cache证据分开。USDEUR是USD per EUR；USDJPY是JPY per USD，不能靠ticker猜倒数或假定cashflow美元估值。

## 停止条件

来源/指纹/时间对齐不符即阻断；数据不足保留名称，不外采/合成补齐。已见历史不作为干净样本外。H08–H13只登记，未来标签尚未计算；矩阵、窗口选择和成本输入都是探索操作，不形成新的验证结果。当前只优先H09的新数据协议规划。
