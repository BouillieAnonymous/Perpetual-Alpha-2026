# Agent Transport 0.1｜PR0 契约与 Forecast Ingress

日期：2026-10-16。基于 PR #2 合并后的 main `c2b2db3`，落实[开发基线 v0.3](development-baseline-v0.3.zh-CN.md)的 PR0。本文及源码是提交审核的实现；不是部署或真实交易授权。

## 1. 本 PR 的边界

已实现纯数据结构、解析/校验、状态迁移规则、Forecast Ingress 预校验和单元测试。新增 transport_version=`agent-transport/0.1`；不改变 ForecastBatch forecast/0.1、DataRequest/Decision/ExecutionReport 1.0、RiskSnapshot/RiskVerdict 或原七份导出 Schema。

**尚未实现**数据库/UNIQUE/事务 Outbox、Scheduler、身份认证服务、token 生成、HTTP 路由、跨进程 Worker、数据查询后端、预算计费、反馈数据库、真实交易。数据库原子性和真实请求幂等必须由 PR1 实现；HTTP 与双进程必须由 PR2 验收。纯状态函数不能独自保证并发安全。

## 2. 文件与使用入口

| 文件 | 职责 |
|---|---|
| `src/perpetual_alpha/runtime/contracts.py` | AnalysisJob、JobLimits、Lease/LeaseRef、ProcessingRecord、SubmissionReceipt；严格类型解码及纯状态迁移 |
| `src/perpetual_alpha/runtime/ingress.py` | 服务端 Principal、租约守卫、冻结 manifest 摘要、纯 validate_ingress |
| `src/perpetual_alpha/transport/messages.py` | 13 类 HTTP 请求/响应、DataRecord、有界严格 JSON 解码 |
| `src/perpetual_alpha/transport/schemas.py` | 独立 Schema bundle 生成器；不扩大原七份业务 Schema 的注册集合 |
| `src/perpetual_alpha/forecast_validation.py` | 从原同步入口抽出的无 I/O Context/Forecast 校验 |
| `src/perpetual_alpha/forecasting.py` | 原 checked_forecast 签名、严格/fixture 模式、调用与时钟检查顺序保留；ForecastUnavailable 仍可从这里导入 |
| `docs/schemas/AgentTransport.schema.json` | Draft 2020-12 bundle；`$defs` 可单独选择消息或嵌套记录 |
| `docs/examples/agent-transport.json` | 每类消息一个合成样例；示例 lease_token 不是真实凭证 |

所有新整数使用非负 JSON 安全整数，拒绝 bool 充当 int；预算必须显式正数。新信封要求所有声明字段（包括 transport_version），拒绝未知字段与版本。嵌套 Forecast 按旧解析器处理，数值预测等可选字段继续允许省略；不要求额外新增预测字段。

新信封字符串非空、最多 4096 字符；现有 Forecast 字段限制不在本 PR 改变。JSON parser 必须显式提供 max_bytes，拒绝重复键、NaN/Infinity、无效 UTF-8、错误形状。PR2 仍须对 HTTP 流、深度、并发、字段和速率做有界处理；body 字节限制不等于完整资源网关。

DataRequest 仍使用原字段，参数可以表达未来 candle 请求；契约能解析不等于 provider 已支持。新传输入口额外检查 symbols/fields 非空唯一、资产前缀及整数范围，不修改原静态 Producer 的调用方式。

## 3. 任务、控制身份与三条状态轴

AnalysisJob 包含 job/context ID、producer/version、control_scope/epoch、role、允许预测 symbols、asof/created/deadline、status、attempt/lease_epoch 与 limits。asof≤created<deadline；control_epoch 从 1 开始。symbols 是服务端预测 allowlist，不是本次必须预测的资产，也不是完整账户风险集合。

control_scope 是唯一控制权限范围，首版由 Core 配置固定到该 Paper 账户控制链，不允许 Agent 指定。Shadow 使用 role=shadow，不能推进 Portfolio；数据读取辅助资产不自动赋予 Forecast 控制权。

```text
AnalysisJob:
queued --claim--> running（attempt+1，lease_epoch+1）
running --submit--> submitted
running --no_forecast--> completed_without_forecast
running --fail--> failed
running --lease_lost--> queued（还有尝试额度）/ failed（耗尽）
queued/running --expire--> expired（now >= deadline）

Processing:
pending --start--> running
running --recover--> pending（同 processing_id，revision+1）
running --complete/block/fail--> completed/blocked/failed

Execution:
继续沿用 ExecutionReport 1.0，独立于以上两条状态轴
```

AnalysisJob 的 submitted/no_forecast/failed/expired 为终态，不能因下游错误回到 queued。提交后预测过期使 Processing blocked，不重跑分析；blocked/failed 的原因不能是 OK。Processing 的 recover 只用于崩溃恢复，业务 blocked/failed 不自动重新调度。

transition_job/transition_processing 返回新记录，不写库。调用方必须在 PR1 事务内核对原状态/租约再应用，拒绝失效快照上的重复更新。lease_lost 事件必须基于服务端确认的租约丢失；claim 必须先实施每 Worker 一个活动任务、同 control_scope 串行与定时积压政策，不能由该纯函数替代。

LeaseRef 为 job_id/attempt/lease_epoch/lease_token；Lease 另带 worker_id、claimed/expires。租约时间必须在 Job 生命周期内，所有权校验覆盖认证 producer/version/role、Worker、Job、attempt、epoch 和 token。heartbeat/query/complete/new submit 均复用 validate_lease；now==lease expiry 或 Job deadline 时失效。

PR1 续租不得超 deadline，不重置 claimed_ms，也不能借续租重置推理预算。失去旧租约的 Worker 不能提交新结果。新 attempt 重新开始分析，旧 attempt 数据只保留审计，不自动继承推理状态或证据。

## 4. ID、重复请求与事务职责

| 身份/约束 | PR1 必须落地的语义 |
|---|---|
| claim request_id | 按认证 Worker 作用域稳定重放领取结果，防领取响应丢失后又领第二项；同 ID 异操作内容拒绝 |
| job_id/context_id | Core 分配，一个 Job 一个冻结上下文；不能客户端自由串线 |
| attempt/lease_epoch | 旧 Worker fencing；Job 重试递增，不复用旧 token |
| submission_id | 按 producer/version 作用域唯一绑定 Job 与规范化 Batch 摘要；同 ID 异内容/异 Job 冲突 |
| producer/version + forecast_batch_id | 绑定唯一 Job 和内容；新 submission ID 重发同 Batch 不能生成第二个 Processing |
| Job 分析终态 | Forecast submit 与 complete 共享一次性终态竞争；同 Job 第二个不同分析结果冲突 |
| processing_id | 稳定绑定已接受结果；重启消费恢复原处理单元，不随机新建 |
| decision_id | 稳定绑定 processing、policy/config/control epoch；重新规划须显式后继记录，不改原目标 |
| schedule 去重 | 内部 schedule_id/planned_tick/producer version/control epoch 唯一键；不是新增 Agent 权限 |

payload_digest 是将 ForecastBatch 通过旧解析器并 to_dict 默认化后的 canonical JSON SHA-256；不包含 submission_id、lease token、Transport 版本。可选字段省略与显式 null 会规范化一致；数组顺序与十进制字符串文本保留，不将 "1.0" 和 "1" 自行改写成相同内容。改 Batch ID/内容是另一个载荷，但仍受每 Job 一个终态约束。

**本 PR 只计算摘要并定义规则，不实现 dedup store。** validate_ingress 返回 ValidatedForecast，不能当 SubmissionReceipt，不能据此发 202。PR1 接受事务必须一次完成：最新身份/状态/lease/watermark 复查 → 结果/receipt → Job submitted → Processing/Outbox → commit。PR2 才把已提交回执映射成 HTTP 202。

对已接受的同内容重试，先按当前认证权限查持久 receipt；即使原 lease/Forecast 过期，也只重放原 receipt，不创建新结果。撤销身份仍拒绝访问；不能为重放旧 receipt 调用“新提交”的 lease 检查并诱导新 Job。完整重放/冲突/跨 ID 去重与崩溃窗口测试留 PR1。

## 5. Ingress 的可信输入与复用边界

validate_ingress 的 submission 是客户端产物。job、当前 lease、Principal、context/requests、manifest_digest、received_ms、current_control_epoch、accepted_asof_ms 均必须来自 Core 的可信配置/存储/时钟；不从客户端 JSON 取值。Principal 虽有内部严格数据类，但不注册为 HTTP 消息 Schema，不接受客户端“自报已认证”。

执行顺序：

1. 重新解析所有记录与 Batch，抵御 frozen dataclass 内可变对象被修改。
2. 核对认证身份、Worker 与有效租约；Job 必须 running。
3. control 任务校验当前 epoch；asof 必须严格大于此前已接受控制结果水位。相等也拒绝推进，重复结果由持久 receipt 分支处理。Shadow 不推动控制水位。
4. 校验 received-claimed≤推理预算、received-asof≤输入年龄；deadline/lease expiry 为严格上界。Forecast.created 在 asof 与可信 received 之间，远程新结果不得早于本 attempt 的 claimed；Forecast.expires 严格晚于 received。
5. 核对 job/context/asof，manifest 中 dataset ID 唯一且视图无越权额外数据；重新计算摘要匹配 Core 已封存的摘要。
6. 调用与同步 checked_forecast 共用的 Context/Forecast 校验，验证 source/kind/parameters、required 状态、记录 event/received≤asof、字段、Batch 身份/时点、预测资产及 evidence_refs。
7. 保留合法 Batch；任一 error item 令初始 Processing blocked/PRODUCER_ERROR；合法 Shadow completed/SHADOW_ONLY；其他合法输出 pending/OK。neutral/abstain/空 Batch 不自动生成任何仓位动作。

asof watermark 在“接受结果”和“消费决策”两端都要保护。PR0 提供接受端预校验，PR1 事务性推进接受水位，PR2 消费端必须检查 epoch 与接受/消费水位，防先前已排队的旧结果后消费覆盖更新结果。同范围串行是额外保障，不能省掉校验。

input_digest 封存请求定义与 Context 内容。PR0/PR2 静态 quote fixture 使用该 manifest；动态 Session 在 PR4 增量记录请求/返回，提交时冻结最终 manifest。digest 来自服务端，不能让客户端随内容提供一个自洽摘要就获信任。dataset ID 仍是旧 evidence_refs 字符串语义；同任务同 ID 改定义必须拒绝。

本 PR 不替 provider 证明数据真实或 fresh，也不执行数据预算计费。required 状态/未来时间等沿用旧校验；供应商真实性、历史质量、权限 allowlist、max_age、记录/字节总额度与 query/submit 竞争由后续 Gateway 在创建 manifest 前实施。

## 6. 未来 HTTP 映射（仅契约，当前没有监听端口）

| Method/path | 请求/响应 Schema `$defs` | 预期状态 |
|---|---|---|
| POST /v1/jobs/claim | ClaimRequest → ClaimResponse | 200；无任务 204 空 body |
| POST /v1/jobs/{job_id}/heartbeat | HeartbeatRequest → HeartbeatResponse | 200 |
| POST /v1/jobs/{job_id}/forecast | ForecastSubmission → SubmissionResponse | 新接受 202；认证持久回执重放 200 |
| POST /v1/jobs/{job_id}/complete | CompleteRequest → CompleteResponse | 200，终态事务提交后才返回 |
| POST /v1/jobs/{job_id}/data/query | DataQueryRequest → DataQueryResponse | 200，missing/stale/error 由 dataset 状态描述 |
| GET /v1/jobs/{job_id}/feedback | 无 body → FeedbackResponse | 200；含 receipt、Processing 和 Decision/Verdict/Report 引用 |
| GET /healthz | 无 body → HealthResponse | ready 200 / unavailable 503；存活与依赖就绪的具体探针由 PR2 落地 |

URL job_id 必须与 body lease.job_id 相同，否则 INVALID_REQUEST。身份使用独立 Authorization 头，不写入 body、Schema、日志或 digest；lease token 不等于认证 token。默认不提供外部创建 Job/下单/改风险参数接口。

ErrorResponse 包含 request_id/code/message/retryable/version。400 INVALID_REQUEST、UNSUPPORTED_VERSION；401 UNAUTHENTICATED；403 FORBIDDEN；404 NOT_FOUND（含防越权枚举）；409 ID_CONFLICT、LEASE_LOST、STALE_RESULT；410 EXPIRED；422 INVALID_FORECAST、INVALID_CONTEXT；429 LIMIT_EXCEEDED；503 DATA_UNAVAILABLE、UNAVAILABLE。dataset 本身缺失是正常 200 状态，与服务依赖不可用的 503 分开。

共享 ForecastUnavailable 原有 reason_code 保留为内部审计原因，HTTP 适配器在 PR2 映射为上述传输错误，不加入 ExecutionReport 原因码。错误消息应脱敏，retryable 仅用于暂时性限流/服务不可用；不能自动重试已冲突终态或未知订单。

所有 Feedback ID 只供关联审计，AnalysisJob submitted 不等于 Processing completed，更不等于 Exchange 成交。Mock/Paper 与真实数据/执行真实性由各自记录区分，不能复用旧 Journal 固定 simulated=true 包装来描述所有运行事件。

## 7. 测试、CI 与后续验收

新增测试位于 tests/test_transport_contracts.py、tests/test_forecast_ingress.py。覆盖请求/响应 JSON 往返、未知/缺失字段、版本、bool/int、重复键/非有限值/大小、嵌套记录、三轴状态与终态、租约/身份/epoch、deadline/expiry 恰等边界、接受水位、manifest 篡改、未来/缺失/错误数据、证据、辅助资产权限、error 整批阻断、Shadow 和旧同步接口一致性。

验证命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check . --no-cache
.\.venv\Scripts\python.exe scripts/validate_schemas.py
```

原七份 Schema 与样例检查继续；同一 CI 脚本增加独立 Transport bundle 和 13 个消息样例的 Draft 2020-12/Python 双重验证。重新导出只需 `python -m perpetual_alpha.transport.schemas`，不运行旧业务 Schema 导出来产生无关变更。

PR1 必须补数据库唯一约束、事务竞争、Worker 串行/调度积压、回执重放和崩溃恢复的真实文件 DB 测试；PR2 必须补真实两进程/容器、认证、HTTP 响应丢失和控制消费的验收。不得将本 PR 单元测试通过写成这些运行能力已经完成。

### 本地验证记录（2026-10-16）

- Python 3.12.15、原 31 个锁定运行依赖检查通过；安装仓库已锁定的检查工具，未修改依赖清单。依赖兼容性通过 uv pip check 验证（本地未安装 pip 模块）。
- 原 57 项回归保留，新增 37 项，共 94 项 unittest 通过。
- Ruff、原七份业务 Schema/样例、独立 Transport bundle 和 13 类消息样例全部通过。
- 原 dry-run 与独立 LT Dummy 两条 smoke run 均为 simulated/completed。
- 原业务契约/Schema、签名工具、交易开关及部署配置均未修改；没有真实 LLM 或下单调用。
- GitHub 容器环境检查以本 PR 对应 Actions 为准，不把本地通过代替远端结果。
