# PAS 公共行情采集

2026-10-19 用户授权启动 PAS 采集，策略留待后续核对，不启用交易。独立 `venue_collection` 只访问固定 Hyperliquid Testnet `/info` 的 metaAndAssetCtxs、l2Book 和 fundingHistory，不读取钱包、账户或环境凭据，不包含交易请求。

十市场：BTC、ETH、HYPE、SP500、EWY、EWJ、BRENT、GOLD、USDEUR、USDJPY，均使用 `pas:` 前缀和 `hyperliquid-testnet/pas` 来源。元数据与上下文每轮读取，逐市场读取完整返回盘口。用户随后批准降低为每分钟一轮：默认和部署参数均为 60 秒，单调时钟安排下一轮起点，网络耗时不再额外叠加到周期；超过一个周期时跳过错过的时钟槽，不补抓或密集重试。各市场依次请求，不声称十市场同一时刻的原子快照。已结算 Funding 每 30 分钟抓取最近一小时，保留重叠和实际可得时间；这是前瞻公共数据采集，不是补齐长期历史。

原 REST 采集器固定 PAR 且空盘口触发 IndexError。本模块使用独立表及 schema/application_id，不改旧库。新响应逐次追加，即使 book time 未变仍保留新 context/Oracle 观测；无盘口/单边/未来/过期/交叉/错误市场分别标记质量，保留原始返回，不填造 bid/ask，不中断后面的市场。单市场请求超时记录独立错误后继续。保留请求、起始/接收/事务内存储时间、原始 JSON 和 SHA256。stored_ms 是事务内采样，不冒称精确提交时间；context 没有上游事件时间，不能把接收时间当 oracle 产生时间。

数据库身份绑定 dex、抵押品、市场集合与固定 endpoint；拒绝异库和跨市场重贴标签。每条写入 FULL/WAL，进程锁保证同一数据库单写者。所有解析与下单就绪判断仍须由未来策略入口独立完成；这里的 quote_ready 仅为最新双边报价的格式/年龄检查，不是资金或交易风险许可。

## 运行与检查

```bash
python -m perpetual_alpha.venue_collection collect --system pas --database /data/public.sqlite3 --poll-seconds 60
python -m perpetual_alpha.venue_collection status --system pas --database /data/public.sqlite3
```

部署使用 `deploy/Dockerfile.venue-collector` 与 `deploy/compose.pas-collector.yaml`，独立 Compose 项目 `alpha-pas-data`，唯一数据挂载为 PAS 专属目录，无密钥挂载、公开端口、账户查询和签名权限。容器 UID1000、只读根、cap_drop ALL、资源/PID/日志上限、开机恢复。重启继续追加原库；禁止将 PAR 数据复制到新目录冒充 PAS。

`status.alive` / 容器 health 表示循环心跳仍在更新；各市场 quote_ready 与 context_age_ms 独立展示。全部市场有有效双边盘口才代表此刻报价覆盖完整；空盘口市场仍持续采集其可用原始观测。异常数据不作为市场正常或无风险的证据。

停止新服务用独立 Compose 的 `stop collector`，保留数据库；启动恢复同一配置和身份。备份应使用 SQLite online backup，不能只复制活动库而漏掉 WAL。原 PAR Collector 与交易 runner 不在该 Compose 中，不被本次重启/恢复。

## 验证

8 项新增离线回归覆盖空/坏盘口、单市场失败隔离、重复 book 时的新观测、正确 public-only 请求、身份隔离、异库拒绝与重启保留。最终 Linux 回归与实际 PAS 采集数量/时间范围见 PR 和部署验收记录。本模块不修改 Funding-LAST 政策或任何策略启用开关。

## 2026-10-19 部署验收

- 运行代码：`f228822fef7a8f90924b10d881e1468839864564`；镜像 `perpetual-alpha-pas-data:f228822`，ID `sha256:a5341fe1064d311dedf48fef439edc39597eddc15b5f1f8941818f6c8b459fff`。
- 服务：`alpha-pas-data-collector-1`；数据库：`/opt/perpetual-alpha-pas-data/pas/public.sqlite3`，不复用 PAR/Paper/策略账本。
- 首个上下文收到时间 `1792417392948` 毫秒 UTC；初轮 10 个盘口、10 个上下文、10 次 Funding 请求及 1 次完整 metadata/context 响应均落库。随后观察到超过 100 条记录、多个连续采集轮次，十市场报价均 ready，容器 running/healthy。
- VPS 相同固定运行环境的 417 项完整离线回归通过，新增 8 项全部通过；Ruff/diff 通过。构建与回归日志存于 `/opt/perpetual-alpha-pas-data/evidence/`。
- 容器只有一个 `/data` 挂载，无 env/钱包/密钥/账户挂载；没有启用或恢复 PAS 策略，也没有发送订单。原 PAR 服务未重启，旧 PAR 空盘口异常仍是独立待办。
- 本服务是独立入口；旧 `config/systems/pas.toml` 的预留标记不控制这个服务，也不提供交易授权。

停止命令：在该 release 目录显式传递 PAS_COLLECTOR_IMAGE/PAS_COLLECTOR_DATA，执行 `docker compose -f deploy/compose.pas-collector.yaml stop collector`；保留数据库。启动使用相同配置 `up -d collector`。本次没有合并 PR，也没有清理旧数据。
