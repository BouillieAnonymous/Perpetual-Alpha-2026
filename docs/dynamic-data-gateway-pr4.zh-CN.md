# PR4：Dynamic Data Gateway

## 范围与兼容

在 PR3 已合并的 `fd650ee` 基础上，将独立历史库接到 PR2 的 HTTP `/v1/jobs/{id}/data/query`。Agent 仍被动领取一个 AnalysisJob，可在固定 asof 内请求不同资产、窗口和字段，再提交原 ForecastBatch 0.1。

保留原七份业务 Schema、13 种 Transport DTO、agent-transport/0.1、原 fixture 和同步 Forecast/LT Adapter。没有增加消息字段或改变旧预测/目标仓位语义。`DataQueryResponse` 使用 `schema_version=candle-gateway/0.1` 标识新的数据内容，旧 quote 使用自己的 fixture schema。

本 PR 提供历史 Shadow 演示，使用 Dummy、真实只读历史库接口和两个独立进程。该入口不运行 Portfolio/Execution；没有真实账户、LLM、交易所订单或 VPS 部署。原 PR2 Mock 模式仍保留。新闻/Jev/市场事件扫描器未实现。

## 实现入口

| 文件 | 职责 |
|---|---|
| `transport/data_gateway.py` | 服务端 capability、固定历史快照、多轮查询、跨 attempt 缓存、质量检查和证据封存 |
| `runtime/migrations/003_gateway.sql` | Policy、Job binding、Dataset、Read alias、Accepted manifest 五张表 |
| `runtime/jobs.py` / `scheduler.py` | 创建 Job 时原子保存 binding；提交事务重检并封存有效 manifest |
| `runtime/outbox.py` | 优先消费已接受的不可变 manifest，不重新向历史源取数 |
| `historical/store.py` | 在原 read transaction 内校验固定 observation 锚点；历史 schema 仍为 1 |
| `runtime/http_core.py --history-database` | 可选只读历史、Shadow 定时 Job，使用独立 Runtime/Mock 文件 |
| `transport/dummy_worker.py --history` / `history_worker.py` | 独立 HTTP Worker，两个窗口查询，落盘后提交/重试 |

## 请求和授权

历史数据使用原 `DataQueryRequest` / `DataRequest`，例如：

```json
{
  "dataset_id": "btc-last-five",
  "source": "hyperliquid-testnet/par",
  "kind": "candle",
  "symbols": ["par:BTC"],
  "fields": ["close", "volume"],
  "lookback_ms": 300000,
  "max_records": 100,
  "max_age_ms": 600000,
  "required": false,
  "parameters": {"interval": "1m", "end_ms": 1792121100000}
}
```

完整 HTTP body 还包含 request_id、lease 和 transport_version；上面的 end_ms 只是显式历史窗口示例，不能直接拿它当当前数据。

DataPolicy 是 **Core 可信配置**，不是来自 Agent 的身份声明。它绑定 producer_id/version/role、policy_id、读取资产、周期、字段与 lookback 上限。预测资产由 Job.symbols 继续约束，读取辅助资产不会扩大 Forecast 输出范围。

Gateway 仅允许 source=`hyperliquid-testnet/par`、kind=`candle`。fields 只能为 open/high/low/close/volume/trade_count 的授权子集；parameters 必须恰好是 interval/end_ms。时间窗口是 `[end_ms-lookback_ms,end_ms)`，按周期对齐，end_ms 不晚于 Job.asof_ms。禁止 URL、SQL、文件路径、数据源覆盖和任意参数。

默认历史 Shadow capability 允许十资产、1m/5m/1h、上述六字段、最多 6 小时 lookback；max_age_ms 也不超过该上限。它是有界演示能力，不是生产风险/数据新鲜度政策。真实策略的读取授权和业务新鲜度应显式配置版本后再接入。

Bearer 映射可信 Worker 身份，lease 只证明该 attempt 的任务所有权。查询前与提交结果前均重新检查身份启用、Worker/Job 归属、lease/token/attempt、控制版本、capability、输入年龄和分析时间预算。Policy 内容变更必须换 policy_id；当前 Policy 与 Job 创建时的 Policy 不同则阻止查询及新 Forecast 接受。已接受的 Receipt 仍按原幂等规则重放，不通过撤销权限伪造未发生的接受事实。

## 时点、快照与版本

Job 创建时，Core 在历史库的 read transaction 中固定：asof_ms、snapshot_seq，以及该序列观测的 anchor。随后与 Job 在 Runtime 事务内保存。Scheduler 的历史读取/绑定准备不持 Runtime 写锁；不可在任务中在线补抓。

每轮查询都使用该 snapshot_seq 和 anchor，在同一历史 read transaction 内校验并读取。仍需满足 close_ms、received_ms、available_ms≤asof。Job 创建后即使在**同一毫秒**发生新的历史入库，也因序列上界被排除；换库/截断导致锚点不符时拒绝创建新响应，不静默读另一份历史。锚点是对可信 append-only 数据库的换库防错检查，不是整个文件的抗恶意篡改签名。

每个新 Dataset 将完整响应、请求定义、历史 read 结果、修订 ID、来源摘要、快照序号、gap/invalid/truncated/freshness 保存在 Runtime DB。后续查询和重启返回已经封存的内容，不对相同 Dataset 重新查询变化的数据库。

Forecast 接收事务会：验证原始 manifest 未变；加载当前 attempt 成功封存的响应；核对请求、响应及 provenance 摘要；合并到有效 Context；复用原 Ingress/Forecast 校验；原子保存 Submission、Job 终态、Outbox 和 accepted_manifest。accepted_manifest 同时保存 Job 的 binding 与具体数据内容，不能仅靠 asof 重新跑查询。

Outbox 消费封存的 accepted_manifest，检测摘要不匹配时停止，不退回原始空 Context。旧非 Gateway Job 仍按旧 manifest 路径运行。历史库与 Runtime DB 之间没有跨库原子事务；只读快照准备失败不产生 Job，响应准备后进程退出/lease 改变则重新授权，不将未提交的响应当成已授权证据。

## 幂等、attempt 和竞争

- Dataset ID 在 **Job 全生命周期**内绑定唯一完整 DataRequest，不能换窗口、资产、fields、required 或新鲜度。同 ID 换内容为 ID_CONFLICT。
- request_id 按 Job/attempt 绑定请求定义；同 ID 重试返回原内容，不重复扣数据预算。不同 ID 的缓存重读也可返回同一 Dataset，但有独立 alias 上限，防止无限写入。
- 新 attempt 可复用 Job 已封存的 Dataset，但必须用自己的有效 lease 重新请求，生成该 attempt 的 read 记录；未读取的旧 attempt 证据不能引用。不能换 request ID 绕过跨 attempt 的数据总预算。
- 本地历史读取位于两次短 Runtime 事务之间。最后一次事务重新校验 lease/epoch/policy/终态及最新预算，再原子写 Dataset 和 Read。两个并发新响应不能分别通过预检后突破总限额。
- submit/query 使用同一 Runtime 写锁提交。如果 submit 先完成，未封存的查询不得补进已接受 manifest；如果 query 先完成，submit 看到完整响应。任务终态后，即使旧查询重试也不再授予读取权限。
- Worker 保留原 PR2 spool：先保存 claim ID、完整 Forecast Submission 后发送；查询轮次 request ID 和 Dataset ID 可从 Job/round 重建。某轮已封存但响应丢失/Worker 死亡，重启后重试该轮，不重复收费，然后继续下一轮。

服务端只能证明响应已被授权并持久封存，无法证明网络对端已经阅读了字节或确实用它完成推理。Evidence scope 和内容摘要不等于模型质量证明。

## 质量：不能只检查总体 status

Gateway 对每个资产同时检查：原状态、gaps、invalid、truncated、未收盘数量、完整窗口和整体返回数量。只有所有条件通过才返回 ready 和 evidence_ref。

| 历史结果 | Transport 表达 | 可引用 |
|---|---|---|
| 全窗口完整、有效、未截断且满足新鲜度 | ready | 是 |
| 缺失 | missing，error 内有诊断 | 否 |
| 不完整/无效/总返回量截断 | error（或无记录时 missing），truncated 单独保留 | 否 |
| stale，同时可能还有缺口 | stale；error 中同时保留 gap/invalid/truncated 诊断 | 否 |

原 DTO 没有 partial 枚举，本 PR 不扩展旧协议；将不完整映射为非 ready。非 ready 的 error 是有界 JSON 诊断摘要（每资产最多首个 gap 与总数）；完整质量明细保存在 provenance。ready 响应不设置 error。DataRecord.event_ms 使用 candle.close_ms，received_ms 保留真实源接收时间，available/revision 等保存在封存的 provenance 中。

units 逐资产/字段标注：价格沿用 PR3 的具体报价单位，volume=venue-size、trade_count=count。不得把 JPY/USD 当 USD/EUR，也不推断合约估值乘数。

探索查询建议 required=false；缺失仍可据此输出 abstain 或提交其他已就绪证据。required=true 继续保持原语义：该 Dataset 不 ready 时不能通过 Forecast Ingress，可使用原 complete(no_forecast/failed)。Forecast 引用非 ready、未读取、其他 Job 的 Dataset 都失败。旧协议允许预测不带 evidence，此项没有被全局改写为新的业务规则。

`payload_digest` 为 canonical(response 去除 request_id、payload_digest) 的 SHA-256；只适用于 candle-gateway/0.1。request_id 是传输相关字段，alias 改变它不会改变数据内容身份。数据库另保存完整 response+provenance 摘要，并在缓存重放与 Forecast 接收时复查。原 fixture 的摘要算法不变。

## 预算

沿用 JobLimits：新 Dataset 数量≤max_queries、单响应记录总数≤max_records_per_query、逻辑数据总字节≤max_bytes。跨 attempt 累计，fixture 与历史查询合并计费。历史字节计入完整 response、DataRequest **以及持久化 provenance**，不只计算裁剪后的 HTTP body。

每 Job 的 query alias 记录最多 4×max_queries（工程防滥用上限）；精确重试不增加记录。窗口最多 10,000 个预期 bar（全部查询资产合计），单次 max_records 还受 PR3 的 5,000 上限约束；HTTP 沿用 PR2 的 body、深度、并发、速率及整连接时限。HTTP 网络重传不重复计数据预算，不能据此认为网络带宽无限。

这些数字为已有测试预算和有界演示配置，不是生产策略的新鲜度、资本或风险阈值。保留策略、磁盘/WAL/性能压测归后续运维验收。

## 独立进程演示

先运行 PR3 只读 Collector 预热独立历史文件；数据必须在 Job 截止之前已入库。不要为了演示成功倒填 received/available。若刚预热就创建当前分钟的 Job，该 Job 可能因冻结时间更早而 missing；下一分钟的新任务才会看到新预热内容。

生成 **Shadow 专用**演示凭证到忽略目录：

```powershell
@'
import json,secrets
from pathlib import Path
p=Path('.cache/history-shadow');p.mkdir(parents=True,exist_ok=True)
t=secrets.token_urlsafe(48)
(p/'worker-token').write_text(t,encoding='utf-8')
(p/'core-auth.json').write_text(json.dumps([dict(token=t,worker_id='history-dummy',producer_id='dummy-agent',producer_version='offline-0.1',role='shadow')]),encoding='utf-8')
'@ | .\.venv\Scripts\python.exe
```

终端一（使用独立 Runtime/Mock 文件，历史文件预先由 PR3 创建）：

```powershell
.\.venv\Scripts\python.exe -m perpetual_alpha.runtime.http_core --history-database .cache/history-demo.sqlite3 --credentials .cache/history-shadow/core-auth.json --database .cache/history-shadow/runtime.sqlite3 --journal .cache/history-shadow/mock.sqlite3
```

终端二：

```powershell
.\.venv\Scripts\python.exe -m perpetual_alpha.transport.dummy_worker --history --token-file .cache/history-shadow/worker-token --state .cache/history-shadow/worker.json --once
```

Dummy 依次请求 Job 资产的 5 根、10 根一分钟窗口（结束点比 asof 的分钟边界早 3 分钟，max_age=10 分钟），比较 close 后生成纯演示方向。窗口/新鲜度只是离线/Shadow 演示，不是 LT 策略。缺失/不完整则 abstain，不伪造 Forecast；最终反馈为 SHADOW_ONLY，不生成任何 Mock Decision。

该入口拒绝带已有 control Job 的 Runtime 或带旧 Decision 的 Mock journal；默认 PR2 无 --history-database 时行为保持原样。历史文件仅 Core 读取，Worker 只有自己的 token 与 spool。实际 LT Agent 后续可按同一 DTO 发多轮请求，但模型、供应商和数据外发仍需单独确认。

## Docker 与验收

保留原 `python scripts/validate_mock_http_containers.py`。新增 `python scripts/validate_mock_http_containers.py --history` 使用相同隔离 Compose 的临时 override，在 Core volume 初始化**空历史库**，两轮 HTTP 查询均明确 missing，提交 abstain，检查 sealed manifest、重启后的证据及零 Mock Decision。它验证容器通信与缺失安全路径，不声称空库拥有真实历史。Worker 不挂历史或 Runtime 文件，测试网络无外网和公开端口。

本机独立进程测试则使用明确标记的合成已收盘 bar，验证 ready 路径：第一轮查询后硬退出 Worker，同时重启 Core，再由独立 Worker 继续完成两轮；重复读取不新增 Dataset/alias。PR3 已保存的公开实样及离线验收继续保留，不为本 PR 额外在线下载行情。

另以 PR3 本机已保存的真实公开历史做了实际 HTTP 验证：BTC 5 根、ETH 10 根均因捕获时间已超出演示新鲜度返回 stale，未获得 evidence_ref；Dummy 输出 abstain 并收到 SHADOW_ONLY，Decision 为 null。保留该真实质量结果，没有放宽新鲜度来制造 ready。该实测与合成数据的 ready/故障测试分别报告，不将旧捕获称为当前市场状态。

新增 27 项测试，包括以上进程/HTTP路径、两窗口/辅助资产、同毫秒入库排除、版本固定、换库锚点、stale+gap、截断、capability 与预测权限、撤销、跨 attempt 重读、query/submit 竞争、控制切换、并发额度、完整证据字节、alias 上限、篡改检测、query 提交后硬退出和 v2→v3 迁移。既有 208 项保留，合计 235 项。

```text
python -m unittest discover -s tests -v
python -m ruff check . --no-cache
python scripts/validate_schemas.py
python scripts/validate_history.py
python scripts/validate_runtime.py
```

## 迁移、回滚与剩余边界

Runtime schema 2→3 仅新增五张表；事务迁移保留旧 PR2 行，Historical schema 1、Mock journal 和旧业务数据格式不变。升级先停 Core 并备份一致 SQLite 状态。回滚停止新入口、保留 DB/spool/已接受事实；旧 runtime 程序会拒绝 schema 3，不删除新表或清库强制降级。需要恢复旧 runtime 时应使用停机前备份并核对期间已接受结果。

Gateway 不包含网络补抓或长历史积累能力。5,000 根上游保留限制仍有效；PR6 需要的长期历史必须通过持续 Collector 积累。当前 Shadow 服务复用本地可读 WAL 文件，完整 VPS/只读容器 sidecar 权限、磁盘故障、备份恢复、跨 VPS TLS 和持久 Intent/交易所对账仍按 PR8–PR9/独立授权开发，不能从本 PR 推导 exactly-once 真实执行保证。
