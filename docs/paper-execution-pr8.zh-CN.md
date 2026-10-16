# PR8：可恢复的合成 Paper 执行与 Core 集成

## 范围与权限

基于 PR7 已合并后的 main `e9585b9`，新增独立 Paper Core，通过现有 Scheduler → HTTP Pull → Dummy Forecast → Outbox → PR7 Portfolio/Gate → 持久 Intent/分段成交 → Feedback 跑通合成服务链。原 `runtime.http_core`、MockExecution、历史 Shadow Gateway 和既有协议保留。

**这是 synthetic Paper，不是真实交易所、实时行情 Paper 或 VPS 长期部署验收。** 没有签名客户端、真实账户读取、订单发送/取消或 LLM。只接受明确的合成状态、资金/估值/风险/成交配置；PR5 的真实 account-observation 不会转成 ready。所有示例阈值及恢复命令仅作用于隔离的合成数据库。

本 PR 解决 PR7 审核中的两项工程问题：持久暂停/权益基准，以及在 Paper 每次成交前读取当前状态。仍保留 PR7 的显式 `risk.return_interval_ms == policy.horizon_ms` 模拟限制；没有擅自加入平方根期限换算、EWMA 或生产参数。

## 文件与兼容性

| 文件 | 职责 |
|---|---|
| runtime/migrations/004_paper.sql | 同 Runtime 事务域中的配置、状态、仓位、报价、计划、Intent、成交、报告、检查证据和操作回执 |
| runtime/paper_engine.py | 有界本地 Paper venue、分段/反向/费用模型、幂等与对账、持久 Supervisor 集成 |
| runtime/paper_core.py | 独立 Core 服务，复用原 HTTP/认证/Scheduler/Outbox/Dummy 协议 |
| runtime/paper_operator.py | 可信本地 OS 操作者的 status/resume/reconcile/cancel，无 Agent HTTP 管理路由 |
| portfolio_policies.py / risk_gate.py | 新增执行时点入口，保留原分析 cutoff 和不可变目标/政策，允许当前执行状态参与检查 |
| runtime/mock_consumer.py | 模式隔离：拒绝将旧 Mock Consumer 接到 Paper runtime |
| config/paper-execution.synthetic.json | 显式合成账户/估值/预算与 paper-fixed-liquidity/0.1 成交模型 |
| scripts/validate_paper.py / validate_paper_http_containers.py | 离线账本与双容器、显式恢复、重启暂停验收 |
| tests/test_paper_execution.py / test_paper_core.py | 39 项账本/故障测试及 1 项独立 Core/Worker 进程验收 |

没有新增运行依赖、业务 Schema 或 Transport DTO。ForecastBatch 0.1、Decision/ExecutionReport 1.0、RiskSnapshot/Verdict 0.1 语义保留，Feedback 仍返回 Processing 及 Decision/Verdict/Report 引用，不新扩展为账户/订单明细外发。

## 状态轴与身份

AnalysisJob 提交后保持 submitted；下游执行取消、未知或失败不会重新启动 Agent。Processing 在 Paper 多步执行时保持 running，全部精确完成后 completed；取消/到期/版本切换/拒绝为 blocked，保留实际已成交数量。unknown 保持等待对账，暂停新控制工作。

```text
Plan: running → completed | failed | partial-cancelled
             → unknown → running（明确的本地账本对账；恢复交易还需另行 resume）

Intent: pending → partial → filled
        pending/partial → cancelled | unknown
        unknown → pending/partial/filled（仅根据已验证本地累计成交）
```

内部 cancelled 不添加到旧 ExecutionReport 枚举。未成交即取消报告 failed；已部分成交后取消报告 partial，items 保留实际数量并带原因，不能伪称 completed。accepted 只说明 Intent 已持久接收；completed 仍要求所有实际目标数量精确相等。unknown 的 actual_quantity=null，不拿未知当作可以重发的失败。

Decision ID 来自稳定 processing_id；Intent ID 来自 Decision/symbol/leg；Fill ID 来自 Intent/此前累计成交量，SQLite 唯一约束拒绝重复身份。Report 按 Decision 单调增加 sequence，保留全部历史。同一个已封存 Plan 重放原结果；配置 ID、完整配置摘要与模型版本不可热替换。

同一毫秒可以发生多个分段审批，因此新版模拟 Verdict ID 还绑定 Decision、当前状态、政策、检查时点与结果摘要；不能仅凭 Decision+毫秒区分不同账户输入。旧 Verdict 字段和历史 ID 仍合法，原 DemoRiskGate 不变。

反向目标拆为确定的平旧腿与开新腿；同资产前腿 filled 后才运行后腿，不并发穿越零点。多资产可以逐腿执行，Gate 继续检查保留仓位、全部其他挂单和过渡敞口；其他人工合成挂单只计入风险，不擅自成交或取消。

## 原子性、恢复与对账

PR2 旧 Mock 使用 Runtime/Journal 两个 SQLite，本 PR 不修改那个语义。**新 Paper 的 Intent、Fill、仓位、现金、报告、Processing/Outbox 终态与消费水位都在同一个 Runtime WAL/FULL 事务内**：

1. 领取 Outbox 后，在事务中重新读取已接受 Forecast、控制身份/epoch/水位、期限及 delivery fence，生成并封存 PR7 Proposal 与初始仓位。
2. 保存 accepted 报告和 Intent；此时没有成交。生成计划的事务中断全部回滚；启动只重新排队没有 Paper Plan 的旧 running reservation。
3. 每次至多一个有界部分成交。先持久检查当前输入与 Verdict，再在事务中写入确定 Fill、更新平均成本/现金/数量和剩余 Intent。
4. 模拟费用/滑点后的状态也必须满足预算；使用 SAVEPOINT 试算，不满足时撤销假设成交，取消剩余本地 Intent，不改写 Decision。真实已提交的先前成交保留。
5. 最后一笔成交与 completed Report、Processing/Outbox、consumed_asof 同时提交。提交后进程退出或响应丢失，不需要跨库补写 Feedback，也不生成第二次逻辑成交。

Paper configuration 限于自己的 control scope 和 control role；即使 Producer/version 相同，也拒绝另一 scope 的任务，不能因共用 Outbox 而继承其执行权限。

Core 单活动实例由现有 ProcessLock 保护；管理 CLI 与 Core 通过同库短事务串行。所有锁内仅有有界本地计算/SQLite，无外部网络 I/O。保留 delivery_epoch fencing，不能由旧 reservation 推动成交。

启动首先持久暂停，再从初始合成仓位和按序 Fill 重建数量、平均成本与现金，检查摘要、稳定 Fill ID、方向、费用、累计成交以及 Intent 与不可变目标计划的一致性。账本不符就停止启动/对账，不猜补单、不清库、不静默修正仓位。

unknown 的恢复只核对**本地 Paper venue 的权威账本**：明确 reconcile 后，已知累计量保留、只开放确实尚未成交的剩余量，暂停仍锁存，需要新的显式恢复。它不是交易所订单 exactly-once 保证；未来真实 Adapter 仍需 venue client ID、持久网络发送边界、交易所订单/成交/账户查询和未知执行政策，不能直接复用本地原子提交假设。

## 执行前刷新与时间语义

原 Job/Forecast 的 asof 和封存 manifest 不变；新增 Portfolio `analysis_asof_ms` 与 Gate `recheck_execution` 入口，将分析信息截止点和执行观察时点分开。Paper 每步读取当前本地账本、报价表、全部其他 Intent、控制 epoch/接受及消费水位，重新检查模型、年龄、目标步长、预算和 Forecast/Decision 有效期。

仅刷新 state_digest，不重绑 policy_digest、不改目标/Forecast/分析时间。政策变更使旧 Proposal 失效。原 PR7 `construct`/`check` 默认调用方式保留，已有测试不改弱。

本 Decision 自己的未成交 Intent 不再次作为独立挂单加入同一个目标路径，否则会重复计算；排除范围限于数据库中该不可变 Decision 的精确 Intent。其他/unknown Intent 不能排除。Supervisor 在无 Forecast 的循环中仍检查包含全部未成交量的当前账户。

报价目前来自显式 static-price synthetic tick，存于 paper_quotes；每次成交读取并校验其真实本地生成时点和步长，不在成交函数中将旧报价改成新时间。测试覆盖报价移动、陈旧、缺失及步长改变。Core 的内置生成器会持续发布相同配置价格的新合成 tick，**这不证明外部行情更新、可成交性或真实账户已刷新**。

风险矩阵也明确标记为 `synthetic-paper-covariance / paper-risk-fixture/0.1`，不是把 PR6 的旧真实历史或短频率结果重新贴时间/频率标签。PR6 历史统计与 PR5 真实观察保持独立；接入真实数据仍需估值、质量、风险期限和账户完整性政策。

Agent 冻结 Context 仍只提供原 quote fixture，account=None；不会将当前 Paper 仓位倒填到更早 Job cutoff，也不通过本轮擅自开放账户明细外发。

## 成交与资金模型

全部配置显式提供：fee_bps、slippage_bps、各资产 max_fill_quantities、min_notional_usd、order_timeout_ms。单步按数量步长向零取整，最多消耗配置的合成流动性；买入价为 mark×(1+slippage/10000)，卖出价为 mark×(1−slippage/10000)，手续费为绝对成交名义价值×fee_bps/10000。非正或非有限模型价拒绝。

初始 Intent 的 min notional 要求不满足则拒绝；已接收订单的部分成交不会因为剩余名义价值变小而重新套用初始最小门槛。新的步长使剩余量不合法时，保留已成交 dust 并取消余量，不自行舍入到另一个目标。

同向成交更新加权平均成本；反向成交按已平数量确认 PnL，剩余新向仓位使用成交价。现金保留已实现 PnL/费用，权益加入未实现 PnL；合成可用保证金沿用 PR7 的显式 margin_fraction 模型，峰值和 session 起点持久化，不在重启时重置损失。

这不是 Hyperliquid Unified 保证金、清算或真实资本模型；没有盘口排队、maker fill、市场冲击、资金费、撮合延迟或经过校准的流动性。超时/过期/版本切换只取消剩余本地 Paper Intent，**不会自动平掉已成交仓位，也没有真实撤单权限**。

## 持久暂停与操作授权

paper_meta 保存 paused/reasons、cash、peak、session_start、boot generation 和 pause_epoch。每次 Core boot 都生成新 generation 并暂停；状态正常或 unknown 对账完成也不自动恢复。

可信本地操作者读取 status 后，以 request_id、actor、当前 generation/pause_epoch 明确 resume；旧启动/旧暂停的请求不能新开权限。同 ID 同内容只返回历史回执，不会在后续故障后再次解除暂停；换内容冲突。reconcile/cancel 同样持久去重、留审计。actor 是审计标签，**本版认证边界是本地 OS/文件权限**，不是签名或生产权限服务；Agent 没有管理 HTTP 路由或数据库挂载。

session_start 是配置中的固定合成会话起点，跨重启保持；不实现真实日界线、自动换日、存提款调整或风险基准重置。production 恢复权限和会话政策仍须用户批准。

## 本地运行与运维边界

先创建独立 Paper Core（使用已有独立 credential 文件，勿提交 token）：

```powershell
.\.venv\Scripts\python.exe -m perpetual_alpha.runtime.paper_core --credentials <CORE_AUTH_JSON> --database .cache/paper/runtime.sqlite3
.\.venv\Scripts\python.exe -m perpetual_alpha.runtime.paper_operator --database .cache/paper/runtime.sqlite3 status
.\.venv\Scripts\python.exe -m perpetual_alpha.runtime.paper_operator --database .cache/paper/runtime.sqlite3 resume --generation <STATUS_GENERATION> --pause-epoch <STATUS_PAUSE_EPOCH> --request-id <NEW_REQUEST_ID> --actor <LOCAL_OPERATOR>
.\.venv\Scripts\python.exe -m perpetual_alpha.transport.dummy_worker --core-url http://127.0.0.1:8080 --token-file <WORKER_TOKEN_FILE> --state .cache/paper-worker/worker.json --once
```

status 给出当前 generation、pause_epoch、暂停原因、最多 20 个执行引用。reconcile 需要 request-id/actor；cancel 另需 decision-id。命令仅操作本地 Paper，不代表真实交易恢复授权。Core 暂停时 /healthz 为 unavailable、claim 拒绝，但 Feedback 仍可查询；无自动 resume 启动参数。

已知真实账户的非空只读验收不由本 PR 制造仓位补齐；本轮不访问任何在线账户或交易接口。尚未部署 VPS，也没有完成长时间运行、磁盘/备份/资源、跨 VPS TLS 验收，这些仍属 PR9。

## 测试与迁移

新增 **40 项测试**，原 355 项保留，合计 **395 项**。覆盖接收≠成交、部分/多腿/反向/取消/unknown、费用与滑点手算、最小数量/残余 dust、成本预算回滚、状态/报价/控制切换、过期、身份/政策/Delivery fencing、ledger 损坏、提交前后进程硬退出、终态反馈原子性、并发填单、暂停/权益/会话/授权跨重启、模式隔离、v3 迁移，以及独立进程 HTTP 闭环。

CI 保留旧离线/Schema/Mock/历史 Shadow 双容器检查，新增无网络只读容器的 Paper 账本验收，以及第三条 Core/Dummy 双容器验收：显式模拟 resume、完成 3 笔分段成交、重启后同一 Plan/Fill 保留且再次暂停。这里的容器是一次性隔离测试，不是 VPS 部署。

```text
python -m unittest discover -s tests -v
python -m ruff check . --no-cache
python scripts/validate_paper.py
python scripts/validate_portfolio_risk.py
python scripts/validate_schemas.py
python scripts/validate_account.py
python scripts/validate_market_risk.py
python scripts/validate_history.py
python3 scripts/validate_paper_http_containers.py
```

Runtime schema 3→4 为附加表迁移，在同一个事务内完成并保留既有 audit/任务数据。Paper 要求单独的空 Runtime：Mock configuration 与 Paper configuration 相互拒绝，不能把两种 Consumer 混在一个执行库里。

回滚优先停止新 Paper 入口，保留完整 SQLite/WAL 证据；旧功能在本分支新版代码中继续支持 v4。若回退到只认识 schema v3 的旧二进制，需要恢复升级前的一致备份，不能直接降 user_version 或删表。备份/恢复应用 SQLite 一致 backup；活跃 WAL 下不要只拷贝主文件，不通过清库解释 unknown。

## 仍未授权/未完成

生产资本、亏损/杠杆/保证金/退出/风险期限政策；真实 USD/合约估值与全账户风险准入；真实市场与账户执行前刷新；真实订单网络边界与交易所对账；长期权益账本/资金流调整；VPS 运维与恢复验收。未来真实 Testnet 或主网 Adapter 必须独立 PR 和单独授权。
