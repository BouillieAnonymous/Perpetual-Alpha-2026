# PR2：Core ↔ Dummy Agent HTTP 通信闭环

## 范围与运行性质

遵循 development-baseline-v0.3.zh-CN.md：PR0 消息、Forecast 0.1 与 PR1 SQLite/Outbox 继续生效；新增真实 HTTP 适配层、两个 Python 入口、独立 Worker 状态文件及合成流水线。原同步 Forecast 接口、LT Adapter、七份业务 Schema、collect/dry-run 命令保持兼容。

闭环：Scheduler → AnalysisJob → Worker 领取 → 固定 fixture 查询 → Dummy ForecastBatch → Forecast Ingress → Outbox → 现有 FixedQuantityPortfolio → DemoRiskGate → MockExecution → Feedback。

**全部是模拟。** `config/mock-http.toml` 的价格、余额、数量、额度、TTL 均为合成测试配置，不代表真实账户、历史行情或生产风险授权。只有 `mock-http` 模式，没有切换真实 LLM、签名客户端或真实下单的参数。策略预测资产、可交易资产、实际敞口仍是不同集合；此处两资产 fixture 不表示全部比赛资产和账户风险基础设施已完成。

历史存储、任意动态查询、真实账户、量化风险及生产执行恢复仍按 PR3–PR8 实现。Jev、新闻、市场事件扫描器未实现。本 PR 不部署 VPS、不自动合并。

## 入口与文件

| 职责 | 文件 |
|---|---|
| Core 主进程：单实例锁、恢复、定时器、消费 | `src/perpetual_alpha/runtime/http_core.py` |
| 有界 HTTP Server、服务端身份 | `src/perpetual_alpha/transport/http_server.py` |
| 禁止代理和重定向、有限超时的 HTTP Client | `src/perpetual_alpha/transport/http_client.py` |
| 独立 Dummy Worker、提交持久化、续租与反馈 | `src/perpetual_alpha/transport/dummy_worker.py` |
| 固定 fixture 的精确请求及额度事务 | `src/perpetual_alpha/transport/fixture_gateway.py` |
| 冻结 Decision/配置、执行前 fencing、Mock 回报对账 | `src/perpetual_alpha/runtime/mock_consumer.py` |
| 合成配置和输入 | `config/mock-http.toml`、`runtime/mock_config.py`、`runtime/mock_fixture.py` |
| SQLite 增量迁移 | `runtime/migrations/002_mock_http.sql` |
| 双容器 | `deploy/Dockerfile.mock-http`、`deploy/compose.mock-http.yaml` |

HTTP 与 Worker 共用协议库，但运行在不同进程。Worker 只通过 HTTP 访问 Core，没有其数据库路径、挂载或查询接口。

## HTTP 契约

沿用 `agent-transport/0.1`，请求和响应均为 PR0 DTO 的完整 JSON（包括版本字段），不接受额外身份字段。Job ID 在 URL 中百分号编码；POST URL 与 body 的 lease.job_id 必须相同。

| 方法 / 路径 | 输入 / 输出 | 成功码 |
|---|---|---|
| POST `/v1/jobs/claim` | ClaimRequest / ClaimResponse | 200；无任务 204 |
| POST `/v1/jobs/{id}/heartbeat` | HeartbeatRequest / HeartbeatResponse | 200 |
| POST `/v1/jobs/{id}/data/query` | DataQueryRequest / DataQueryResponse | 200 |
| POST `/v1/jobs/{id}/forecast` | ForecastSubmission / SubmissionResponse | 新接受 202；重放 200 |
| POST `/v1/jobs/{id}/complete` | CompleteRequest / CompleteResponse | 200 |
| GET `/v1/jobs/{id}/feedback` | FeedbackResponse | 200 |
| GET `/healthz` | HealthResponse（不含内部信息） | 200 / 503 |

除了 healthz，均要求 `Authorization: Bearer <worker-token>`。token 是服务端可信 Principal 的映射，与租约 token 独立。没有注册 Worker、切换控制版本或订单的 HTTP API。启动凭证文件是显式 allowlist；移除的 Worker 在数据库中禁用。修改文件需重启 Core，正在使用旧身份的 Worker 必须按运维流程停止/更新。

错误返回 ErrorResponse。身份缺失/错误 401、权限 403、未知资源 404、ID/租约/旧结果冲突 409、到期 410、预算/限流 429、暂不可用 503，其余无效消息 400。错误 body 使用固定说明，不回显 token、请求数据和内部异常。HTTP 202 仅表示持久接收，必须继续查反馈，不能据此判断风控通过或模拟执行成功。processing.completed 也要结合 Mock report 的具体状态判断（可能是明确拒绝）。反馈沿用 PR0 的 Decision/Verdict/Report 引用，不另扩展业务协议。

服务限制：测试默认每身份 40 请求/秒、8 并发请求、512 KiB 请求、32 层 JSON、5 秒整次连接处理时限；超过并发直接断开，客户端按未知响应重试。禁用 chunked body，要求单个 Content-Length 和 application/json；重复 JSON key、非法 UTF-8 等使用 PR0 校验。每个请求关闭连接。健康检查只检查 Core 循环/暂停标志，不声称外部服务就绪。

本机默认绑定 127.0.0.1。Compose 内部使用 0.0.0.0，无端口发布且 `internal: true`；没有 TLS 的 HTTP 只用于可信隔离网络。未来跨 VPS 必须通过经过认证的 TLS 反向代理或私有加密通道，不将明文 bearer 暴露到公网。Client 支持 HTTPS（标准证书验证）、不使用环境代理、不跟随重定向。

## 数据边界

Worker 只请求预先声明的 `fixture-quotes`（source=`fixture/par`），请求字段、参数、资产和时点必须与冻结 manifest 完全一致。没有 URL 抓取、SQL、路径读取或任意数据源。响应来自服务器保存的不可变 manifest，事件/接收时间不晚于 asof；摘要由服务器对该 fixture 的 request、dataset、asof 计算，重试返回原响应。该摘要格式和算法仅适用于 fixture，不作为 PR4 的通用数据真实性协议。

查询计数/返回字节预算跨该 Job 的全部 attempts 累计，在同一 SQLite 事务中校验并保存响应；相同 attempt/request_id/内容的重试不重复计费。响应丢失可重试，同 ID 换内容冲突。Worker 重启也使用同一 query ID。租约过期不能借旧缓存绕过权限。

Dummy 根据 mark 与 oracle 的比较输出方向，引用 fixture dataset；不推导真实收益、概率或校准值。synthetic account 的观察时点是场景设定，不用于历史评估，不声称 Core 在现实中的 asof 已收到该账户信息。不得把这批 fixture 用于回测/收益评价。

## 并发、幂等及恢复

1. 保留 PR1 的单 Worker 活动任务与同控制范围串行政策。HTTP 调用各有短事务连接；Scheduler/Mock Consumer 在 Core 主线程执行。双重内核锁覆盖 runtime DB 和 Mock journal（本地磁盘；不支持 NFS），活跃 Core 未退出不得启动第二个同状态实例。正常关闭等待有界 HTTP 请求结束后才释放锁。
2. `JobQueue.submit()` 仍在一个 SQLite 事务内重读身份、lease、epoch、水位并原子保存 Submission、终态及 Outbox。新增可选返回重放标志只用于 HTTP 状态码，旧调用方返回值不变。
3. Worker 在发送前原子写入 claim request ID；计算出提交后先 fsync 保存完整 Submission，再发送。提交超时/进程退出都保留原始 ID 与内容，重启先查反馈；已接受就等原 Processing，绝不生成替代 Forecast。没有任务的 204 也会重放，下一次新轮询必须使用新 request ID。
4. 续租独立于数据读取和 Dummy 推理。网络错误有限超时并退避至最多 5 秒；到期/失去租约先查反馈，未被接受才放弃该 attempt，之后领取由 Core 决定。权限或内容冲突直接停止供检查，不清空提交绕过错误。
5. Outbox 取出和执行前分别重查控制版本、接受/消费水位、输入年龄和 Forecast/Decision 到期。在 Core 写事务保护期间仅调用**本地 Mock journal**，使控制切换与 Mock 副作用有明确串行顺序；没有外部网络调用持有该锁。
6. 执行前在 runtime DB 保存稳定 Decision ID、完整 Decision、输入和不可变模拟配置版本。重启时已有计划沿用原配置和 Decision；同配置 ID 改内容拒绝启动，需要显式换新版本。未执行的过期计划阻断，不重生成更新 Decision 来延长生命周期。
7. 两个 SQLite 文件不能形成一个原子提交。恢复顺序是先读取并核对既有 Mock Decision/report，再 recover Outbox。若 Mock 执行已持久化但 Processing 未更新，即使后来过期或 epoch 已变化，也记录该既有执行事实，而不是再次执行或伪报未发生。Mock 自身事务将持仓、Decision 与 report 一并提交。
8. unknown/非终态 Mock report 不重发；Core 暂停定时任务及新领取，healthz=503，保留反馈访问，等待人工检查。这里没有真实订单对账/自动恢复权限，不能据此推断 PR8 的真实执行恢复已完成。

持久化数据含内部租约/账户 fixture，限制目录读写权限；日志不打印凭证和租约。示例未添加保留期清理，不能长期无限运行后再盲删幂等记录；生产保留、归档及空间监控仍需后续运维设计。

## 本机运行（两个终端）

在仓库根目录，先生成**仅本次测试使用**的凭证到忽略目录（不是交易所 key）：

```powershell
@'
import json,secrets
from pathlib import Path
p=Path('.cache/http-demo'); p.mkdir(parents=True,exist_ok=True)
t=secrets.token_urlsafe(48)
(p/'worker-token').write_text(t,encoding='utf-8')
(p/'core-auth.json').write_text(json.dumps([dict(token=t,worker_id='dummy-local',producer_id='dummy-agent',producer_version='offline-0.1',role='control')]),encoding='utf-8')
'@ | .\.venv\Scripts\python.exe
```

终端一：

```powershell
.\.venv\Scripts\python.exe -m perpetual_alpha.runtime.http_core --credentials .cache/http-demo/core-auth.json --database .cache/http-demo/runtime.sqlite3 --journal .cache/http-demo/mock.sqlite3
```

终端二：

```powershell
.\.venv\Scripts\python.exe -m perpetual_alpha.transport.dummy_worker --token-file .cache/http-demo/worker-token --state .cache/http-demo/worker.json --once
```

`--once` 等待一个已领取任务的终态反馈后退出；不指定则持续被动轮询。中途退出保留 state；不要为了重试删除状态文件或数据库。测试参数由 config/mock-http.toml 明确设置，不加载 .env。两个入口支持 `--config`。同一 schedule/epoch 下改变调度定义会被 PR1 拒绝，须换 schedule ID/提高控制 epoch；提高控制 epoch 不能降低或清空水位。

## Docker 双容器与 CI

有可用 Docker Linux daemon 时，在根目录执行：

```text
docker build -f deploy/Dockerfile -t perpetual-alpha-runtime:ci .
docker build -f deploy/Dockerfile.mock-http -t perpetual-alpha-mock-http:ci .
python scripts/validate_mock_http_containers.py
```

验收脚本创建随机命名的隔离 Compose 项目、临时合成 token、独立 volumes，启动 Core 和只运行一轮的 Worker，检查终态 Feedback，然后重启 Core 容器核对唯一 Mock Decision 仍存在。脚本最终只移除该临时项目及测试 volumes，不涉及实际数据。

需要保留模拟数据长期人工演练时，可使用 compose.mock-http.yaml：设置 `MOCK_HTTP_SECRET_DIR` 指向上述两份合成凭证目录，`MOCK_HTTP_IMAGE=perpetual-alpha-mock-http:ci`，然后自行指定测试 project 名执行 `docker compose -f deploy/compose.mock-http.yaml up`。两个服务不共享数据库或状态 volume；secret 逐服务挂载；容器只读根文件系统、无额外 capability、无主机 Docker socket。

既有 CI 离线回归后新增真实双容器验收。本机 daemon 不可用时不将“写了 Compose”记为容器通过，最终以该提交 CI 的实跑结果为准。Windows 回环测试可能需允许 Python 访问本机端口。

## 测试、迁移与回滚

`tests/test_http_transport.py` 使用真实 TCP 和 SQLite，包括：身份/越权/撤销、重复提交并发、提交已落库但 HTTP 超时、查询去重与并发额度、迟到/过期结果、消费水位、控制切换（含计划已冻结后的切换）、完成无预测、JSON 限制、限流/暂停、重定向拒绝；真实 Core 重启、Worker 在提交成功后硬退出并恢复；Mock 提交后反馈落库前硬退出；执行前恢复保留原 Decision/配置；锁互斥及 schema v1→v2。

```text
python -m unittest discover -s tests -v
python -m ruff check . --no-cache
python scripts/validate_schemas.py
python scripts/validate_runtime.py
```

runtime schema 升到 2，仅新增三张表：mock_configurations、mock_plans、fixture_queries。原 Job/Submission/Outbox 表及幂等语义保留。迁移在 PR1 同一 BEGIN IMMEDIATE 中执行，并测试 v1 的审计数据保留。Mock journal 格式未修改。

升级前停服务并备份 runtime 与 journal 的一致 SQLite 快照（包含 WAL 的正确 backup，不直接热拷贝单一主文件）。回滚先停止两个新入口，保留数据库和 Worker spool；原 collect/dry-run 仍可使用独立文件。PR1 拒绝打开 schema 2，不自动降级、不删表或清库；若需运行 PR1 runtime，应使用停机前的备份并处理回滚期间已接受/执行事实，不能自动重放交易。数据库硬件损坏/两个 volume 不一致时停止人工核查，不承诺跨库 exactly-once。

审核后下一步仍是 PR3 历史数据层，然后 PR4 动态 Gateway；不因 Dummy 闭环成功而授权无人值守交易。
