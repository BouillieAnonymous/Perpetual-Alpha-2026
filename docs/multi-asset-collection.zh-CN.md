# 十资产只读 WebSocket 采集

这是独立入口，不改变现有 `alpha collect`、配置文件、`snapshots`、`runtime` 或 VPS 服务。
默认订阅历史 catalog 已定义的十个 `par:` 市场，每市场 `l2Book` 和 `activeAssetCtx` 共 20 个订阅。
不创建钱包，不导入 Exchange，不发送订单。优先在新的本地数据库运行：

```powershell
python -m perpetual_alpha.stream_collection --database data/ten-assets.sqlite3 --seconds 180
python -m perpetual_alpha.collection_quality data/ten-assets.sqlite3 --output quality.json --rows observations.jsonl
```

省略 `--seconds` 为持续运行；`--symbols par:BTC par:ETH` 可缩小集合。
`--max-age-ms 10000 --max-skew-ms 2000` 为默认接收时间质量边界，可显式调整，不能据此声称源价格同步。
每次连接重新取官方 metadata（每小时主动更新连接），记录原始响应、精度和接收时间；记录断线、重新连接、订阅确认、pong、停止事件。
20 秒 ping、10 秒接收超时、2–60 秒重连退避；每次连接清空两路缓存。
旧连接的 Oracle 不会与新连接的盘口关联。事件与关联观察在同一 SQLite 事务提交。
接收超时也视为数据中断；不补造中断期间盘口。磁盘写入失败会使进程退出，应由未来运维告警处理。

## 时间与数据质量

记录盘口 `event_ms`、盘口接收时间、上下文接收时间、当前本地接收时间和连接 ID。
官方上下文没有 Oracle 上游源时间，因此 `oracle_source_event_ms` 和 `context_event_ms` 保持 null。
`receipt_fresh_source_time_unknown` 只表示本地接收时效通过，不能解释为 Oracle 源价格新鲜。
报价过期、接收时间偏差过大、深度无效、非有限数、盘口倒序均留下原始证据；无效关联行标记原因。
市场事件倒序或畸形消息会保存原始消息后重连。参考市场休市/节假日和上游停更仍需要独立证据。

质量报告逐资产列出有效/无效行、接收间隔、长缺口、5 秒接收网格覆盖、重复盘口时间、重复盘口内容、
盘口/上下文年龄、两侧深度层数。重复盘口内容可能是真实静止市场，不等于网络重复包。
事件驱动流的网格缺失不是可识别的丢包率，报告不把它称为交易所可用率。

## 兼容迁移与回滚

连接数据库时只执行 `CREATE TABLE/INDEX IF NOT EXISTS`，新增 `collection_events_v2` 与 `observations_v2`。
不对旧表执行 ALTER、UPDATE、DELETE，不回填或去重旧历史。新主键是 `(session, sequence)`，
因此同一 `(symbol,event_ms)` 下新的 Oracle 观察完整保留。旧表已经漏掉的观察无法恢复。
建议独立新库，避免与现有 writer 竞争；如选择旧库，必须由运维另行安排 writer 和磁盘容量。
回滚代码只需停止新入口并恢复旧入口；保留新增表与历史即可，旧程序忽略新表。禁止 DROP 表式回滚。
迁移、重启和重复身份事务回滚已有测试。只读导出在一个读事务中完成，`quick_check` 校验完整性。

## 负载边界与后续运行条件

串行 REST 十市场每五秒一轮约 132 次请求/分钟，按当前官方权重约 480/min（10×l2Book×2 + metaAndAssetCtxs×20，乘12）。
这不是 VPS 实测结论，还会共享 IP 配额、产生上下文年龄差和请求排队；本 PR 不采用这个扩容方式。提交期间 main 和远端已被并行工作改为十资产 REST 配置，本 PR 保留该配置，
仅提供独立 WebSocket 入口，不声称已对远端 REST 扩容完成压力验证或自动切换服务。
WebSocket 每连接只发 20 次订阅和每分钟约 3 次 ping，metadata 每重连一次，不做周期全量 REST。
官方连接/消息限制可能调整，上线前应重新核对共享 IP 流量。
此提交附短时公开订阅质量样本，以及离线写入 benchmark；它们不能代替 VPS 长期磁盘/网络/重连压力验证。

```powershell
python scripts/benchmark_stream_store.py observations.jsonl --database data/new-benchmark.sqlite3 --messages 10000
python -m unittest discover -s tests -p test_stream_collection.py
```

上线仍需单独授权：至少运行一个完整参考市场交易周期，评估每日增长、WAL、磁盘余量、断网恢复、
UTC 与参考市场日历、Oracle 源新鲜度及告警。此次没有修改、重启或部署任何 VPS 服务。

依据：[官方订阅](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions)、
[限流](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)、
[比赛市场和单位](https://perpetual-alpha26.github.io/docs/)，核对日期 2026-10-18。
