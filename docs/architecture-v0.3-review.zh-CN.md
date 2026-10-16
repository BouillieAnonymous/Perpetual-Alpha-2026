# Perpetual Alpha 2026 — Architecture v0.3 技术审查报告

> 归档说明（2026-10-16）：本报告保留审查时的事实、建议与操作记录。用户审阅后的决定及当前开发政策见[开发基线与分阶段实施计划 v0.3](development-baseline-v0.3.zh-CN.md)。文中“仓库外”“未修改仓库”等表述描述原审查交付时的状态；此次仅通过独立文档分支归档，不包含 PR0 代码实现或交易授权。

审查日期：2026-10-16，Europe/London。状态：供讨论；未开始代码实现、部署或交易。

## 1. 结论与审查基线

总体方向技术上可行，适合在已有代码上增量落实。保留独立 Agent/Core Python 进程与容器、HTTP Pull、多轮受控取数、Forecast-only、唯一控制链和未来跨 VPS 的方向。当前缺口主要是持久化运行系统、数据覆盖及执行恢复，不能靠将现有同步 Adapter 包一层 HTTP 解决。

建议先批准契约与两进程 Mock 闭环的开发范围；账户估值、真实风险政策、订单后端另设验收阶段。文档足以指导分阶段设计，但尚不足以直接作为无人值守交易实现规范。尤其需要补齐任务状态与处理状态分离、终态竞争、重试查询、动态证据身份、历史数据可获得时间、控制版本切换及乱序预测处理。

用户本次请求是“阅读、核查、评估和制定计划”。附件中的代码目录、HTTP 路径、时间和额度示例、算法候选及建议 PR 划分均作为待评审材料；不把它们当作实施或交易授权。仓库旧文档中对 v0.2 模拟实现的批准也不扩大本次权限。

### 基线证据

- 通过 GitHub branches/main 接口实时确认最新 main：`380284b8dd7fb5fbdb3ead24bef264035e185251`，PR #1 合并提交，与附件一致。[固定源码](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/tree/380284b8dd7fb5fbdb3ead24bef264035e185251)。
- 本地 HEAD 是 `36a14e76b002789b3da6f12c793c7c972af1ca0f`，不是 main 提交；但两者 Git tree 均为 `2fec3e43893aeb330a5b83e54e7e1f0c4b381f07`，`git diff origin/main HEAD` 无差异，工作树干净。因此本报告的本地路径/行号对应上述 main 的相同内容，没有混入未合并实现。
- GitHub main CI：run `38733601425`，push 事件，completed/success。[CI 记录](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/actions/runs/38733601425)。
- 本地 Python 3.12.15，SQLite 3.53.1；重新运行 `python -m unittest discover -s tests -q`，57 项全部通过。
- 本地 Ruff、第三方 JSON Schema 验证分别因缺少 `ruff`、`jsonschema` 未能执行；没有安装依赖或将失败写成通过。GitHub CI 包含这两类检查，但不等于本地已复验。现有测试也检查导出 Schema 与类型生成结果一致。
- 普通 git 远端查询受本地网络环境限制失败；最新远端分支与 CI 已改用 GitHub 连接器核实。没有 fetch、切换分支或修改 Git 元数据。
- 完整阅读附件；核对 README、架构 v0.2/实现说明、v0.2.1 接口、数据与策略文档、Agent 资料索引，以及下表涉及的源码、测试、Schema 和部署/CI 配置。未检查实际 VPS 运行状态、实际市场数据覆盖、真实账户或 LLM 服务，不能据仓库文本证明这些运行事实。

## 2. 现状核查：已实现、Demo 与缺失

以下相对路径均相对于上述固定 main；行号定位主要实现入口。“缺失”指当前仓库中没有对应可运行实现，不代表没有外部原型。

| 模块 | 核查结果 | 代码证据及边界 |
|---|---|---|
| 报价采集 | 已实现的基础能力 | `market_data.py:8,17,35`：固定 Testnet /info、metaAndAssetCtxs+l2Book、来源/价格/时间校验；`cli.py:35`：文件锁、退避、退出；非 WebSocket 全量行情 |
| 行情 SQLite | 已实现 | `storage.py:7,21`：WAL、(symbol,event_ms) 主键、INSERT OR IGNORE；只存报价，没有通用 source/interval/revision 历史表 |
| 数据健康检查 | 已实现的采集级检查 | `cli.py:15`：heartbeat、最后成功及资产报价年龄；不等于整个 Core/Agent 的 readiness |
| 基础协议 | 已实现 | `interfaces.py:38,76,140`：DataRequest、绝对目标 Decision、ExecutionReport；`contracts.py:33,111,145,229`：Forecast、Batch、风险契约；七份导出 Schema 见 `schemas.py:11`，不包含新增 Job/Transport |
| 冻结 Context | 已实现、支持范围有限 | `context.py:52,75`：仅指定 source 的 quote、空 parameters、固定字段；SQL 同时限制 event/received；`validate_account:14` 是输入形状与时效校验，不是账户采集器 |
| Producer 注册与静态数据声明 | 已实现、进程内 | `producers.py:32,58,87`：白名单实例、单 ID 版本、requirements 快照、同 dataset ID 冲突、独立深拷贝视图；没有远程身份注册与动态 session |
| Forecast 接入校验 | 已实现、同步调用 | `forecasting.py:72`：显式时钟、输入年龄/耗时、身份、证据、TTL；在 `producer.forecast()` 返回后检查预算，不能中断卡住的调用 |
| Agent | Demo | `examples/lt_adapter.py:24` 是独立协议实现，但仍进程内合成推理；`forecasting.py:44` MockAgent，没有 Worker、真实 LLM 或 HTTP 客户端 |
| 传统策略 | Demo | `forecasting.py:11` mark/oracle 比较，不是已验证 Alpha |
| 风险估计 | Demo | `forecasting.py:185` MockRiskEstimator：固定波动和对角矩阵，无历史收益估计 |
| Portfolio | Demo | `portfolio.py:11,29` FixedQuantityPortfolio；只接受 simulated risk，风险 interval 必须等于配置 horizon；不是资本配置模型 |
| Independent Risk Gate | Demo，有可复用边界 | `risk.py:26`：完整持仓与目标覆盖、时效、演示数量/总敞口限制、挂单直接 deferred；没有生产保证金/清算/压力与订单情景模型 |
| Execution | Mock 已实现；真实工具局部实现 | `simulation.py:52`：事务、Decision 去重/冲突、持仓、unknown/BUSY；`:108` reconcile 仅重读持久化结果。`execution.py:15,56,88` 是显式启用的人工 Testnet 下单/撤单工具，不能作为自动 ExecutionEngine |
| 只读账户/metadata 查询 | 已实现的低层接口 | `api_client.py:54,74`：SDK metadata、账户多接口查询；缺规范化权益/可用保证金快照、完整性和一致性恢复。`account_ready:99` 不是独立风险审批 |
| Journal | 已实现的模拟/研究日志 | `journal.py:26,47,64`：事件、模拟状态、报告历史；所有 record 包装固定 simulated=true，不能直接承载真实运行证据 |
| Research Registry | 已实现的离线治理 | `research.py:150,168`：不可变产物、人工状态迁移、offline-only；不执行回测、不自动部署 |
| Candles/通用 metadata 服务 | 只有局部类型/查询，完整能力缺失 | `models.py:14` 有 Candle dataclass，`api_client.metadata()` 有请求；没有历史抓取/补数/版本化存储/查询服务，不能称为已经支持历史数据 |
| Scheduler / AnalysisJob / Lease / HTTP / Feedback API | 缺失 | 当前 CLI 仅 collect/check/status/dry-run（`cli.py:79`）；没有持久化任务表、HTTP 路由或跨进程 Worker |
| Position Supervisor、可恢复 Paper、自动订单恢复、Replay | 缺失 | 现有 Mock 和研究记录不能替代持续监督、部分成交模型或可复现回放执行器 |
| 部署 | Collector 已配置；业务双服务缺失 | `deploy/compose.yaml:1` 含 collector 和 diagnostics profile 的 connection-check；后者不是 Agent/Core |
| CI | 已实现离线基线 | `.github/workflows/ci.yml`：固定运行镜像、lint、57 测试、Schema、两条 smoke run；无真实签名凭证、无部署步骤 |

源码文件均位于 `src/perpetual_alpha/`，除非另行注明。对应固定源码可从[模块目录](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/tree/380284b8dd7fb5fbdb3ead24bef264035e185251/src/perpetual_alpha)查看。

关键已验证语义有专门回归：`tests/test_interfaces.py:11,32`；`tests/test_architecture.py:118,192,251,274,300,311,318`；`tests/test_integration_stabilization.py:74,79,144,175,194,206,292,311,341`。保留全部测试，不为网络接入删改这些验收语义。

## 3. 必须保持的协议语义与接入方法

### 3.1 原有协议可以保留

- DataRequest/Decision/ExecutionReport 1.0、ForecastBatch forecast/0.1、RiskSnapshot/RiskVerdict 的现有版本无需因 HTTP 升版。
- Forecast 的可选数值继续可缺省；JSON 数值预测与目标数量遵循现有 Decimal 字符串要求，不要求 LLM 编造收益率或概率。
- Forecast.asof 等于信息截止点；Batch.created 是输出完成时间；expires 是新决策可消费截止时间；horizon 是预测窗口，四者不能合并。
- targets 是有符号绝对数量；未列出的资产不自动平仓；空 targets 不提出目标；显式 "0" 才提出归零。
- neutral/abstain/未覆盖不自动退出。**现运行器对批次中任意 error item 阻断整个 Producer 路径**（`dry_run.py:138` 附近），并非简单跳过 error 资产后继续执行其余预测；远程入口必须保留这一点。
- RiskVerdict deferred 和 ExecutionReport rejected 当前可以同时出现；不能给 Report 1.0 强塞不存在的 deferred 状态。Feedback 应同时展示两个层次。
- RiskSnapshot 是未年化 simple-return-per-interval，矩阵按 symbols 排序。真实估计应显式设置 simulated=false；旧 Demo 拒绝该输入是预期隔离，不应删除条件让其伪装生产算法。

### 3.2 新增异步入口，不把远程任务伪装成同步 Producer

保留旧 ProducerRegistry/ProducerPlan 与 checked_forecast 调用方式。新增 Forecast Ingress，接收已完成的 Batch，统一进行业务校验，另加 Transport 身份、Job、lease 和证据校验。旧 checked_forecast 可在测试保护下抽出“纯输出校验”共用函数；不把它改成等待远程 Worker 的阻塞调用，也不把旧 fixture 的合成时钟用到网络服务。

本地 Producer 的有效输出与远程 Ingress 的有效输出进入同一个下游消费边界；只有批准的控制身份/版本/控制 epoch 能推进 Portfolio。远程身份配置属于服务端 allowlist，不能为了复用 Registry 而让 Worker 上传 Python 实例、代码或 import 路径。

动态查询使用独立 Session，逐笔授权与记录，冻结为该任务的最终输入 manifest；它不改变静态 requirements 的定义。首版可只在同一个 repo 放 Worker 和轻量共享协议客户端，发布独立镜像；拆 repo 会增加版本协调成本，当前无必要。

## 4. 问题清单与建议

优先级：P0 为进入双进程闭环前必须明确的正确性/权限边界；P1 为数据与持续运行必须完成；P2 为规模和维护改进。这里是架构审查，未执行完整安全渗透或专项漏洞扫描。

| ID | 优先级 | 问题、后果及建议 |
|---|---|---|
| A01 | P0 | 同步 checked_forecast 和动态远程推理不匹配。拆纯校验与调用适配；Ingress 保持 strict 模式和可信接收时钟，禁止用伪 requirements 绕过检查。依据 `forecasting.py:72`、`producers.py:58`。 |
| A02 | P0 | 文档将 submitted/downstream_processing/analysis_completed 放在 Job 同一状态轴，容易让下游失败重新触发分析。分析 Job 在合法结果落库后结束，另存 processing 状态；交易另有 Report 状态。 |
| A03 | P0 | Lease 不能解决终态竞争与所有重试。claim 响应丢失、heartbeat/到期、submit/complete 并发都需事务条件更新；提交落库后旧请求只能查询/重放已接收 receipt，不能新增或替换结果。见第 5 节。 |
| A04 | P0 | 只靠 submission_id 不能防止新 ID 重复投递同 Batch。建立 job→唯一分析结果、producer+batch ID→唯一内容、submission ID→唯一 receipt、processing key→唯一 Decision 的持久化约束；outbox 与结果同事务提交。 |
| A05 | P0 | 不同 Job 的先后完成、控制版本切换均未充分定义。旧 asof 预测晚返回可覆盖新目标；任务接受与消费都核对 control epoch，控制范围维护消费水位；首版建议同控制范围串行，落后结果仅记录或拒绝推进。水位与拒绝规则需纳入契约。 |
| A06 | P0 | 动态 dataset_id 可被复用于不同请求/返回版本，evidence_refs 会指向错误数据。ID 在 Job/attempt 命名空间不可变，同 ID 不同定义拒绝；保存 request digest、返回内容 digest、schema/单位/质量与可获得时间，refs 保留字符串语义并映射到冻结 manifest。 |
| A07 | P0 | 当前 Journal 无条件标 simulated=true。新增运行态存储/审计事件，显式标明数据来源真实性与执行 backend；首轮可继续把 Mock 子链放独立旧 Journal。不得把真实行情+Paper 统一称为虚构数据，也不得把 Mock Report 当真实成交。依据 `journal.py:47`。 |
| A08 | P0 | 现有 ENABLE_TRADING=false 只是预留字段，实际人工工具由 --execute/enabled=True 启动，不能依赖该 env 作为全局保护。新服务镜像/启动配置不挂签名凭证、不组装 ExecutionClient；未来启用必须独立授权，不能因 merge/重启/profile 自动开启。依据 README:97、`execution.py:16`、`test_order.py:14` 附近。 |
| A09 | P1 | frozen as-of 与“任务中在线补数”有张力。严格模式下补抓时 received>asof，必须返回 missing；后台先采集/预热，再创建 Job。回放研究的重建历史可另标 observed/published availability 假设，不伪造 received 时间。 |
| A10 | P1 | 历史条目修订/迟到覆盖可破坏回放。报价表 INSERT OR IGNORE 适合保留首条，但不能直接扩成 candle UPSERT 表；新表保留 first_seen、版本/修订与原始内容，按 as-of 选当时可见版本。固定 as-of 还应固定 data/schema/policy 版本或返回内容，避免同任务后续查询因清洗升级改变答案。 |
| A11 | P1 | 当前账户是多次独立 SDK 查询，不能假定原子快照；USDPR 总余额不等于可用保证金。新 Adapter 保留各响应时间、账户模式、来源和完整性；未知订单与外部人工订单需入风险集合，消费前重新读取可信状态。依据 `api_client.py:74,99`、`context.py:14`。 |
| A12 | P1 | 风控不能只计算最终目标：撤单未确认、部分成交、未知订单及多腿过渡状态都可能增加风险。审核当前与可能成交情景；减仓路径独立且受授权政策约束，不能只比较绝对数量就认定组合风险下降。 |
| A13 | P1 | SQLite WAL 不等于多 Writer/多线程安全。Core 单活动实例、短事务串行写入，HTTP/SDK/LLM 不在写锁内；不要跨线程复用现有 connection，不靠 check_same_thread=false 解除安全问题。采集库与 runtime 分开，但 result/outbox/processing 需同一个事务域。 |
| A14 | P1 | 当前 MockExecution 即时写到目标、同 ID 返回旧报告，不能升级为 Paper 成交模型或直接接 SDK。新增执行状态与订单 intent/reconcile，旧 Mock 完整保留；Decision 去重不等于交易所请求 exactly-once。 |
| A15 | P1 | v0.3 认证原则尚未落实到传输规范。身份凭证与 lease token 分开；认证主体绑定 producer/version/role，跨 Job 查询禁止；请求体/字段长度、嵌套、记录/字节总量、并发都必须有界。kind/source 由服务端 provider 映射，禁止传任意 URL/SQL/路径；日志不记录 token/认证头。 |
| A16 | P1 | 历史不足、十资产价格口径与市场规则尚未验收。metadata 返回需要缓存/时效与交易规则来源；不同参考标的的文档单位不能直接当合约结算估值单位，估值乘数/币种/报价方向由已验证规则决定；不把外部参考行情当 par 可成交价。 |
| A17 | P1 | CI 原本 --network none，不能直接承载真实 HTTP 两容器测试。保留旧检查；新增隔离测试网仅连 Core/Dummy，禁止外网、真实凭证和 Exchange，验证跨进程而非同进程 TestClient。 |
| A18 | P2 | 部署验收放到最后过晚。双进程骨架阶段就验证容器网络、SQLite 挂载与重启；最终阶段再压测。特别测试 Collector WAL 的只读跨容器挂载，不能复制单个活跃 .sqlite 文件当一致备份，也不能将实时变动库标 immutable。 |
| A19 | P2 | 模块目录示意有 risk.py/risk/、portfolio.py/portfolio/、execution.py/execution/ 三类同名冲突，不仅 execution。保留原模块，新增 market_risk.py、portfolio_policies.py、risk_gate.py、order_state.py、reconciler.py 等；不提前创建大量空包。 |

SQLite WAL 只允许一个同时活动 Writer，需要同机共享内存；多个数据库之间也不提供共同原子事务。因此首版不需要 Redis/Kafka，但必须落实短事务、背压、checkpoint/磁盘监控和一致备份。[SQLite WAL 官方说明](https://www.sqlite.org/wal.html)、[事务说明](https://www.sqlite.org/lang_transaction.html)。

部署还应记录实际 `sqlite3.sqlite_version`。官方披露 WAL-reset 竞态影响部分旧版，3.51.3 及指定回移版本修复；本地 3.53.1 在修复之后，VPS 镜像内版本未探测，不能从 Python 版本推断安全状态。该项是部署检查，不据此声称当前仓库已发生损坏。[官方 WAL-reset 说明](https://www.sqlite.org/wal.html#walresetbug)。

## 5. 建议最小运行契约

### 5.1 两条状态轴

```text
analysis_job:
queued -> running(attempt, lease_epoch)
running -> submitted | completed_without_forecast | failed | expired
running -> queued 仅限无终态结果、尚未过 deadline、允许重试且 attempt 未耗尽

processing（仅 submitted 才存在）:
pending -> running -> completed | blocked | failed
running 崩溃 -> 恢复同一个 processing/Decision；不重新运行 Agent

execution:
沿用 ExecutionReport 1.0 状态，独立于以上两条轴
```

leased/running 不必设两个持久状态；claim 成功即 running，lease 信息描述所有权。Job 已 submitted 后，即使 Forecast 在排队期间过期，也保留 submitted，processing 标记 EXPIRED/blocked；不能把已提交任务重新置 queued。

建议 no_forecast 是合法无结果终态；abstain 是合法 Batch；error Batch 先保留原文与校验事实，但 processing 阻断。坏 HTTP/坏预测请求通常返回错误且保持当前租约，允许纠正；不让一次解析失败自动导致 Job failed。明确 fatal completion、重试耗尽和管理取消的分类；首版无取消 API 时不必额外实现它。

### 5.2 任务与租约

Job 保存 schedule key、planned tick、job/context ID、producer/version、role/control_epoch、asof/deadline、limits/config digest、状态与 attempt。定时去重建议 UNIQUE(schedule_id, scheduled_tick, producer_version, control_epoch)，防 Core 重启重复触发；补跑/合并积压是待用户确认的运行政策。

claim 在短事务中验证身份并从 queued 原子更新为 running，增加 epoch，返回不可猜测 token；每次 mutation 与 data query 都绑定 job/attempt/epoch/身份。deadline 为绝对上限，heartbeat 不延长 Forecast expiry 或 asof。服务端时间为判断依据；进程内测耗时用 monotonic，持久 UTC 时间记录需对时钟回拨采取暂停/重新核对的保守处理。

claim 增加客户端稳定 request_id，避免响应丢失后盲领第二项；恢复凭据可由客户端持久化 pending claim ID，并由服务端对同身份同请求安全重放。receipt 不在日志中泄漏 lease。即使采用每 Worker 最多一个活动任务，也需数据库约束，不能只靠进程内标志。

### 5.3 提交与终态竞争

接受 submit 的单事务完成：验证当前身份/epoch/lease/deadline → 校验 Batch 与冻结 manifest → INSERT immutable submission/accepted result → UPDATE Job submitted → INSERT outbox/processing pending → COMMIT → 返回 202 receipt。禁止提交前返回成功，禁止事务内调用外部服务。

同 Job 仅接受一个终态分析结果；submit 与 complete 使用同一个条件更新边界，先成功者获胜。相同 key/digest 重放原 receipt；相同 key 不同内容冲突。新 submission_id 携带已存在 Batch 也不能新建第二个处理单元。规范化依照解析后 payload 确定，明确可选字段默认化与数组顺序；拒绝重复 JSON 键/非有限数值，避免解析器与 digest 对同一输入理解不同。

租约过期后：旧 Worker 不能提交新结果。但对已经接受的同一提交，可在身份仍有权限时查询或重放原 receipt，即使 lease/Forecast 已过期也不能再次消费；撤销身份则拒绝访问。新 attempt 不自动继承旧 attempt 的工具结果、额度或推理状态：首版重跑分析，旧 attempt 仅保留审计。若未来要恢复推理，另定 manifest 与预算规则。

消费采用 at-least-once 调度 + 持久化副作用去重。稳定 decision_id 绑定 accepted result、policy/config/control epoch；重启恢复同一逻辑 Decision，不用随机 ID 重做。若状态变化必须重新规划，显式创建后继决策并记录 supersession；未知订单未核实前不自动规划替代订单。

### 5.4 数据 Session

- 预测资产权限、数据读取权限分别配置；允许读取辅助资产不自动授予对其生成控制 Forecast 的权限。
- DataRequest 仍用原字段；HTTP 解析器须额外校验 symbols/fields 序列、唯一性、allowlist 和参数 schema，不能只依赖 dataclass 的有限校验。
- request_id 同内容重放同结果，冲突拒绝。预算预留/结算原子化，避免并发查询绕过总限额；重试不双扣逻辑配额，但服务端仍有认证级流量限制。
- dataset_id 不可变；同一任务中要改变 fields/lookback/interval，使用新 ID。返回 request/result digest、source/schema/单位、asof、quality、truncated 与 evidence 映射。
- 任务结果提交后 manifest 封存，新 query 拒绝；与 submit 竞争的在途查询必须先完成记账并纳入 manifest，或明确取消且不产生可引用证据。不能结果已接受后补写“此前证据”。
- 分页也计预算，游标绑定身份、Job、asof、查询 digest 和版本；截断不能伪称历史完整。先实现有界小结果，确有需求再做复杂分页。
- required 缺失、不可用证据、未来时间、未知资产等保留现有拒绝语义；没有 evidence_refs 的合法旧 Forecast 不应被全局变成必填证据。若远程生产政策要求额外证据，标成入口政策而非偷偷修改 forecast/0.1。

### 5.5 HTTP、并发和反馈

HTTP/JSON Pull 足够首版：无任务可用 204，有接受且已持久化的提交返回 202；错误使用稳定 transport reason code，区分身份/权限、冲突、格式、过期、限流与临时不可用。不要把 Transport 错误码塞进旧 ExecutionReport.REASON_CODES。

建议初版短轮询+退避/jitter，Agent 一次一个分析任务；heartbeat 与推理有独立调度，不能因阻塞 LLM 调用丢租约。Core 单活动实例管理调度与控制消费者，API 的并发仅处理有界请求，实际 DB 写入串行；框架可选 FastAPI 等成熟方案，但以依赖锁定、线程连接管理、取消与限流测试为选型条件，不为首版引入消息中间件。

Feedback 返回 Job 终态、submission receipt、processing 状态和 Decision/Verdict/Report ID，带递增版本/游标。已接收不等于风险批准；Mock completed 不等于交易所成交。历史 Context 读取反馈按当时 observed/received 截止，当前运维反馈可返回最新授权状态，两种用途不能混用。

## 6. 数据、风险与执行的可行性边界

1. **历史数据先预热。**严格 as-of 会排除任务启动后下载的记录；缺历史时应明确缺失或创建新任务。长期回测可使用固定历史数据集与明确的“当时可公开获取”假设，单独报告其与真实接收记录的区别。
2. **不能承诺十资产都有足够历史。**官方 candleSnapshot 仅最近 5,000 根，HIP-3 查询需 dex 前缀；反复分页不能重建保留窗口之前的记录。先交付覆盖报告与缺口，BTC/ETH 实样，其余同一实现但不足时标记 missing。[Hyperliquid 官方接口](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#candle-snapshot)。
3. **协方差先用简单收益。**直接匹配现有 simple-return 契约；log returns 仅单独特征或显式转换。按共同 bar close 对齐，不将缺失补零；样本不足不返回 ready。样本协方差先于 shrinkage/优化器，允许奇异 PSD 矩阵，不强求可逆。
4. **预测期限与风险间隔分开。**首个真实政策可以明确仅支持一种经批准的风险评估时间尺度，不能默认 sqrt(time) 缩放成立，也不能把旧 Demo 的相等限制当业务真理。
5. **组合估值必须覆盖保留持仓。**生成新 BTC 目标时，ETH/GOLD 的实际仓位和所有待确认订单仍影响总风险。只有返回 Forecast 的资产不够；N=1..10 是接口能力，全账户超出允许集合时应阻断新增风险并告警。
6. **风险监督独立于 Agent。**Supervisor 与执行者在 Core 内按职责隔离即可，不必首版增加第三个签名微服务；Agent 离线仍持续读取账户/健康。自动减仓、撤单、暂停恢复属于需要批准的政策，不能仅凭“安全”二字自动授权。
7. **执行规范化不得暗改目标。**Portfolio 输出符合步长的目标并记录 sizing；Execution 若发现目标已不符合最新 metadata，应拒绝/请求重规划。无法清除的 dust、min notional、数量/价格舍入与 completed 的严格目标一致性需明确，不修改 Report 1.0 来掩盖残差。
8. **未知结果先核对。**本地 intent、cloid、订单回执、fills、fees、撤单和查询游标落盘。订单状态不存在/unknownOid 本身不足以证明从未成交，需要结合权威订单和成交状态；网络 exactly-once 不能只靠本地唯一索引承诺。
9. **Replay 只承诺可复现工程结果。**固定数据/receipt、时钟、配置、代码与模型版本，保存原 Forecast 供重放；真实 LLM 随机性和模型预训练知识的历史泄漏不由 as-of 数据接口自动消除，不能把历史 LLM 演练宣称为无泄漏 Alpha 验证。
10. **VPS 可行性需实测。**轻量 API Worker 值得尝试，不能用 collector 的 256m 上限推算整体资源。历史下载、JSON 编解码、DB checkpoint 和 LLM 延迟共同测；重计算移出 Core。Agent 迁往第二 VPS 时只改受保护连接与部署配置，Core DB 留在单机。

## 7. 建议 PR 拆分、依赖、测试与验收

以下均是待确认计划，不表示本次已经创建文件/分支。路径以 `src/perpetual_alpha/` 为前缀；保留所有现有入口和 Demo。每个 PR 从当时最新 main 的独立 `codex/...` 分支开始，经 CI/审查后合并；并行 PR 合并前重新同步契约。

| PR | 文件级建议 | 依赖、测试及验收 |
|---|---|---|
| 0 契约冻结 | 新 `docs/agent-transport-v0.1.zh-CN.md`、`runtime/contracts.py`、Transport Schemas；谨慎抽取 `forecasting.py` 的纯校验；新增 `tests/test_forecast_ingress.py` | 开发授权后首项。旧 57 测试/7 Schema/两个示例不变；新增最小/完整 Batch、所有时间边界、error 整批阻断、未知字段、静态请求兼容对照。验收：状态、身份、幂等 key、错误码无歧义 |
| 1 任务持久化与调度 | `runtime/jobs.py`、`runtime/store.py`、`runtime/scheduler.py`、版本化 SQL migration；`tests/test_jobs.py`、`test_job_recovery.py` | 依赖 0。真实文件 DB、多独立连接/进程竞争 claim；submit/complete/到期竞争，epoch 拒绝，重复 tick、重启积压、事务回滚/磁盘错误。验收：一任务一终态，无丢失已接受结果；不依赖 HTTP |
| 2 双进程 HTTP + Dummy | `transport/api.py`、`security.py`、`client.py`、`runtime/ingress.py`、`runtime/consumer.py`；独立 `agent_worker/`；新增 Core/Worker Dockerfile 和仅测试 Compose；保留旧 `cli.py` | 依赖 1。Scheduler→claim/heartbeat→一份 quote fixture→Forecast→outbox→旧 Mock 子链→Feedback。实际两个 Python 进程/容器；杀 Worker/Core、丢 HTTP 响应、重复提交、跨 Job 越权、控制切换、乱序结果。验收：有恢复证据、仅一个控制 Decision、无签名客户端。首次容器验证就在本 PR |
| 3 历史与 metadata | `data/metadata.py`、`candles.py`、`quality.py`、`store.py`、`provider.py`；独立 ingest CLI/配置，不重写 `storage.py`；数据质量测试/固定响应 fixture | 数据契约冻结后可与 1/2 并行。十资产通用接口、BTC/ETH 实样人工只读采样可另做；闭合 bar、重复、迟到、修订、断档、限流、单位、received/asof、全资产覆盖。验收：可复查覆盖报告、不伪造缺失、不影响 collector |
| 4 动态 Session Gateway | `runtime/sessions.py`、`transport/data_gateway.py`、provider 路由；`context.py` 只加受测扩展点；`agent_worker/dummy_agent.py` 扩多轮 | 依赖 2；真实 candle 查询依赖 3。多轮请求、同 ID 冲突、重复缓存、并发预算、权限、分页/截断、跨 attempt 证据、submit/query 竞争。验收：最终 Forecast 可重建到每一份授权输入；旧 requirements 行为不变 |
| 5 真实只读账户与快照 | `account/adapter.py`、`account/snapshots.py`，复用 `api_client.py` 低层查询；真实/fixture 模式分离 | 依赖 3 的规则/估值契约；可与 4 部分并行。统一账户余额与 margin 区别、接口间变化、坏响应、额外 GOLD 持仓/挂单、unknown intent、时效。验收：完整性不足时阻断新增风险；始终只读 |
| 6 风险估计与最小 Replay | `market_risk.py`、`backtest/replay.py`、data returns 工具；不新建同名 `risk/` 包 | 依赖 3/5 的数据和资产顺序，端到端依赖 4。手算 1/2/N 资产样本、PSD、奇异矩阵、缺口、零/负权益、不同 horizon、重放一致性。验收：RiskSnapshot 单位/模拟标志正确；相同冻结输入得到相同工程结果 |
| 7 Portfolio、独立 Gate 与监督 | `portfolio_policies.py`、`risk_gate.py`、`runtime/supervisor.py`、显式政策配置；保留 `portfolio.py`/`risk.py` Demo | 依赖 5/6 和用户批准的政策；仅 Paper。固定资本预算优先，逆波动率后续可单独 PR。测试 retained holdings、全账户敞口、挂单最坏情景、版本水位、失效审批、健康停开与受控退出。验收：目标可解释、审批绑定 digest、不能静默改目标；业务阈值未批准则不启用对应路径 |
| 8 可恢复 Paper Execution | `paper_execution.py`、`order_state.py`、`reconciler.py`、`runtime/execution_store.py`；明确成交/费用/滑点 fixture；旧 simulation.py 保持 | 依赖 7。intent 写前/写后、发送前/发送后崩溃，部分成交、撤单竞争、未知、反向、dust、多腿、重启、重放。验收：恢复不重复副作用，unknown 阻断新增危险动作，Report 与 Paper 账户一致；不含真实发单 |
| 9 VPS 验收与运维 | 更新 `deploy/compose.yaml`、独立 Core/Agent 配置、备份恢复脚本、运行手册、CI 双容器 job、健康/资源指标 | 依赖 2 的早期容器验证及 4–8 的目标范围。断网、重启、队列背压、峰值 RSS/CPU/磁盘、WAL、只读卷与备份恢复；测试 Agent 无密钥/DB mount/API 公网端口。验收：经用户确认的性能与恢复目标有实测记录，Paper-only |
| 后续独立 PR | 真实 Testnet Execution Adapter 与专门启用配置/验收 | 依赖全部必要安全/账户/恢复验收及**单独交易授权**。先写 Fake Exchange 契约与回放测试；本计划不授权任何真实订单，也不保证 PR9 后自动进入此项 |

依赖主线：`0 → 1 → 2 → 4`；`0 → 3 → 5 → 6 → 7 → 8`；`3 → 4`；最终集成 `4+8 → 9`。PR7 的端到端运行还需 PR2/4；独立算法不必等待真实 Agent。新闻/Jev/市场事件仅在内部 create_analysis_job 的 trigger 接口留枚举/来源边界，不建 scanner、不集成供应商。

第一轮可交付范围建议为 PR0–2，加 PR3 的数据契约设计；这时验证的是 Dummy+quote fixture+Mock Feedback，不宣称已有 candle、多轮工具或完整 Paper。多轮真实数据闭环以 PR4 验收为准。这样减少一次性更改 Forecast、数据库、HTTP、历史采集、组合、执行的耦合。

### 每个 PR 的回滚规则

- 新功能独立入口/配置，旧 collect/dry-run 原样可运行；停止新 Core/Worker 不影响已部署 collector。
- migration 采用版本号、前向兼容加表/列与迁移前一致备份；停止写入再回滚二进制，不能依赖 downgrade 丢掉已接受结果。
- 回滚保留 submissions、outbox、Decision、order intents 和审计；重启先恢复未完成状态，不以清库解决 BUSY/unknown。
- 未来若出现真实订单，回滚前必须账户/订单 reconcile；本阶段没有这类真实副作用。

## 8. 验证矩阵与完成标准

| 类别 | 必测场景 | 通过标准 |
|---|---|---|
| 向后兼容 | 57 旧测试、七 Schema、两条离线示例、旧 DataRequest/Forecast/Decision/Report 载荷 | 不删减旧语义，不读取私钥，不触发真实网络交易 |
| 身份隔离 | 未注册/版本错误/撤销 token、其他 Job、shadow、旧控制 epoch | 无越权查询或推进控制；错误可追踪且不泄漏凭证 |
| 并发/幂等 | 多 Worker claim、submit/complete、同 ID 异内容、新 ID 同 Batch、并发配额 | 一个合法终态、一个逻辑处理单元，冲突保留原记录 |
| 崩溃恢复 | DB commit 前后、202 丢失、消费前后、lease 到期、Core/Worker 重启 | 接受结果不丢失；已知 receipt 可查询；不重跑分析制造新交易 |
| 时间/证据 | 未来/过期、接收后补历史、bar 未收盘、修订、跨 attempt ref、处理排队至过期 | 不泄漏未来信息，不延期旧预测，拒绝无效证据 |
| 控制顺序 | 旧 Job 晚返回、新版本切换、shadow 失败 | 不覆盖更晚的控制状态，不阻断控制链，不生成 shadow Decision |
| 风险/执行 | 仅 BTC Forecast 但有 GOLD 仓位/挂单；unknown/部分成交；真实风险数据进 Demo | 覆盖所有潜在风险，不把未知当零，不误用 Demo 或伪造成交 |
| 运维 | 资源耗尽、磁盘满、WAL 增长、只读挂载、备份恢复、网络断开 | 有背压/健康状态/恢复证据；Agent 无密钥或 DB 直读；外网无 Core API 暴露 |

网络端到端测试使用独立本地隔离网络及合成行情/账户，不能只用 HTTP 框架同进程客户端宣称双进程验收。故障注入精确标注到事务/HTTP/订单步骤；重复跑成功不是替代条件竞争测试。真实性采样、VPS 压测、LLM 调用分别报告，不能混入离线 CI 成功的含义。

## 9. 需要用户决定的事项

下面给的是推荐方案与备选，均未写入生产默认值。工程细节可以在确认开发范围后落实；风险、资金、退出和真实权限必须由用户单独批准。

### 首轮开发前影响契约的决定

| 事项 | 推荐讨论起点 | 备选及影响 |
|---|---|---|
| 任务节奏/积压/并发 | 同控制范围串行；定时积压合并到最新有效 tick；旧 asof 结果不推进控制 | 全部补跑适合研究，但增加过期负载/LLM 成本；调度周期与最大并发需给预算 |
| frozen as-of | 严格 event/received 双截止，先预热数据；要新信息创建新 Job | 可设计显式新快照，但会影响 session/证据与比较语义，不应在同 context 偷偷滚动 |
| Runtime 与 Transport 方案 | 单活动 Core、SQLite runtime 同事务 outbox、认证 token+独立 lease、同仓库独立镜像；HTTP 框架锁定后验收 | PostgreSQL/独立 Worker repo 可扩展，但当前运维/发布成本更高；框架无须由用户逐个技术细节拍板，只需确认成本与边界 |
| 运行预算 | 分别给 deadline、lease、推理/API timeout、数据年龄、Forecast TTL、尝试次数、查询/字节/LLM 预算设上限 | 长任务容错更强但预测更旧、成本更高；附件示例时间/额度不作为默认 |
| 首轮目标 | PR0–2 的 Mock 闭环，之后 PR3/4 多轮真实数据；控制 producer/version 显式指定 | 直接首轮做完整多轮历史闭环会扩大审查和故障定位范围 |

### 进入组合/风险与持续 Paper 前必须决定

- 策略预测/允许交易的资产子集、风险覆盖集合；退出/到期/neutral/abstain/error 的业务政策。默认保留旧“不隐式平仓”语义不等于批准无限持仓。
- 固定资本预算或逆波动率等政策；风险窗口/收益间隔/评估期限、样本与缺失处理、再平衡规则；Paper 的成本/成交/资金费假设。建议先固定预算，避免同时验证优化器与运行系统。
- 资本分配、单资产/总/净敞口、杠杆、保证金缓冲、亏损/回撤、单边成交与未知订单容忍度。**本报告不提供或批准真实风险数字。**
- 自动停止、减仓/撤单、应急退出、人工干预及恢复谁有权限；Kill Switch 是停止新增风险还是包括退出，需明确且可审计。
- LLM 供应商/版本、能否发送账户摘要、费用预算/留存；VPS 资源与恢复目标、数据保留/备份范围。新增 VPS 以资源实测再决定，不默认采购。

### 任何真实 Testnet 订单前另外决定

实际订单类型、精度与残差政策、成本/滑点、部分成交/反向/多腿处置、签名凭证运维、启用时间和明确授权范围。合并 PR、启动容器或启用 Paper 都不自动授权真实 Testnet 订单；主网权限也不在本计划内。

Jev 身份/模型、新闻数据许可和事件扫描策略暂留后续。现在不需要选型，也不应成为前几个 PR 的阻塞条件。

## 10. 本次交付与下一步边界

交付本报告、问题清单、契约建议、文件级 PR 计划及验收矩阵。没有修改仓库实现、提交 commit、建立 PR、部署服务、读取交易私钥或发送订单。本报告保存在仓库外的审查产物目录，工作树保持不变。

建议讨论先聚焦：第一轮 PR0–2 范围、严格 as-of/预热规则、任务并发/积压与旧结果处理、控制版本切换和运行预算。确认开发阶段后，再从最新 main 建独立分支开始；真实资金风险阈值与交易授权继续单独保留。
