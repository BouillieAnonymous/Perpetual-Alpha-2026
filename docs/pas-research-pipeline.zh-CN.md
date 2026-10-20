# PAS Research Data Pipeline

独立、离线、只读提取的研究入口：`perpetual_alpha.pas_research`。基线为 main
`560caff4eb8ba2dbc6af5d90c7f3c7b8ee806188`。复用 [公共采集器](pas-public-collection.zh-CN.md)
的 identity、observations、SHA256、十资产列表及盘口质量检查，不修改 Collector、部署文件、交易执行或 Funding-LAST。
没有新依赖、网络请求、账户查询、钱包、订单、Agent、LLM 或常驻服务。

## 数据流与权限

`PAS public.sqlite3 → samples / funding 派生 SQLite → UTC 分钟 panel / 日统计 / Markdown`

原库使用 URI `mode=ro`、`query_only=ON` 和一个一致读事务，识别 WAL 中已经提交的数据；不调用
`Capture` 初始化原库，不 checkpoint、修改 journal_mode、清空或迁移它。SQLite 自身可能更新 WAL
共享内存读者标记；测试证明原 DB/WAL 字节及记录不变。不要对活动 WAL 库使用 `immutable=1`，也不要只复制
主文件而遗漏 WAL。长读事务会暂时阻止旧 WAL 回收，大库研究优先使用运维提供的 SQLite online backup
完整快照，并将其放在独立只读目录。这不是要求现在操作 VPS，本 PR 全部使用合成数据。

派生库独立、可丢弃；禁止与原库及其 WAL/SHM/journal 同路径或硬链接，也拒绝已有异种数据库。
增量以同一个原库绝对路径和完整 PAS 身份绑定。换路径、源库截断/改写、管线源码改变时，应在新路径重建
派生库，不覆盖旧库或自动删除任何文件。对移动后的同内容快照也须重新构建，避免隐式换源。

## CLI 与完整复现

在新 checkout 根目录、安装已有锁定依赖的 Python 3.12.15 中执行；以下为 PowerShell：

```powershell
$env:PYTHONPATH = "$PWD/src"
# synthetic-v1 必须是新路径；fixture 明确拒绝覆盖已有文件
.\.venv\Scripts\python.exe -m perpetual_alpha.examples.pas_research_fixture --output .cache/synthetic-v1/public.sqlite3
.\.venv\Scripts\python.exe -m perpetual_alpha.pas_research update --raw .cache/synthetic-v1/public.sqlite3 --derived .cache/synthetic-v1/research.sqlite3 --batch-size 1000
# 重复执行 update，inserted=0；派生表不产生重复记录
.\.venv\Scripts\python.exe -m perpetual_alpha.pas_research update --raw .cache/synthetic-v1/public.sqlite3 --derived .cache/synthetic-v1/research.sqlite3
.\.venv\Scripts\python.exe -m perpetual_alpha.pas_research report --derived .cache/synthetic-v1/research.sqlite3 --date 2026-10-19 --output .cache/synthetic-v1/reports --label SYNTHETIC
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_pas_research.py -v
```

Linux 使用同样的 `python -m ...`，无需 Compose。读取真实数据时仅把 `--raw` 指向用户提供的 PAS 完整快照，
`--derived` 与 `--output` 使用独立研究目录；不要运行合成生成器覆盖真实库。本 PR 不获取 VPS 原始数据。
完整重建示例：仍读取同一 `--raw`，把 `--derived` 改为新的 `research-rebuild.sqlite3`；旧派生库保留。

日日期严格按 UTC，而不是本地时区。`report` 输出五个文件，覆盖同名报告文件以便确定性重跑，但禁止覆盖
输入库、派生库及其 SQLite sidecar；建议 output 永远使用专用报告目录。报告有调用者提供的 provenance label，
默认 `UNVERIFIED INPUT`，标签本身不证明数据真实。输出不含生成时钟或机器路径，浮点 CSV 保留 Python 精度。

| 文件 | 内容与时间口径 |
|---|---|
| `YYYY-MM-DD-panel.csv` | 十资产各 1440 行；UTC 分钟决策时点，独立 as-of 组件；空值输出为空单元格 |
| `YYYY-MM-DD-statistics.csv` | 十行日统计、样本数、质量与延迟、最长无效分钟段 |
| `YYYY-MM-DD-report.md` | 方法、十资产指标、局限及输入前缀/代码哈希 |
| `YYYY-MM-DD-observations.csv` | 当日 available_ms 范围的全部标准化原始观测；含异常、重复及全局元数据行 |
| `YYYY-MM-DD-funding.csv` | 当日可得的全部 Funding 响应明细版本；重叠和修订保留，seq 可联结 observations |

直接 Python 使用 `readonly`/`checked_derived` 时用 `contextlib.closing` 显式关闭连接。`panel(db,start,end)`
支持任意分钟对齐的半开时间范围；用于收益时应从所需起点前一分钟开始，再过滤掉预热行。CLI 日报告已这样处理。

## Schema

原库必须具有采集器 application_id `0x56434C31`、user_version=1 和完全一致的 PAS identity：source、dex、USDP、
十资产集合及固定 Testnet endpoint。PAR 即使有同名 ETH 也会被拒绝；混入 `par:*` 的行、错配请求、原始 payload
SHA256 不符、时钟字段非法，均使当前批次失败，游标不越过问题行，不默默丢弃数据。

派生库 application_id `0x50525331`、user_version=1，DDL 在模块的 `SCHEMA` 常量中：

| 表 | 主键与字段 |
|---|---|
| `state` | 单行 id=1；raw_path、原 identity、pipeline_hash、last_seq、prefix_hash |
| `samples` | seq（对应原 observations.seq）；source、symbol、kind、event_ms、requested_ms、received_ms、stored_ms、available_ms、raw_quality、quality、raw_sha256、row_sha256、bid、ask、mid、spread_bps、mark、oracle、context_funding |
| `funding` | (seq,item)，关联 samples.seq；symbol、event_ms、available_ms、rate |

一条接收观测对应一条 samples，不按内容哈希删除重复接收记录；完整原始响应仍保留在原库，派生只保存摘要和字段。
`request-error` 按原 request type 映射为 book 等类型，从而新的盘口请求失败会使旧报价失效。
`raw_quality` 保留采集器标签；`quality` 是标准化验证结果，例如交叉、空/单边、过期/未来、非有限报价、
无正数量或事件时间不一致。无效盘口的 bid/ask/mid/spread 全部 NULL，不从 mark/oracle 填成交价格。
元数据、context、Funding 观测保持独立记录；context.event_ms 通常 NULL，不伪造上游 Oracle 时间。

`pipeline_hash` 是模块 UTF-8 文本（统一换行）的 SHA256；输入前缀 hash 按 seq 顺序串接每条完整原始行的
canonical JSON 加换行。源码改变后必须新派生库重建，防止同库混用算法。每次更新流式重新校验已处理前缀，
再只标准化新增行。校验时间 O(原始记录数)，规范化时间 O(新增量)。批次行、Funding 明细和游标同一事务，
中断仅回滚未提交批次，重跑接续；已提交 prefix 被改写或缩短会拒绝接续。没有自动删除或 reset 命令。
原库快照读与派生写事务独立，派生库用 BEGIN IMMEDIATE 串行化写入；不锁原库写者。

## Point-in-time 规则

可得时间取 `max(requested_ms, received_ms, stored_ms)`，必须不晚于决策点。`stored_ms` 只是事务内采样，
不是精确 commit 时间，因此本管线证明的是**已记录接收/存储时标的因果过滤**，无法恢复过去某毫秒真实提交可见性。
对有严格交易延迟要求的研究，需要额外在线决策/提交证据；本管线不会猜出该时间。

每个 symbol 的 book/context 按 (available_ms,seq) 分别取决策点之前最新观测。盘口还需 event age 和 receipt age
均不超过 90 秒；context 的 receipt age 不超过 90 秒。不向后找较旧的“好报价”替代最新异常；无有效值就 NULL。
TTL 内 as-of 延用有明确时效，**不是同步快照或插值**，所有组件 seq、event/request/receive/store/available 时间、
原质量和处理后质量均导出。context 行在 Collector 中由 metadata/context 响应派生，其 requested_ms 等于该响应
received_ms；它不是独立网络请求起点。实际 metadata 请求耗时在原库全局 metadata-context 记录中，不能由
context 行反推。先收到 book、后收到 oracle，不得让后来的 oracle 改写先前决策点 basis。

Funding `context_funding` 仅为当前上下文观测，和 `funding_rate` 已结算费率分别命名。Funding 明细只接受同源、
有限费率、整数且不晚于收到时间的结算事件。重叠窗口保存全部版本；每个决策点仅从当时已可得版本中，
按 (available_ms,seq,item) 选同一结算事件最新版本，再取最新结算事件。超过 2 小时保持 unknown。
`funding_change` 仅比较相邻且相差恰好一小时的两个已知结算事件，缺小时不补零；不把费率当现金盈亏。

## 指标与样本口径

- mid=(bid+ask)/2；spread_bps=(ask-bid)/mid×10000；basis_bps=(mid/oracle−1)×10000。
  basis 是有年龄限制的异步 as-of 配对，不是原子报价；非有限计算结果为 NULL。
- 收益是相邻 UTC 分钟决策点的 log(mid_t)−log(mid_t-1)。两端均需有效，新端 book 事件时间必须前进且 seq
  不同；重复事件、断档或异常切断收益链，不将跨缺口多分钟收益当作一分钟收益，不伪造零收益。数值仍只是
  有限时效 REST as-of 价格变化，实际事件间隔未必正好一分钟，也不是可执行策略收益。
- 历史波动率为日内有效分钟收益的样本标准差（ddof=1，至少两个收益）；realized_variation=√Σr²（至少一个）。
  不年化、不对缺口缩放、不将部分覆盖包装成完整全天 RV；报告样本数，缺数据为 unknown。CSV 不做展示性舍入。
- 均价差/basis 为有效分钟网格等权均值，另列各自 n，不按原始采集重复次数加权。分钟收益有相关性，不是 IID。
- 有效报价比例=有效分钟点/1440；报告 missing_quote_points（尚未收到）、unusable_quote_points（已有但异常/过期）、
  missing_context_points、最长连续不可用分钟数。部分天数据依然以全天为分母，明确反映覆盖不足。
- 每日 book_receipts 按 available_ms 在 UTC 日内计数，包含请求失败；invalid_book_receipts 和 quality_counts
  展示异常。请求延迟=received−requested，>5000ms 计 delayed_book_receipts；它不等于交易所事件传播延迟。
  panel 另列事件年龄、context 年龄。没有固定 collector 调度槽的原始记录，不能精确反推漏了多少次 HTTP 请求；
  用研究网格的缺失率描述覆盖，不能叫真实请求丢包率。
- 日 Funding count 按事件时间在当日、且日结束之前已经可得的唯一结算时点计数；重复窗口不增加事件数。
  收到但结算于前一天的历史仍在 receipt CSV，晚到的当日事件不会混入当日 as-of 统计。

## 示例与研究边界

已提交 [合成日报](research/pas-pipeline-example/2026-10-19-report.md)和
[合成统计 CSV](research/pas-pipeline-example/2026-10-19-statistics.csv)。固定 fixture 仅有 12 分钟、227 条记录，
包含 BTC 空盘口、GOLD 交叉报价、EWJ 三轮断档、BRENT 慢请求和 USDJPY 完全无数据。日分母仍为 1440，
所以其他市场即使局部有效也只有不到 1% 全日覆盖。USDJPY 的价格、波动率均 unknown。这些数字不是实际 PAS 表现。
按上方命令可以重建完整 14,400 行 panel 和 receipt/Funding CSV，不在 Git 中提交数据库或大量重复网格行。

设计参考 PR [#15](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/15) 的冻结输入/成本分解/
来源隔离，以及 [#20](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/20) 的因果前缀、连续样本与
不足样本报告方法；没有合并其研究分支或复用事后优选结果。未来研究需先冻结假设并保留真正新样本，
不能把本次开发数据当独立留出或把十资产×重复分钟当独立交易。

现有原始数据不足以恢复逐笔交易、队列、实际成交、盘中路径、Oracle 上游生成时间、精确事务提交时间、
长期历史、实际账户盈亏或费用后净 Alpha。无法据此证明可成交策略收益；不补猜测数据、不启用任何策略。

研究报告目前按日读取当天结束之前的历史序列到内存，便于严格 as-of 和 Funding 修订，内存/时间随历史增长；
适用于当前轻量只读研究，不是无限规模数据仓库。数据增长后可在保留相同时间语义下分页或分区，不能改用未来值填充。
