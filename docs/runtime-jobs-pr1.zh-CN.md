# PR1｜SQLite AnalysisJob、定时调度与事务 Outbox

2026-10-16。基于 PR #3 已合并的 main `54e2d91`，落实[开发基线 v0.3](development-baseline-v0.3.zh-CN.md)的 PR1，沿用 [PR0 Transport 契约](agent-transport-v0.1.zh-CN.md)。本实现提交独立 PR 审核，不部署，不自动合并。

## 1. 交付范围

已实现真实文件 SQLite 上的任务准入、原子领取、续租、提交/完成、注册主体核对、稳定回执、持久化去重、调度 tick、独立 Processing/Outbox 预留与恢复。接收 Forecast 时，最新身份、Job、lease、控制版本和接受水位从当前写事务中读取，不接受调用方传入这些状态来代替数据库事实。

当前仍没有 HTTP Server、token 认证服务、Agent Worker、常驻调度/消费者进程、Portfolio/Risk/Execution 调用或真实下单。Scheduler.tick 是由未来 Core 循环调用的一步操作；Outbox 只预留和记录投递，不执行交易。原业务协议与 PR0 Schema 不变。

## 2. 文件与事务边界

| 文件 | 职责 |
|---|---|
| `runtime/store.py` | 独立 runtime DB、应用标识/迁移版本、每次操作自己的连接、短写事务、可信时钟和审计 |
| `runtime/migrations/001_runtime.sql` | 首个事务迁移、唯一约束、索引与外键 |
| `runtime/jobs.py` | 受信配置、准入/领取/续租/提交/完成/反馈、过期回收 |
| `runtime/scheduler.py` | 持久 tick 去重、触发合并、锁外构建 Context、锁内再次准入 |
| `runtime/outbox.py` | 持久投递预留、delivery epoch、状态确认、启动恢复与水位复查 |
| `tests/test_jobs.py` | 文件 DB、权限/身份/时效/幂等、迁移/备份、锁等待与回滚 |
| `tests/test_job_recovery.py` | 两个独立 Python 进程的竞争与进程硬退出 |
| `tests/test_scheduler_outbox.py` | 调度合并、控制切换、接受/消费水位、Outbox fencing |

表包括 workers、controls、jobs、claims、submissions、submission_keys、completions、processing、outbox、schedules、audit，以及 runtime_meta 时钟记录。runtime 数据不混入 collector 或旧模拟 Journal；审计事件不统一伪标 simulated=true，不记录 lease token。

每次操作新建连接，在所属线程关闭，不共享 check_same_thread=false 连接。开启外键、WAL、synchronous=FULL；默认 SQLite 锁等待上限 5000ms，可显式配置 1–60000ms。写入使用 BEGIN IMMEDIATE，取得锁后才读取可信时间。比最后成功提交时间更早的 Core 时钟被拒绝；失败事务不假装记录了新的可信时间。

没有网络、LLM、交易所或 provider 调用占用 SQLite 写锁。Ingress 的纯校验与摘要核对在事务中完成；Context provider 在 Scheduler 两阶段事务之间调用。全文件 DB 恢复能力不等于交易所成交恢复，也未测试物理断电/磁盘硬件故障。

## 3. 数据库唯一约束与串行政策

- job_id 主键、context_id 唯一；创建摘要绑定初始 Job、manifest 和调度身份。同内容创建重试返回已有记录，不能替换 Context。
- jobs 上的部分唯一索引限制每 Worker 最多一个 running Job、同 control_scope 最多一个 running control Job。
- claim 在同一写事务中检查 pending/running Processing，因此分析提交后旧下游尚未结束时，不领取同控制范围的新分析；不同 scope 和 Shadow 不共享控制权。
- submissions 每 Job 仅一份；producer/version/batch_id 另有唯一约束；submission_keys 以 producer/version/submission_id 唯一绑定 Job/摘要。
- completions 每 Job 唯一，producer/version/completion_id 唯一；与 submit 在同一 Job 终态上竞争。
- processing 每 Job 唯一，outbox 每 processing_id 唯一；同 scope 最多一个 running control Processing。
- 调度 group/tick 唯一，另有持久 last_job_tick；不是依赖进程内 set 去重。

跨表的“Job 或 Processing 正在控制范围内活动”通过同一个 BEGIN IMMEDIATE 事务检查，不能只依赖两张表各自的唯一索引。所有运行入口共享该规则。可信 operator 的 inspect/原始 SQL 不属于 Agent 接口，不得在 PR2 暴露给 Worker。

JobQueue 默认 max_active_jobs=1000，统计 queued/running Job 加 pending/running Processing。这是可调整的工程准入上限，不是交易阈值。当前不自动清理历史 claims/results/audit；磁盘监控和归档留后续运维，不承诺历史体积永远有界。

## 4. 主体与控制配置

configure_worker 记录 Principal 的 worker/producer/version/role 及 enabled；每个领取、续租、输入读取、提交、完成和反馈操作都重新核对。停用或改变身份后，连旧 receipt 的重放与反馈也拒绝。其他 Worker 即使属于相同 producer/version，也不能访问当前归属他人的 Job。

这只是 Core 内的白名单/撤销检查，**不是身份认证实现**。PR2 必须先从受保护凭证建立 Principal，不能用客户端 body 中的“已认证”字段构造。configure_worker/configure_control 仅允许可信服务组装或运维调用，没有外部注册/改风控接口。

控制切换必须显式提高 epoch，保持接受/消费水位不倒退。相同配置重放不改变 epoch。旧 epoch 排队 Job 保留原记录，不伪造提前到期；它们不能领取，也不阻挡新 epoch 的 queued 准入。旧 running Job 仍占用控制执行槽，直到租约丢失/终止，防版本切换时产生双控制活动。

撤销 Worker 凭证停止其访问与新提交；已合法接受的历史结果不因 token 轮换自动删除。要撤销该策略控制结果，使用 control epoch 切换；Outbox 会复查并阻断旧结果。

## 5. claim、lease 与终态恢复

claim request_id 按 Worker 唯一，保存完整领取响应；同请求重放返回原 lease，不多领第二项。一次空结果也稳定重放为空，下一次轮询必须新 request_id。旧领取响应过期后仍不能使租约复活；Worker 应根据新鲜 heartbeat/feedback 判断，不能把重放当重新领取。

租约 token 由 secrets 生成，只存受限 runtime DB/claim 回执，不写审计。heartbeat 校验当前 attempt/epoch/token，并在 deadline 内延长 expires；保留原 claimed_ms，不能借续租重置推理预算。

recover_jobs/claim/scheduler 检查实际 deadline 与 lease expiry。Core 重启不无条件取消有效租约；过期租约在仍有尝试额度时 requeue，下次 claim 增加 attempt/lease epoch 并生成新 token；尝试耗尽 failed，达到 deadline expired。旧 Worker 的续租/新提交被拒绝。submitted 与 no_forecast 等终态不回队。

无法继续工作的旧 queued Job（例如 epoch 或接受水位已失效）不被领取，不阻塞新鲜控制触发，最终按原 deadline 归档 expired。不会通过修改其 asof 或 Context 来“更新”旧任务。

## 6. Forecast 接受事务与回执重放

submit 不使用外部预校验结果作为落库凭据。事务内部执行：

1. 读取并核对当前白名单主体与 Job 归属。
2. 查询 submission key 与已接受结果：同内容返回原 receipt；冲突拒绝。
3. 新结果检查全局 Batch 身份、Job 尚 running，以及最新租约。
4. 读取当前控制 identity/epoch/accepted_asof 与封存 manifest，调用 PR0 validate_ingress；接收时间取自取得写锁后的 Core 时钟。
5. 插入 immutable Submission 和 submission key，将 Job 改 submitted。
6. 同事务插入 Processing 和 Outbox；正常控制为 pending，Shadow 为 completed，任一 error item 为 blocked，后两者 Outbox 直接 done。
7. 推进 control 接受水位、写接受审计，commit 后才把 SubmissionReceipt 返回调用者。

上述步骤任一失败，包括中途 SQL 错误或 commit 前进程硬退出，结果/终态/Outbox/水位全部回滚。commit 后响应丢失，重新打开 DB 并提交同内容，只返回原 receipt，不重新分析，不新增处理单元。

换 submission_id 重发同 Job/Batch/内容会记录一个 alias，仍返回原始 receipt（其 submission_id 是首次接受的 ID）。新 ID 不允许替换同 Job 的 Batch；Batch ID 或 submission ID 跨 Job 重用也拒绝。回执重放不受旧 lease/Forecast 到期影响，但仍受当前身份授权和 Job 归属检查。

complete 的 no_forecast/failed 与 submit 竞争同一终态；同 completion_id/内容稳定返回原响应，异内容冲突。无预测完成不创建 Forecast/Processing/Outbox；坏 Forecast 请求只拒绝该次提交，不自动使整个分析终止。

## 7. 定时积压：先合并触发，再冻结 Job

Schedule 显式提供 interval、tick 后 deadline、身份/控制范围、预测资产 allowlist 与 JobLimits；没有批准生产调度或资金风险数字。group 由 schedule_id/producer/version/control_epoch 确定；同 group 改配置拒绝，需新 schedule_id 或明确版本/epoch 升级。

tick 流程：

1. 从可信时钟计算最新计划时点，事务中维护 pending_tick，回收真正到期的 Job。
2. 已物化过该 tick、控制范围忙碌、tick 已过 deadline 或不比接受水位新时，不创建 Job，不调用 provider。
3. 允许创建时，在锁外构建固定 context_id/asof 的 Context。
4. 重新取得事务，复查当前 tick、控制配置、水位、busy 状态与唯一键，才原子创建 Job 并推进 last_job_tick。

忙碌期间只更新最新触发，不创建每个错过时点的 Job。恢复空闲后物化最新有效 tick，不逐根历史补跑。provider 失败不留下半个 Job，同 tick 可重试；provider 耗时跨到下一 tick 则丢弃未入库 Context，再按新 tick 构建。已创建 Job 的 asof、manifest 永远不因合并触发而改写。

尚未实现常驻 timer/HTTP/Worker；PR2 负责循环频率、优雅关闭和健康探针。新闻/Jev/市场扫描仍只保留内部 enqueue 边界，没有扫描器。

## 8. Processing/Outbox 恢复与后续执行边界

Outbox.begin 在事务内选择 pending 结果，重新核对 control identity/epoch、接受/消费水位、Forecast expiry 和输入年龄。不合法的结果只将 Processing blocked，Job 保持 submitted。合法结果预留为 running，增加 delivery_epoch，返回稳定 processing_id、Batch 和 manifest；不调用组合、风控或执行。

Outbox.finish 核对当前 running reservation 与 delivery_epoch；旧/重复确认拒绝。complete 再次复查控制与时间条件，失效时不能标记成功；只有合法 complete 推进 consumed_asof。Decision/Verdict/Report 引用仅是调用方提供的审计关联，不代表本 PR 生成过这些业务产物。

Outbox.recover 只能在**旧 Core/消费者已停止**的启动恢复阶段调用：running 回 pending，同 processing_id、递增 revision/epoch；旧 reservation 不能确认新一次投递。它不根据进程重启推断交易所结果，也不是活跃消费者的强制接管机制。

**单活动 Core 仍是部署前提。** 本 PR 尚无常驻服务及其进程所有权锁；PR2 必须在恢复/启动消费者前保证旧 Core 已退出。多进程测试验证的是数据库竞争边界，不是批准部署多个活动 Core。

PR2 接 Mock 控制链时仍必须：

- 在副作用之前再次核对控制版本与消费水位，不能仅依赖 begin 时的快照。
- 在第一次消费时冻结 policy/config 和稳定 Decision 身份；恢复同一个逻辑 Decision，不能因重启换 ID 或换政策重做。
- 将投递视为 at-least-once，配合现有 Mock/未来执行后端的持久化幂等；本 Outbox 不承诺外部 exactly-once。
- 若副作用已发生后才检测版本/时效变化，记录真实结果并 reconcile；finish 的 blocked 不会撤销外部副作用。

DataQueryResponse 的 payload_digest 真实性仍留 PR4：当前仅保存服务端已提供的静态 Context/manifest，不实现数据来源验证、动态额度记账或响应摘要生成网关。

## 9. 迁移、备份与回滚

RuntimeStore 拒绝 memory DB、collector/旧 Journal、其他 application_id 和未知未来 schema。迁移在单事务内核对版本并建表；并发首次打开不会重复迁移，失败后没有半建表，可修正后重试。数据库异常直接失败，不能捕获后当成“尚无提交”继续。

使用本地文件系统 WAL，不跨 VPS/NFS 共享。SQLite busy 在有界等待后向调用方报错；未来 HTTP 层映射临时不可用，并用相同请求 ID 重试，不能创建新 ID 绕开不确定结果。

本地使用 SQLite 3.53.1；VPS 部署前仍须记录容器实际 sqlite3.sqlite_version 并核查已知 WAL 修复/发行版回移补丁，不能仅凭 Python 版本或 CI 成功推断部署库版本。

一致备份使用 SQLite backup API 或停写后的完整一致快照，不能只复制活跃 .sqlite 文件忽略 WAL。测试使用 backup API 恢复 Job、回执键和 Outbox 后验证重放；这里未新增远程备份服务。DB/备份含 lease 和授权记录，沿用 Core 受限目录，不挂载到 Agent 或公开下载端。

回滚时先停止新 Core/消费者、保留 runtime 文件和 accepted results，再回滚二进制；PR0 的旧离线入口继续可用，但不认识 PR1 runtime DB，不能让它重建该库。没有降级删除/清空队列脚本；不能靠清库解决 unknown/BUSY。

分支保护（必须 PR/CI、禁止直接推 main）作为仓库管理后续项，本 PR 未修改 GitHub 权限或 ruleset。

## 10. 验证与当前限制

本地验证基线：原 94 项保留，新增 58 项，合计 **152 项 unittest**；最终远端结果以 PR 对应 CI 为准。测试包括九类独立进程竞争场景、六个进程硬退出窗口、SQL 中途失败、锁等待跨租约截止、迁移失败/并发迁移、一致备份和时钟回拨。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check . --no-cache
.\.venv\Scripts\python.exe scripts/validate_schemas.py
.\.venv\Scripts\python.exe scripts/validate_runtime.py
```

测试数据、账户名和配置均为合成 fixture；没有真实 LLM、HTTP 或交易所订单。原七份业务 Schema、Transport bundle、业务协议、签名工具、部署配置和两条离线示例继续保留。

通过本 PR 只证明上述 SQLite 边界和恢复行为；HTTP 身份认证、双进程链路、实际部署资源、数据真实性、真实风险和订单恢复均未因此完成。PR1 等待审核，不提前开始 PR2。
