# PR3：Historical Market Data Layer

## 范围与当前能力

PR2 已合并，本 PR 从 main 的 `c4a9ad2` 开始，落实开发基线 A09/A10/A16：历史 candle 的实际可获得时间、版本/修订、十资产 metadata、来源/单位及质量。新增独立 `historical` 包和 SQLite 文件；不修改已有报价 snapshots、Runtime Job/Outbox、Mock Journal、Forecast 0.1、LT Adapter 和七份业务 Schema。

新增入口只调用公开的 testnet `/info`：`meta`（dex=par）、`candleSnapshot`（par: 前缀）。不读取钱包、.env、签名配置，不调用 `/exchange`。没有 Agent HTTP 历史查询入口，也不把真实历史接入 PR2 的合成执行链；多轮查询/权限/证据封存属于 PR4，风险估计属于 PR6。

| 模块 | 代码与职责 |
|---|---|
| 单位和约束 | `historical/contracts.py`：十资产目录、固定周期、Decimal 与 OHLC 校验 |
| 公开客户端 | `historical/client.py`：固定 HTTPS origin、无代理/重定向、有限超时/返回量/请求间隔 |
| 历史存储 | `historical/store.py`、`migrations/001_history.sql`：原始观察、追加版本、asof/快照读取与质量 |
| 有限分页 | `historical/ingest.py`：最多 20 请求、每页最多 500 bar、每页独立提交，不持锁请求网络 |
| 手动采集 | `historical/cli.py`：metadata / backfill / collect / query，独立于旧报价采集服务 |
| 实样与验收 | `examples/history/testnet-par-capture.json`、`scripts/validate_history.py`、`tests/test_historical_data.py` |

## 数据时点与可复现性

每次完整公共响应记录唯一 observation_id、原始 JSON、规范 JSON 的 SHA-256、真实 received_ms 和入库事务采样的 available_ms。传输失败不生成成功观察；已有原始响应、修订和无效内容不覆盖删除。没有使用旧 Journal 的固定 simulated=true 包装真实数据。

- candle `t` 是开盘毫秒；源 `T` 是该 bar 最后毫秒，统一输出 `close_ms=T+1`，bar 区间为 `[open_ms, close_ms)`。只接受精确周期边界。
- received_ms 是公开响应完整接收后的本机时间，**不等于收盘或发布时间**。来源没有提供可信 published time，输出 published_ms=null。
- available_ms 是获取入库写锁后的本机时间。它保守地覆盖已收到但等待入库的延迟，不伪称网络收到即已进入数据库。它是事务内采样时点，不是硬件级精确 commit 时间；SQLite 读事务只见已提交数据。
- 查询要求 close_ms、received_ms、available_ms 都不晚于 asof；后来下载的旧 bar 不能进入早于下载的 Job。任务中补数不能解决该冻结任务的 missing。
- 未收盘响应只留在原始 observation 中，不创建可读取的 candle 版本。等待时间过去也不会自动将它变成最终 bar，必须收盘后重新抓取。
- `snapshot_seq` 固定观察序列上界；与 asof、source、schema_version、请求窗口一起保存，用于之后完全重读。查询在单一 SQLite read transaction 中选快照和版本；CLI 的 metadata 与多个资产使用同一序号。
- 仅给定毫秒 asof 不能替代快照封存：同毫秒的后续提交、清洗/schema 升级都需要明确版本。PR4 必须将实际返回内容/摘要和快照绑定到 Job manifest，不能只记一条可重新运行的查询。

旧 bar 修订采用追加版本：同一 bar 最新观察内容不变则保留原 first available/received；A→B→A 则创建三个版本，不能按全局 content hash 去重成一个。first_seen_ms 表示该 bar 首次入库（可能先看到无效版本），当前 revision 有自己的 received/available/revision_id。旧响应晚入库不能覆盖已接收时间更新的响应。

`payload_digest` 为 candle 源内容和校验结果的版本指纹，`source_response_digest` 标识完整规范 JSON 响应；查询整体还提供内容摘要。保留源字符串精度，不使用 float。此处是 history/0.1 的本地契约，不修改 ForecastBatch/Transport 的摘要定义。

## 质量和覆盖

请求窗口按 open_ms 为 `[start_ms,end_ms)`，端点必须按周期对齐；最多 10,000 个预期 bar，返回预算 1–5,000。支持 1m/3m/5m/15m/30m/1h/2h/4h/8h/12h/1d。官方还支持其他周期，但本版明确拒绝 3d、1w、1M，避免错误的日历/周边界和隐式重采样。

| 情况 | 行为 |
|---|---|
| 缺失/迟到 | gaps 返回缺失的连续开盘区间；不补零、不前向填充、不当成零收益 |
| 未收盘范围 | 不返回 bar，not_yet_closed_records 指出窗口中尚未到收盘的数量 |
| OHLC/volume/count 无效 | 原始观察保留，bar 版本隔离；不能绕过坏修订回退到旧好值 |
| 重复/乱序 | 完全相同重复合并、排序并记质量统计；同一响应同 bar 的冲突重复隔离 |
| 跨资产/周期/请求范围 | 整页拒绝，不写入另一条时间序列 |
| 返回量预算 | truncated=true，不能声称 ready；available_records 与实际返回数量分别保留 |
| 完整旧窗口 | 可以 ready，不自动因为它是历史而 stale；调用方显式传 max_age_ms 才应用尾部新鲜度政策 |

状态为 ready/partial/missing/invalid/stale，并独立返回 gaps、invalid、truncated、expected/available 数量。页成功或 HTTP 200 不等于覆盖完整。无效响应不能让已存在数据静默变好；修正后的后续响应可产生新有效版本。

`meta` 观察保存完整原始响应（含 collateralToken/marginTables）及十资产规范视图。缺失资产不造规则，delisted 保留状态，缓存过期返回 stale。TTL 由读调用方明确提供；CLI 的 300 秒仅为查看时的演示设置，不是生产风控阈值。

## 十资产规则和单位

配置目录与当前比赛指南对应：BTC/ETH/HYPE/SP500/EWY/EWJ/BRENT/GOLD/USDEUR/USDJPY。目录限定读取范围，不等于策略必须预测或获得真实交易权限。

szDecimals、quantity_step、maxLeverage、delisted/isolated 和 raw rules 来自有版本的 venue metadata；杠杆上限不是推荐杠杆。数量步长不推出最小订单金额或价格 tick。

价格标注按目录版本 `competition-units/2026-09-30`：USDEUR 为 USD/EUR、USDJPY 为 JPY/USD；SP500 为指数点，其他标的按指南保留报价单位。USDPR 结算币与参考报价单位分别记录。volume 只标 venue-size，不把十资产都猜成 BTC 数量或美元成交额。

**valuation_multiplier=null、valuation_supported=false。** metadata 缓存不构成已验证的全账户估值适配器；后续 PR5–PR7 必须核对合约估值、FX 转换、保证金及完整账户敞口。不能将这里的参考单位直接用于美元风险预算。

## 真实 BTC/ETH 实样

2026-10-16 通过公开 testnet/par 查询了完整十资产 metadata 和 BTC/ETH 最近 120 个一分钟 bar 窗口。窗口 `[1792114140000,1792121340000)`；metadata received_ms=1792121397896，具体各响应时间与 digest 见捕获文件。

| 资产 | 预期 / 返回 | 质量观察 |
|---|---|---|
| par:BTC | 120 / 119 | partial，缺 `[1792121280000,1792121340000)` |
| par:ETH | 120 / 116 | partial，缺 `[1792121100000,1792121340000)` |

这是捕获时观察到的尾部缺口，原因未判定；不能声称这些分钟没有交易、一定是接口延迟或稍后一定可补回。metadata 当时返回全部十资产，但不代表所有资产具有相同长度的历史。

仓库保存完整 metadata 和每资产最后 8 条公开 candle，以及完整源响应摘要/条数、实际 received/available 时间、样本摘要。**该文件是解析回归样本，不是完整 120-bar 数据集。** CI 从样本离线导入，导入可用时间是 CI 本轮时间，不伪造样本在 CI 数据库中的历史可用性。

官方 candleSnapshot 只提供最近 5,000 根，分页不能突破来源保留窗口。空页、缺口和历史不足保持可见；本 PR 不引入 Binance，也不把外部历史标成 par 可成交历史。

## 手动使用

仓库根目录，有可用 Python 环境时：

```powershell
# 公开 metadata，一次性写入独立文件。
.\.venv\Scripts\python.exe -m perpetual_alpha.historical.cli metadata --database .cache/history-demo.sqlite3

# 一次 BTC/ETH 最近 120 bar 预热；与旧 collector、PR2 fixture 无关联。
.\.venv\Scripts\python.exe -m perpetual_alpha.historical.cli collect --database .cache/history-demo.sqlite3 --once

# 持续采集：默认 BTC/ETH、1m、120 bar 滚动重抓、60 秒轮询。
.\.venv\Scripts\python.exe -m perpetual_alpha.historical.cli collect --database .cache/history-demo.sqlite3

# 显式窗口的有限补抓；实际可用范围受来源限制，不能通过参数伪造 received。
.\.venv\Scripts\python.exe -m perpetual_alpha.historical.cli backfill --database .cache/history-demo.sqlite3 --symbols par:BTC par:ETH --interval 1m --start-ms 1792114140000 --end-ms 1792121340000

# 只读查询本轮已入库内容。历史 asof 早于补抓接收时间会返回 missing。
.\.venv\Scripts\python.exe -m perpetual_alpha.historical.cli query --database .cache/history-demo.sqlite3 --start-ms 1792114140000 --end-ms 1792121340000
```

query 可指定 `--asof-ms`、`--snapshot-seq`、`--max-records`；采集命令拒绝 asof/snapshot 参数，CLI 没有 received/available 时间覆盖选项。支持 `--symbols` 的十资产白名单和上述周期；时间跨度是明确的工程预算，不是交易参数。

collect 在每轮重抓滚动窗口，以观测其中迟到/修订；窗口以前的修订需显式 backfill，不声称发现来源的全部历史修订。轮询失败输出固定 error_type，保留已提交数据，最多退避到 300 秒；收到停止信号在有界公共请求/当前页结束后停止，不把中断记为完整覆盖。

单进程客户端每个公开请求至少间隔 1 秒、超时 15 秒、规范源响应最大 8 MiB；单资产最多 20 请求/轮，页最多 500 bar。工程默认值只用于只读采集初版，十资产/多周期并行时仍须统一评估官方 rate limit，不能为每个 Worker 各建一个无限补抓器。没有任务内补数入口。

## 存储、故障和回滚

历史文件拥有独立 application_id 和 schema version 1，包含 history_meta/observations/metadata_versions/candle_versions。发现已有报价库、Runtime DB 或未知版本即拒绝打开。没有对现有文件做迁移。

每页在一个 BEGIN IMMEDIATE 中提交原始观察与全部 bar 修订；硬退出未提交页整体回滚，已提交页保留。重跑同窗口内容不变不新建 candle 版本；Observation 仍保留本次实际请求记录。Network I/O 在写事务之外；写操作各自创建并关闭连接，无跨线程 connection。WAL、FULL sync、foreign keys、时间回拨拒绝和有界 SQLite timeout 沿用项目原则。CLI 用独立 writer 锁避免多个采集 daemon。

读取使用 `mode=ro`、query_only 和正常 WAL 协议，不使用 immutable。同机读取活跃 WAL 时必须正确提供数据库、WAL 和共享内存/目录访问条件；只复制活跃主 .sqlite 文件或简单只读挂载主文件不能保证安全/完整。当前验证是独立只读连接对已提交 WAL 的读取，**不是完整 VPS/跨容器只读挂载验收**，后续部署需要专门验证 SQLite sidecar 权限。

不共享 SQLite 到跨 VPS/NFS；跨 VPS Agent 仍通过 Core HTTP，不能直接挂历史文件。没有自动删幂等记录、压缩修订、checkpoint 运维或备份定时任务。长期存储容量、保留期、磁盘故障和备份恢复仍归 PR9。回滚停止新历史入口并保留该文件；旧报价和 PR2 服务入口未改动。备份应使用一致的 SQLite backup，不能热拷贝单个 WAL 主文件。

## 验收

新增 32 项离线测试，覆盖时间边界、迟到/补数、修订和隔离、缺口/截断、所有资产/周期、metadata TTL/缺失/退市、请求 scope、资源上限、只读 WAL、并发写、读期间写入、进程硬退出和分页网络超时。公开 HTTP 替换仅在网络边界；时点/版本/并发与崩溃验证均使用真实 SQLite。

```text
python -m unittest discover -s tests -v
python -m ruff check . --no-cache
python scripts/validate_history.py
python scripts/validate_schemas.py
python scripts/validate_runtime.py
```

旧 176 项测试保留，合计 208 项。CI 增加 `--network none` 的公开实样→历史库→独立只读连接验收，继续原有 Forecast/Schema、Dry Run/LT Adapter 和 PR2 双容器检查；CI 不请求真实行情，真实源只读实测与离线回归分别报告。

## 官方依据

- [比赛市场与报价单位](https://perpetual-alpha26.github.io/docs/#markets)：十资产、报价方向、结算与公开查询。
- [Hyperliquid candleSnapshot](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#candle-snapshot)：字段、周期、HIP-3 前缀及最近 5,000 根限制。
- [Hyperliquid perpetual metadata](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals)：meta 的 universe、精度、杠杆与 margin tables。

上述文档与固定 SDK 的请求实现已在本 PR 开发时核对；未来 API/比赛规则变化应单独更新版本与测试，不能静默重新解释旧数据。
