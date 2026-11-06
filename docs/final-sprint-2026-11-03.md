# Final Sprint Readiness Audit — 2026-11-03

审计对象：`BouillieAnonymous/Perpetual-Alpha-2026`。现场窗口：2026-11-03 **10:31–10:34 UTC**；数值为不同只读快照，不是原子跨系统快照。英国当地时间目前等于 UTC。

**结论：最低算法成交条件已有直接证据；持续运行与最终交付尚未就绪。** PAS 交易器进程存活，但策略处于硬暂停且授权过期，仍持有 ETH 空仓。不要为了资格重复制造成交。优先处理带仓暂停的操作员决策、交付封版与证据保存，不建议截止前更换策略或扩大风险预算。

本轮仅执行 GitHub 查询、严格校验已知主机密钥的 SSH、Docker 状态/有界日志分类、SQLite `mode=ro` / query-only 查询和交易所未签名 `/info` 查询。未部署、重启、恢复交易、改授权、改参数或发送签名操作。只新增本报告；保留原工作区已有改动。未读取或输出 API 密钥、钱包私钥、SMTP 凭据。一次整份策略配置输出方案被自动审批拦截，改为服务端解析后仅输出明确列举的非秘密策略字段，通过审批后完成检查。

## 1. 状态总表

`VERIFIED` 表示在本轮指定证据范围内核实，**不等于健康或获准操作**；`UNKNOWN` 表示缺少直接证据；`BLOCKED` 表示目标存在已知阻断。

| 检查项 | 状态 | 结果与边界 |
|---|---|---|
| GitHub main | VERIFIED | `0c579d77282e7adffc8bd55b2b08e5f6cabc702a`，最新为 #35 合并提交 |
| 本地工作区 | VERIFIED | 分支 `codex/reference-basis-research`，HEAD `13888e7612d9f1d7251a1e699165c0cd77690bd3`；大量既有未提交研究与文档，不能直接作为最终发布包 |
| VPS 可访问性 | VERIFIED | SSH 成功；主机 uptime 26 天；根盘 19% 使用、约 31G 可用；可用内存约 2.9GB，无 swap；仅现场资源快照 |
| VPS Git commit | UNKNOWN | `/opt/perpetual-alpha` 不是 Git 仓库；不能用该目录推导实际源码 SHA |
| PAS Trader / Monitor 镜像 | VERIFIED | 均为 `perpetual-alpha-pas-trading:1b9bc79`，实际 image ID 见下；无 revision label，源码来源只能部分核实 |
| Trader 实际状态 | BLOCKED | Docker healthy，但账本 `paused=true`，`market_wait=null`；持仓没有随暂停平掉 |
| 交易授权 | BLOCKED | Monitor 明确报告批准过期/时间范围不合规则；当前不能自动续期、不能恢复交易 |
| PAS Collector | VERIFIED | 初次 unhealthy，之后自行恢复 healthy；近期 heartbeat 与十资产数据持续更新；历史 health 失败仍需调查 |
| Research Pipeline | UNKNOWN | 无 Research 常驻容器；`/opt` 可访问范围未发现 `*research*.sqlite3`；不排除其他命名、路径或定时任务，未证明完整派生管线运行 |
| Monitor | VERIFIED | heartbeat 新鲜；明确报告 paused 和 renewal blocked；782 条 outbox 均标记 delivered，0 待投递 |
| 邮件实际收件 / 站外监控 | UNKNOWN | outbox delivered 不能证明用户收件，也不能证明主机故障时站外告警有效 |
| PAS 账户 / 挂单 / 成交 | VERIFIED | 未签名交易所查询与本地账本对账见第 4 节；7 笔真实成交，0 挂单，ETH -0.0109 |
| 实际净 PnL | VERIFIED | 当前快照 +1.256954 USDP，含浮盈、费用与资金费；非最终结算或清仓后收益 |
| PAS 数据范围 / 粗覆盖 | VERIFIED | 10-19 13:43 起至 11-03 10:32 左右，ETH 分钟桶覆盖约 99.823%；不代表每桶可执行或全部字段完整 |
| 干净样本外区间 | UNKNOWN | 已用研究段不能重新称为样本外；10-30 后是否被其他成员看过/调参未证明 |
| 至少一次算法成交 | VERIFIED | 10-20 00:00 正常窗口决策、持久 intent、交易所 fill 可串联；最终资格裁定仍属主办方 |
| 最终技术报告 / 代码提交 | UNKNOWN | 未找到可确认的最终 2–4 页报告、最终封版及提交回执 |
| 整体截止准备 | BLOCKED | 缺最终交付证据与带仓暂停/到期后的操作方案 |

## 2. 官方截止与资格口径

本轮重新读取[官方时间表](https://perpetual-alpha26.github.io/schedule)：计分交易期为 **2026-10-19 12:00 UTC 至 2026-11-06 12:00 UTC**。审计开始至截止约 **73 小时 29 分钟**。11 月 6 日提交 2–4 页技术报告及代码；页面没有单列报告的具体小时或时区，内部应保守按 **11-06 12:00 UTC 前完成**，并确认提交入口及主办方书面要求。11 月 6–9 日评审。

[官方规则](https://perpetual-alpha26.github.io/about)要求计分阶段至少一次实际成交，入场须由算法产生，禁止完全手工交易、刷量和自成交。当前事实支持最低成交条件；未核实报名身份、白名单映射、线下参会等全部资格条件。不要把它扩写为“已通过最终资格审核”。

当前官网按期末 **mark prices** 计净回报、按每日 12:00 UTC 权益收益计算波动，并评审技术报告；旧本地文档提及 oracle 期末估值，与当前官网不一致，最终交付须修正口径。

## 3. 版本、PR 与实际部署差异

### 3.1 可核实版本

| 对象 | 实际证据 | 差异判断 |
|---|---|---|
| GitHub main | `0c579d77282e7adffc8bd55b2b08e5f6cabc702a` | 当前远端基线 |
| 本地 HEAD | `13888e7612d9f1d7251a1e699165c0cd77690bd3` | 旧研究分支，不能用本地 README 代表 main 能力 |
| Trader / Monitor | tag `1b9bc79`；image `sha256:001918c5d9636da033b0625fb5d8a90f39f05d63cb26c6e9988aff336819ab3c` | tag 对应 #35 head；GitHub compare 该完整 head → main 为 ahead 1、files=[]，是合并提交差异，无源文件差异；**镜像字节与该源码对应关系尚缺构建证明** |
| PAS Collector | tag `b2e0d19`；image `sha256:4cf58cad26c244610b1e97efe44c9ef1eefe20ac4749676c5738e7ccdc97d0a5` | 独立旧镜像；未核实其完整源码 SHA 和与 main 的逐文件差异 |
| PAR Runner | `perpetual-alpha-funding:43dad96`，healthy | 与 PAS 独立，不作为 PAS 成交证据 |
| 旧 Collector / Paper | `2026-10-08` unhealthy / `11f9835` healthy | 非 PAS 主链；停止它们也需要后续明确操作范围 |

Trader、Monitor 均自 `2026-10-23T12:40:19Z` 左右启动；PAS Collector 自 `2026-10-19T13:56:45Z` 启动。三者 RestartCount 均 0，不能据此推断期间业务无故障。运行镜像没有提供 `org.opencontainers.image.revision` 标签。

### 3.2 关键已合并 PR

以下为本轮 GitHub 查询状态；PR 描述中的测试是历史声明，本轮未重跑或将其冒充当前 CI。

| PR | 内容 | 截止前意义 |
|---|---|---|
| [#18](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/18) | PAR/PAS 隔离 | 保留隔离边界 |
| [#22](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/22)、[#23](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/23) | PAS 十市场采集、研究管线 | 代码存在，派生服务仍需直接验收 |
| [#24](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/24)、[#26](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/26) | 限权只读 CSV 导出、Python 3.14 独立交付 | 最终数据交付可复用；当前导出端到端未测试 |
| [#25](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/25) | 集成独立 PAS Funding-LAST | 不应再合并旧 #21 来重复集成 |
| [#28](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/28) | collateral snapshot 竞态重采 | 不放宽保证金/所有权检查 |
| [#30](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/30)、[#32](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/32) | 下单前 spread / network wait | 仅安全阶段恢复，不应将所有 502 视为可自动解锁 |
| [#33](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/33)、[#34](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/34) | 有条件授权续期、邮件格式 | 已过期或暂停不会自动恢复 |
| [#35](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/35) | stale-book wait、告警去重 | main 最新；现场已有 tag，但仍发生硬暂停 |

### 3.3 未合并 PR

本轮查询得到 **9 个 open PR**：[#13](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/13)、[#15](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/15)、[#16](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/16)、[#17](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/17)、[#19](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/19)、[#20](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/20)、[#21](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/21)、[#27](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/27)、[#29](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/29)。

- #21：双环境 Funding 原始分支，head `99736bf1a9685b7d5846c64d0decba583cdf7caf`；main 已通过 #25 集成。应核对剩余差异后归档/关闭，不能因为 open 就认为执行器尚未实现。
- #16：WebSocket 采集；截止前避免替换当前数据链。
- #13/#17：basis 研究；#15：量化候选；#19：新闻 Agent；#20：regime switching。均不建议纳入生产冲刺。
- #27：PAS 均值回归/短周期/十资产失败研究归档；#29：公开排行榜审计。可择要作为技术报告的研究证据，属于文档交付，不是上线前置。
- 当前本地 10-26、10-30 研究产物尚未提交，不能假定已在上述 PR 中。最终只挑选必要证据，避免把整个脏工作区打包。

## 4. PAS 真实运行、账户与净 PnL

### 4.1 真实运行状态

交易账本 `/opt/perpetual-alpha-pas-trading/state/pas/funding.sqlite3`：25 次 decision，7 个 intent，全部 terminal，0 unknown；`generation=5`、`pause_epoch=19`。最近 decision 为 **2026-11-02 12:00:01.875 UTC**。

现场 `last_tick_ms=1793701879000`（11-03 10:31:19 UTC），heartbeat 新鲜；但 `paused=true`，`market_wait=null`，reason 为 `Temporary public query metaAndAssetCtxs: 502`。最近 paused 事件 **11-03 08:50:54.604 UTC**；此前还有 `Stale/future exchange book`、`Wrong/incomplete book`。本轮只做日志分类，未完成故障触发路径的逐事件复盘，不能断言 #32/#35 有回归，也不能自动解除暂停。

24 小时最多 200 行 Docker 日志分类：Trader 出现 502、paused、stale、wrong/incomplete；未见该有界样本中的 Traceback。Monitor 的 200 行中均出现 paused，表明持续报告问题；不是 200 次邮件投递。历史 events 中 network-wait 12 次、spread-wait 168 次、stale-book-wait 5 次，均不是订单数量。

Monitor status 时间约 10:30:52 UTC，`ready_to_activate=false`，公共检查通过，独立 signer/公开授权检查为 true；阻断为 trader paused 与 `Approval expired, future-dated or exceeds 24h`。此处不能证明持有私钥，也没有检查或复制私钥。outbox 782 条均 delivered、0 pending、最大 attempts=1；仅证明程序记录的投递状态，实际收件 UNKNOWN。

Collector 初查 unhealthy；10:30:33、10:31:13 health exit=-1，随后连续三次 exit=0、FailingStreak=0，恢复 healthy，未进行任何重启。其 heartbeat `1793701908219`（10:31:48.219 UTC），error=null；说明容器健康波动与数据停采需要分开判断，失败原因尚未定位。

### 4.2 交易所直接核对

11-03 **10:32:04 UTC** 左右，通过固定 Hyperliquid Testnet `/info` 未签名查询 `userFillsByTime`、`userFunding`、`clearinghouseState(dex=pas)`、`spotClearinghouseState`、`openOrders(dex=pas)`。历史查询从计分开始，PAS 过滤后分别返回 7 fills、209 funding payments；未达到常见批量上限，但本轮未独立验证平台长期归档完整性。账户地址仅用于内存查询，不写入本报告。

| 指标 | 结果 |
|---|---:|
| USDP 总权益（Unified spot 口径） | 100001.256954 |
| 初始账本基线 | 100000.0 |
| 观察净权益变化 | **+1.256954 USDP（约 +0.001257%）** |
| 当前仓位 | pas:ETH **-0.0109**，entry 2273.4，1× Cross |
| 仓位名义 / marginUsed | 24.70921 USDP |
| 未实现 PnL | +0.070850 USDP |
| 真实可用余额（最新运行观察） | 100000.76277 USDP |
| 当前 PAS 挂单 | 0 |
| 已实现 closedPnl 合计 | +0.860170 USDP |
| 手续费支出 | **0.155201 USDP** |
| Funding 净收款 | **+0.481135 USDP**，209 条 |
| 扣费/资金费后已实现部分 | +1.186104 USDP |
| 本地 / 交易所 fills | **7 / 7** |

核算：`0.860170 - 0.155201 + 0.481135 + 0.070850 = 1.256954`，与 Unified 权益相对基线变化完全一致。不要再把 perp marginSummary.accountValue 加到 Unified spot total 上造成重复计算。交易所 position.cumFunding.allTime=-0.481135 与付款明细净收款正号相反，属于字段符号语义，不能再扣一次。此时没有发现权益残差；未独立审计全部历史非交易资金转移，不能把一次残差为零当作完整资金流水审计。

本轮按 `tid, oid, cloid, coin, px, sz, side, fee, feeToken, closedPnl, time` 逐字段核对，**全部一致**。成交有符号数量求和为 -0.0109，与账本 expected_quantity 和交易所仓位一致。运行观察 account_consistent=true、other_positions=[]、open_orders=[]；交易所 PAS 直接挂单查询亦为空。共享抵押品范围由现有公开运行观察核对，本轮未另行逐一请求全部 sibling DEX。

| 成交 UTC | 方向 / 数量 | 价格 | fee USDP | 可追溯 tid |
|---|---|---:|---:|---:|
| 10-20 00:00:16.808 | 开多 0.0103 | 2406.9 | 0.022311 | 380561109842515 |
| 10-29 00:00:57.668 | 平多 0.0103 | 2357.2 | 0.021851 | 496892746369569 |
| 10-29 00:01:12.220 | 开空 0.0105 | 2357.7 | 0.022280 | 1056496823847189 |
| 10-30 00:00:21.335 | 平空 0.0105 | 2250.6 | 0.021268 | 138858906682331 |
| 10-30 00:00:34.502 | 开多 0.0111 | 2250.6 | 0.022483 | 419662082875302 |
| 11-02 00:00:05.923 | 平多 0.0111 | 2272.9 | 0.022706 | 150973454673791 |
| 11-02 00:00:20.779 | 开空 0.0109 | 2273.4 | 0.022302 | 436986488822519 |

上述日期已由交易所毫秒时间自动复算核对。7 笔 fill 不等于 7 个独立策略样本，也不等于 25 个 decision 都成交。

### 4.3 ETH Funding-LAST 当前策略与限制

实际运行容器的非秘密配置字段经白名单读取：dex=pas、collateral=USDP、enabled=true；策略版本由真实 decision 确认为 `eth-funding-last/1`。仅使用决策前已接收的已结算 funding，正费率取空、负费率取多，0 取平；本地版本实现拒绝超过 2 小时的 funding。UTC 00/12 决策、窗口 15 分钟、poll=60s；不应为了补首单 bootstrap 或追赶错过窗口。

| 限制 | 已读实际值 |
|---|---|
| target fraction / 名义上限 / 单笔上限 | 5% / 25 / 25 USDP，名义上限实际约束规模 |
| 累计亏损 / 回撤停止 | 5 / 5 USDP |
| 最低可用保证金比例 | 90% |
| 最大 spread / 执行 slippage / oracle 偏离 | 15 / 5 / 50 bps |
| 最大单边费用 | 9 bps；当前观察正好 9 bps |
| book age / snapshot age | 10000 / 30000 ms |
| 实际 leverage | 1× Cross，交易所读回确认 |

暂停不会清仓，5 USDP 停止阈值也不是主机离线期间的托管止损保证。最新负 funding 信号与当前空仓相反；Monitor 的 reduce-only 买入预览只是预览，未执行。没有发现研究结果足以支持截止前换策略，当前小额正收益也不证明统计显著优势。

## 5. PAS 数据与样本外验证

原始库 `/opt/perpetual-alpha-pas-data/pas/public.sqlite3` 约 544MB；范围 **2026-10-19 13:43:12.948 UTC 至 2026-11-03 10:32 左右**，十资产均有近期记录。该库最早时间比计分开始晚约 1 小时 43 分钟，不可声称覆盖完整计分期。

本轮约 456,8xx 条观察，动态增长。10:31 左右固定统计：ready book 214221、empty-or-one-sided book 7、context 214230、metadata-context 21423、request-error 40、settled-funding 6910。quality 标签是入库分类，不是独立完整质量验收。

按 ETH book 所覆盖的首末自然分钟桶统计，21372 / 21410 = **99.8225%**；其余资产分钟数相近（查询过程中仍写入，尾部最多相差一轮）。该分母不含计分期开始前的缺失部分，也未剔除空盘口、过期报价或验证 oracle/funding 对齐。ETH 有 9 个相邻样本间隔超过 120 秒，最大 **1319.993 秒，约 22 分钟**；不能把原始分钟桶覆盖率当成可执行回测覆盖率。

历史研究已确认：10-30 09:00:30.344 UTC 的 funding 结算前，缺 90 秒内已接收 oracle；不等于缺结算费率。所有需要该估值的有仓位净收益应为 unknown，不能补未来报价或当作零资金费。参见[10-30 验证报告](research/candidate-validation-2026-10-30/report.zh-CN.md)。本轮没有全量重跑该质量检测。

| 历史区间 | 可用性与研究边界 |
|---|---|
| 10-19 13:43—10-26 | 已用于多轮筛选与拟合，是开发数据 |
| 10-27 00:00—10-30 13:00 | 已用于固定候选新增段验证，现已是已见数据；85 小时，存在 oracle 估值缺口 |
| 10-27 00:00—10-30 09:00 | 81 小时完整账本敏感性分析；截点是发现质量问题后追加，不能冒充事前独立窗口 |
| 10-30 13:00—11-03 本轮 | 原始数据可能支持下一轮验证；各成员是否已用于研究 UNKNOWN，不宣称干净 OOS |
| 本轮后—11-06 12:00 | 可在先冻结方案后用作前瞻观察；最长约三天，样本量很弱，不支持复杂策略显著性声明 |

10-30 已有结果不支持替换基线：BRENT TSM 基础/压力 -2.1801/-2.8365；ETH ROLLING_Z -3.8215/-4.4810；GOLD MEAN24 +0.1067/-0.1129 USDP，且只有一个持仓段。研究使用 500 资本、100 名义、模拟执行；不能套到线上 25 名义实盘。Research Pipeline 的调度、最新派生水位、生成质量报告与可复现导出尚未直接证明，列为独立任务。

## 6. 截止前任务（估算为人时，不含等待授权）

以下是建议任务，**本轮未执行任何修复或运维动作**。P0 不是自动授权。负责人角色为建议分工。

| 优先级 / 任务 | 人时 | 建议负责人 / 最迟节点 | 验收标准 |
|---|---:|---|---|
| P0：带仓暂停与授权到期决策 | 1–2 | 操作员 + 执行维护，11-03 | 书面选择保持暂停监视、按原规则续准并恢复、或单独授权 reduce-only 退出；重读持仓/挂单/费用/授权；明确不得把暂停当作平仓 |
| P0：资格与真实账本证据封存 | 1–2 | 执行维护，11-04 | 保存去秘密的 decision→intent→fill 对应、7 笔精确对账、费用/funding/权益公式、UTC 与哈希；团队账户/白名单关联由操作员确认 |
| P0：最终 2–4 页报告与提交渠道 | 4–6 | 报告负责人 + 团队，11-05 18:00 | 完成方法、实际运行、失败研究、成本、风险、局限及复现；页数合格；确认主办方入口与具体截止；取得提交回执 |
| P0：最终代码包封版及秘密检查 | 2–4 | 代码维护，11-05 18:00 | 干净独立 checkout 固定 SHA；记录镜像 ID 与来源差距；仅收录所需研究产物；离线复现/相关现有测试通过；tracked 与打包文件经不输出内容的秘密检查；完成回执 |
| P0：比赛截止运行处置 | 1–2 | 操作员，11-05 决策、11-06 执行 | 明确 12:00 后不再新入场的执行责任、到期前后订单/仓位处理和快照时间；任何暂停、停止或平仓另行授权，不能假设当前过期授权永久阻止后续交易 |
| P1：调查硬暂停触发路径 | 2–4 | 执行维护，11-04 | 基于持久事件重建 Wrong/incomplete→stale→502 顺序、当时是否在安全恢复阶段；解释 fail-closed 是否符合设计；如需补丁先隔离验证再独立授权部署 |
| P1：Collector health 波动与数据质量 | 1–3 | 数据维护，11-04 | 解释 exit=-1 原因，核对采集是否断档；输出逐资产缺口、空盘口、oracle/funding 新鲜度与有效覆盖率，至少连续 2 小时观测稳定；不通过放宽 freshness 掩盖问题 |
| P1：Monitor 与站外告警验收 | 1–2 | 操作员 + 运维，11-04 | 人工确认现有告警到达收件箱；读到 paused/expiry/unknown 的真实事件；确认主机/Monitor 失联监测；发送测试或改配置需另行明确范围 |
| P1：Research Pipeline 水位与复现 | 2–3 | 研究/数据维护，11-04 | 找到真实调度入口、派生库及最新水位，或明确标注按需离线模式；固定只读输入指纹并复现现有结果，保留全部 unknown |
| P1：一致备份与隔离恢复交付 | 1–2 | 运维，11-05 | 交易/监控/采集库一致快照、有哈希；隔离恢复可读且默认暂停；不使用活库普通复制替代 WAL 一致备份；备份动作另约执行范围 |
| P2：样本使用登记与有限前瞻观察 | 1–2 | 研究负责人，11-04 | 登记已看区间，冻结唯一候选/基准/成本/质量规则；不重新搜索盈利参数，不以三天少量样本宣布优势 |
| P2：PR 与文档整理 | 1–2 | 维护者，11-05 | 说明 #21 被集成关系，择要归档 #27/#29；更新首页实际状态与 mark 估值口径；不顺带合并生产改造 |
| P2：评审 walkthrough | 1–2 | 团队，11-05 | 演示信号、风控、幂等、恢复、真实成交及研究失败证据；不需现场签名交易 |

总计约 **19–36 人时**；可按角色并行，但授权依赖不可跳过。优先保障 P0，不能为了凑齐所有 P2 延误提交。

## 7. 推荐保留、停止与明确授权边界

**保留：** 当前 ETH Funding-LAST 作为可说明、可对账的实际执行基线；25 USDP 名义限制及原风险阈值；独立 Collector 与 Monitor；所有失败研究和数据缺口；每日 12:00 UTC 权益证据。保留策略不等于现在获准恢复交易。

**停止新增工作方向：** 新闻/LLM/RL/regime 新架构、大范围参数搜索、WebSocket 替换、扩大预算、多资产生产上线、排行榜策略猜测。可以写入未来工作，不应成为本次交付前置。不执行任何服务停止操作；这里指研发投入取舍。

**后续必须由操作员明确授权：** 发布新的交易批准或 standing permission、resume、启动/重启/替换 Trader/Monitor/Collector、部署补丁、撤单/平仓、杠杆/策略/风险参数变更、截止后运行处置。不得重用本次只读审计授权。保持暂停仍有价格与资金费风险，应明确人员持续看护与失联应对；主机离线期间本地风控不能保证退出。

**交付授权：** 本轮没有 commit、push、合并 PR、发送邮件或向主办方提交。后续提交报告/代码、关闭或合并 PR、修改通知配置等属于下一轮明确范围。报告只携带公开源码标识、非秘密风险数值和成交证据，不附 `.env`、钱包文件、完整容器 inspect、SMTP 配置或数据库原始账户响应。

## 8. 最终交付准备与证据索引

| 交付项 | 现状 | 达标所需 |
|---|---|---|
| 交易截止 11-06 12:00 UTC | 时间 VERIFIED；运行处置 BLOCKED | 操作员签定截止方案；临近截止重查真实仓位/订单与权益 |
| 最低算法成交证明 | VERIFIED | 导出去秘密的完整链与账户归属确认；不用补交易 |
| 技术报告 | UNKNOWN | 可确认的 2–4 页最终版、口径统一、提交回执 |
| 代码提交 | UNKNOWN | 固定 SHA、必要依赖/说明、秘密扫描、复现证据与回执 |
| 数据交付 | 部分 VERIFIED | 原始范围与粗覆盖已有；最终快照、指纹、有效覆盖及派生管线证明未完成 |
| 净收益及成本 | 当前 VERIFIED | 截止时再生成同口径最终对账；区分 realized、unrealized、funding、fees |
| 安全/恢复 | UNKNOWN（完整性） | 本轮未执行安全扫描、权限全审计或恢复演练；必须完成打包秘密检查与隔离恢复证据，不声称安全审计全通过 |

证据来源：

1. GitHub connector 现场 main commit、全部 PR 搜索及 `1b9bc798ac8d6109d124a9e5ee4022b579148f1b...0c579d77282e7adffc8bd55b2b08e5f6cabc702a` compare；[main 固定提交](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/commit/0c579d77282e7adffc8bd55b2b08e5f6cabc702a)。无源文件差异不能替代镜像构建证明。
2. VPS SSH 现场 `docker ps/inspect/image inspect` 的限定字段、只读 SQLite、资源快照及有界日志分类；完整原始响应留在本次工具执行记录，未另行复制生产秘密或制作数据库备份。
3. 交易所公开信息接口：本轮直接查询与逐字段对账；成交 tid 表可供操作员复核。
4. [10-30 候选验证](research/candidate-validation-2026-10-30/report.zh-CN.md)、[样本使用审计](research/candidate-validation-2026-10-30/sample-use-audit.zh-CN.md)为本地既有、部分未跟踪研究证据，不冒充 main 已合并产物。
5. 10-19 旧部署验收只用于理解路径与历史，**未用其初始余额、WAITING ACTIVATION 或旧镜像替代本轮现场结果**。

本报告是时间限定的审计记录，运行状态和权益会变化。未验证项保留 UNKNOWN，任何后续生产动作另行授权。
