# PAS Funding-LAST 首次上线准备

状态：WAITING ACTIVATION。此变更不授权 PAS 签名、更改杠杆或下单。

## 集成与隔离

以 main `d2e9244`（已合并 #22/#23）为基线，合并 #21 `99736bf`。
保留 Collector、Research Pipeline、原始库和派生库；仅解决文档导航冲突。
独立 Compose `deploy/compose.pas-trading.yaml`，项目 `alpha-pas-trading`，
根目录 `/opt/perpetual-alpha-pas-trading`。不挂载 Collector/Research/PAR 数据库。
默认只启动 preparation，禁用策略配置，只有公开账户地址，没有交易私钥。
未来 trader 在显式 activation profile 内，拥有独立 PAS 账本与专用 API signer。
PAR 已使用的 API 钱包不得直接作为 PAS signer。健康监控不挂载交易私钥。

## 冻结策略与限制

ETH Funding-LAST `/1`；每 12 小时，UTC 00:00 / 12:00 后的正常 15 分钟决策窗口。
采用决策时实际收到的最新已结算 Funding，最长允许 2 小时陈旧。
正值做空、负值做多、零值空仓；PAS 禁止 bootstrap。
最大目标及单笔名义 25 USDP，预算同时不得超过权益 5%；不向上凑交易所最小额。
1× Cross；保留至少 90% 权益的真实可用保证金。
最大亏损/回撤各 5 USDP，重启或恢复不重置；名义漂移超过 110% 停止。
价差上限 15bps、滑点 5bps、oracle 偏离 50bps、手续费上限 9bps。
盘口不超过 10 秒、快照不超过 30 秒；实际深度、双边买入能力和共享抵押品均检查。
反转先 reduce-only 平旧仓，确认成交和真实持仓后才开反向仓。
任何拒单、部分成交、未知状态、外部持仓变化均阻止继续追单。

## 上线步骤与独立授权

1. 合并前完成回归和 CI；VPS preparation 公开预检记录余额、真实可用保证金、
   所有共享抵押品 DEX、API 公开授权、费用、元数据、杠杆和盘口。
2. 准备并确认独立 PAS API 钱包授权及私钥归属；密钥仅放独立 secrets/pas.env，权限 600。
   本次不创建该文件、不复制 PAR 密钥。
3. **另行请求用户授权将 PAS ETH 从 20× 改为 1× Cross。**
   `python -m perpetual_alpha.funding_live.leverage` 仅接受 disabled PAS policy，
   leverage 用途审批文件及完整非杠杆预检，执行后重新读取 activeAssetData 和 clearinghouseState。
   未重新确认 1× 前禁止启动 trader。本次不运行此命令。
4. 配置通知并验证接收邮箱送达；目前只允许声称 outbox 持久化，不能将 SMTP 接受等同于送达。
5. **另行请求用户明确批准正式签名下单。** 审批文件只允许 trading 用途，绑定团队、
   独立 API 地址和 `pas-live.toml` policy hash，最长有效 24 小时。
   每次 signed action 前重新检查审批及交易所授权，过期停止；续期仍需人类批准。
   leverage 和 trading 审批不可互换。不得自动生成真实审批文件。
6. 启动 trader 后默认暂停；核对 generation，再显式 resume。PAS resume 不设置杠杆。
   只在下一正常决策窗口执行算法；市场检查失败时可以没有首单。
   首次真实签名拒单、成交与持仓一致性验收仍需实际执行后判断，离线测试不能替代。

审批文件 schema 为 `pas-operator-approval/1`，严格字段：schema、purpose、dex、
team_wallet、api_wallet、policy_hash、not_before_ms、expires_ms。
不在仓库提交真实审批文件、SMTP 密码或私钥。

## 监控、告警与运维

PAS 运行中仅“提交订单前的价差超限”进入 `market_wait` 自动等待，`paused` 仍为 false。
每轮重新采集快照，验证非价差市场规则、账户/账本持仓、亏损回撤、本地审批有效期及交易所 API 授权；
价差回到原有上限后自动解除等待。等待期间不生成决策或订单意图；恢复后若已过正常窗口则跳过，
不会补单或改变信号时钟。等待和恢复分别写事件并发送状态变化通知。
人工暂停、启动/重启暂停、授权失效、外部持仓、保证金异常、止损及未知/部分订单仍锁定。
订单提交后的对账异常不进入自动等待，不自动追单。PAR 保持原有暂停行为。
`market_wait` 是现有 JSON 状态的可选字段，无数据库表迁移；重启仍必须显式 resume。
自动等待不会延长或续期签名审批，也不会自动平仓。

preparation 每 60 秒只读预检，独立 `monitor.sqlite3`（application_id PASM，FULL/WAL），
保存最近完整快照、心跳和持久告警 outbox。相同问题去重，问题变化/恢复生成事件。
健康退出码仅代表监控存活，不代表允许交易；输出单独列 readiness 和未送达数量。
未来交易账本存在时，监控只读检查暂停、未知订单、超过 180 秒无心跳。
首次无交易账本时要求账户平仓；激活后只接受与独立 PAS 账本一致的持仓，不自动认领外部持仓。
健康检查本身不能发现自身所在主机断网/停机：需外部监控接收心跳，当前不作已实现声明。

邮件配置为独立 notifications/smtp.json，仅 monitor 挂载；支持验证证书的 SSL 465 或
STARTTLS 587。字段 host、port、sender、recipient，可选 username/password。
recipient 必须等于同目录 recipient.txt 中用户确认的地址。
无配置/发送失败不丢事件；SMTP 接受单独记录，收件箱送达需人工验证。
本次接收地址仅存在 VPS 运维配置，不提交个人地址到仓库。

**进程停止和暂停均不会自动平仓。** 显式 flatten 仍需有效签名授权、真实持仓归属和
成交对账；可能因最小交易额、流动性、交易所拒绝或 API 不可用而无法退出。
独立 watcher 应持续运行，人工值守需覆盖首次上线及通知未验证时段。

在线备份使用 `python -m perpetual_alpha.funding_live.ops --source ... --destination NEWFILE`，
只接受 Funding 账本，SQLite online backup 保留未知意图，不覆盖源或已有备份。
恢复先核对身份/hash、重启暂停并与交易所真实订单/成交/持仓对账，绝不自动重放未知意图。
备份和恢复不启动 signer；PAR 账本不能迁移到 PAS。

## 尚未通过的生产门槛

当前已知 PAS ETH 20× Cross；独立 PAS signer 未配置；SMTP 和外部送达未验证；
杠杆及正式下单尚无本次授权；首笔真实签名订单未验收。
因此代码/公开预检准备完成也不意味着下一窗口无条件具备执行条件。
详细 VPS 时间戳、镜像、服务变化和验收结果见独立 operations 报告。

### PAS 临时公共请求故障等待

PAS 在已完成待定订单对账、无操作员请求、未暂停且尚未创建本轮决策/订单意图时，公共 `/info` 查询的连接失败、超时和 HTTP 408/429/500/502/503/504 转为 `market_wait.reason=network`。沿用60秒轮询；等待期间不创建决策或订单。只持久化白名单查询类型和状态码，不记录请求体、响应体或凭据。其他HTTP错误、TLS身份错误及数据/账户校验错误仍锁定暂停。

每次重试先检查本地交易许可，恢复后重新采集完整快照、检查账户持仓/损失/杠杆、交易所API授权和盘口；窗口内正常执行一次，超出15分钟窗口不追单。待定订单对账、操作员恢复/退出/取消和所有决策创建后的故障均不自动恢复。人工暂停、重启暂停及授权到期仍需要相应明确操作，不自动续期。PAR不启用此恢复行为。暂停、等待和停止进程均不自动平仓。

监控分别报告 `waiting for network` 与 `waiting for spread`，恢复后清除等待告警；持续相同状态不会反复创建通知。真实故障的历史HTTP状态码不可事后恢复，新逻辑仅为后续故障保留安全诊断。

### 持续授权与健康自动续期（显式启用）

默认不自动续期。操作员明确授权后，单独的 `public/standing-trading.json`（schema `pas-standing-trading/1`）绑定团队、独立API钱包和精确的 enabled policy hash；`duration_ms=86400000`、`renew_before_ms=900000`、`enabled=true`。它不授予杠杆变更、解除暂停或扩大风险预算的权限。关闭时将 `enabled` 改为 false，现有24小时授权仍按原期限有效；立即停止交易另用 pause，暂停不会平仓。

通过基础 compose 加 `deploy/compose.pas-renewal.yaml` 显式部署独立监控。只有监控增加交易授权目录写权限和持续授权文件只读挂载；不挂载API私钥。Trader原有授权检查与24小时有效期上限保持不变，Collector/PAR/研究数据不参与续期。

每60秒检查；当前授权剩余不超过15分钟且尚未到期时，要求完整公共账户/市场预检通过、API授权覆盖未来24小时、交易器心跳新鲜且未暂停/等待、无待确认订单或操作员请求、持仓/损失/回撤/杠杆/保证金一致，且SMTP已配置、无发送失败待重试邮件。失败不续期、不恢复、不追单；后续仍在有效期内可重新检查，已过期则需人工恢复。每次新授权从续期时刻起24小时，不是无限期单次许可。

授权文件原子替换且fsync；发布时锁定交易账本以避免与新订单/暂停竞争，但不修改交易账本。崩溃恢复日志保证已生效续期进入独立Monitor的持久outbox；每次续期只入队一次。主题为 `PAS trading authorization automatically renewed`，正文含续期标识、旧/新到期时间、UTC新截止、策略、25 USDP上限和1× Cross。发送失败保留并重试，不把SMTP接受冒充收件箱送达。SMTP接受后崩溃、尚未提交投递确认时可能重发同一renewal_id，这是至少一次通知语义。

### 暂停原因修复：过期盘口等待与完整事件去重

仅过去时间戳超过原10秒上限的盘口，在PAS活跃、已完成待定订单对账且尚未创建决策/订单意图时，进入 `stale-book` 等待；不使用过期报价下单。其他快照校验、仓位/损失/回撤、当前许可和API授权仍检查。每60秒重新采集，恢复后只在原窗口内决策。未来盘口时间戳、时钟回退、账户问题、人工/启动暂停和下单后故障保持锁定，不自动解除。PAR行为不变。

监控必须将公共/运行/续期检查合并为最终完整issues后再记录一次，防止同一轮中的中间状态造成虚假的恢复及再次报警。历史积压中确认由旧缺陷生成的重复“暂停/续期被阻止”邮件可在独立monitor库中归档抑制（保留完整原行及原因和在线备份），保留最新完整告警；不修改交易账本、采集库或已实际送达记录。
