# PAS 公共行情采集

2026-10-19 用户授权启动 PAS 采集，策略留待后续核对，不启用交易。独立 `venue_collection` 只访问固定 Hyperliquid Testnet `/info` 的 metaAndAssetCtxs、l2Book 和 fundingHistory，不读取钱包、账户或环境凭据，不包含交易请求。

十市场：BTC、ETH、HYPE、SP500、EWY、EWJ、BRENT、GOLD、USDEUR、USDJPY，均使用 `pas:` 前缀和 `hyperliquid-testnet/pas` 来源。元数据与上下文每轮读取，逐市场读取完整返回盘口；循环末等待 10 秒，因此实际间隔为请求耗时加 10 秒，不声称严格 10 秒同步采样。已结算 Funding 每 30 分钟抓取最近一小时，保留重叠和实际可得时间；这是前瞻公共数据采集，不是补齐长期历史。

原 REST 采集器固定 PAR 且空盘口触发 IndexError。本模块使用独立表及 schema/application_id，不改旧库。新响应逐次追加，即使 book time 未变仍保留新 context/Oracle 观测；无盘口/单边/未来/过期/交叉/错误市场分别标记质量，保留原始返回，不填造 bid/ask，不中断后面的市场。单市场请求超时记录独立错误后继续。保留请求、起始/接收/事务内存储时间、原始 JSON 和 SHA256。stored_ms 是事务内采样，不冒称精确提交时间；context 没有上游事件时间，不能把接收时间当 oracle 产生时间。

数据库身份绑定 dex、抵押品、市场集合与固定 endpoint；拒绝异库和跨市场重贴标签。每条写入 FULL/WAL，进程锁保证同一数据库单写者。所有解析与下单就绪判断仍须由未来策略入口独立完成；这里的 quote_ready 仅为最新双边报价的格式/年龄检查，不是资金或交易风险许可。

## 运行与检查

```bash
python -m perpetual_alpha.venue_collection collect --system pas --database /data/public.sqlite3 --poll-seconds 10
python -m perpetual_alpha.venue_collection status --system pas --database /data/public.sqlite3
```

部署使用 `deploy/Dockerfile.venue-collector` 与 `deploy/compose.pas-collector.yaml`，独立 Compose 项目 `alpha-pas-data`，唯一数据挂载为 PAS 专属目录，无密钥挂载、公开端口、账户查询和签名权限。容器 UID1000、只读根、cap_drop ALL、资源/PID/日志上限、开机恢复。重启继续追加原库；禁止将 PAR 数据复制到新目录冒充 PAS。

`status.alive` / 容器 health 表示循环心跳仍在更新；各市场 quote_ready 与 context_age_ms 独立展示。全部市场有有效双边盘口才代表此刻报价覆盖完整；空盘口市场仍持续采集其可用原始观测。异常数据不作为市场正常或无风险的证据。

停止新服务用独立 Compose 的 `stop collector`，保留数据库；启动恢复同一配置和身份。备份应使用 SQLite online backup，不能只复制活动库而漏掉 WAL。原 PAR Collector 与交易 runner 不在该 Compose 中，不被本次重启/恢复。

## 验证

8 项新增离线回归覆盖空/坏盘口、单市场失败隔离、重复 book 时的新观测、正确 public-only 请求、身份隔离、异库拒绝与重启保留。最终 Linux 回归与实际 PAS 采集数量/时间范围见 PR 和部署验收记录。本模块不修改 Funding-LAST 政策或任何策略启用开关。
