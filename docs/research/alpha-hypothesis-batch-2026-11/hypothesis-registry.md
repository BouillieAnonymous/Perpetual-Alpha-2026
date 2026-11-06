# Batch Alpha prior-evidence registry

研究日期：2026-11-04。研究工作区为本目录；仅新建离线文件，不覆盖根目录已有未提交研究、不更改任何生产组件。原 checkout HEAD 为 13888e7612d9f1d7251a1e699165c0cd77690bd3，并非当前 main；不能把旧 checkout 缺少模块误报为生产缺少模块。PR 当前状态与完整说明见 prior-pr-snapshots.json，PR head 固定如下。所有已见历史均为 retrospective。

|先验证据|固定 head / 实质结果|对本轮的约束|
|---|---|---|
|[PR36](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/36)|7322df45a1d057a304794c6739a204a94cd12e33；Stage1/2A 检验 BTC RV15 → ETH RV30，控制 ETH RV15；A 半小时验证 MSE 改善2.03%，末段恶化0.63%；B 非重叠不足。后续 EDA 检验一分钟 Q05/Q95 冲击，2181事件/179重叠簇|不把波动预测当方向 Alpha；不将 EDA 重叠事件当独立证据；H01/H02 不是首次观察这段历史|
|[PR27](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/27)|ccaa3ecf2d4d9d5160722caa0b14028cc42244f3；5分钟动量/反转、BTC1分钟→ETH1分钟、盘口不平衡均成本后负；新增段十资产40个5/10分钟基本组合全负，成本过滤均不交易|这些原规则停止重跑；改变名称、方向或成本不产生新 Alpha|
|[PR15](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/15)|6832145603b2bbd305fbbd91377d16538ecb5951；24h回看/12h持有；37个短期正组合长期36拒绝、1不确定；28毛负、8成本转负；ETH Funding-LAST正但方向超额区间跨零|禁止重选传统规则/参数；Funding-LAST仅冻结对照，外部永续成本情景不能迁移PAS|
|[PR20](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/20)|31668b900e9142e716e59ee828c61ece8ce524ec；25h效率×RV24/历史168h中位数，680归因单元；选择器240映射全FLAT；三个固定过滤器滚动/末段均负|不重新训练状态选择器、不优化波动阈值；归因正不等于可成交过滤收益|
|[PR13](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/13)|13888e7612d9f1d7251a1e699165c0cd77690bd3；Basis5m配对17次，mid +8.83bps，touch −0.05bps，费用后−22.10bps；反向亦负|H06暂缓，不把回归概率当盈利；旧PAR不是PAS|
|[PR17](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/17)|0e100aaeae1d12e01a96a553408f3b7aa1a88a1e；BTC/ETH匹配7/11次，原方向净−26.17/−13.92bps，反向−37.71/−45.71；其余估值/样本不足|H06只整理证据，不扩搜|
|[PR25](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/25)|c0553ca9429a0785300adedbef828b3f1b9591eb；ETH Funding-LAST，UTC00/12，目标5%、目标/单单上限25 USDP，1x Cross；损失/DD5，余量90%，spread15bps，slippage5bps，fee上限9bps|这是风险/执行预算，不是账户实际历史费率证明；本轮不改变任何规则/预算/授权|

已额外阅读 weekly-synthesis-2026-10-26：12条分钟规则×3持有期的失败筛查，以及原17条传统算法重放；mom60已有20bps门槛/60m持有对照。这里也不能重新包装为新发现。

|假设|实质等价检查|本轮决定|
|---|---|---|
|H01 有符号15→30|不同于PR36的RV目标、PR27的1→1或5→5/10；未发现相同的控制ETH后固定方向OLS|执行一次；反向ETH→BTC仅诊断，不是第二个发现|
|H02 15m绝对冲击Q90|与PR36一分钟冲击EDA相近但非同实验；是H01尾部条件版本，信息与样本高度重叠|执行一次正/负冲击的去重描述与ETH控制残差；与H01合为一个证据家族|
|H03 BTC/ETH残差60m|不是Oracle-perp Basis；先验文档有pair候选，未发现同一动态past-only hedge/60m检验|执行一次；不宣称相关即平稳，不搜索持有期|
|H04 波动条件60m趋势|旧PR15/20为24h/12h，PR27为5/10m；weekly mom60基线已测，未发现相同RV60条件/60m检验|仅固定高/低状态的配对增量检查；不重新筛选传统策略，不把基线叫新Alpha|
|H05 已结算Funding→12h|Funding-LAST在PR15及PAS weekly已测，规则与成本结论均非新发现；本问题的actual-availability价格条件描述未发现充分独立决策支持|只描述、固定对照；不足60个非重叠12h决策/30日，不作盈利推断|
|H06 Oracle–Perp Basis|已有实质等价且负面成本证据|DUPLICATE_PRIOR_WORK；按用户要求暂缓，无新搜索|
|H07 OBI|PR27已测分钟采样前5档不平衡，成本前后负；weekly HYPE线索敏感性未过|DUPLICATE_PRIOR_WORK；暂缓。分钟数据不证实秒级Alpha，也不证伪全部秒级策略|

来源：连接器读取全部7个PR说明；本地读取Stage1审计/Stage2A协议报告、PR27短周期/全资产报告、PR13报告、PR15长期报告和PR20报告；PR15/20报告另以固定head从GitHub核对，PR25配置亦从固定head核对。source-evidence.json记录报告正文、Git blob和路径；input-hashes.json记录实际计算输入。未读取账户、私钥、钱包或生产账本。
