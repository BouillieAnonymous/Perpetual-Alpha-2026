# PR5：只读账户状态 Adapter

## 范围与结果

在 PR4 已合并的 main `ea2b584` 上新增账户观察模块，落实开发基线 A11/A12/A16：真实来源与时点、前后核对、余额/保证金分离、完整 `par` 持仓与挂单、十资产和未知资产的数量敞口、失败与 unknown 表示。

固定访问 Hyperliquid Testnet 公开 `/info`。不导入 Exchange、不读取 .env/私钥、不修改账户模式/杠杆、不提交/撤销订单。仅处理操作者显式提供的公开地址；API agent 地址不自动跟随到 owner 查询。

新增独立 `account-observation/0.1` 格式、Schema、合成样例及账户证据 SQLite。旧七份业务 Schema、13 个 Transport DTO、Forecast 0.1、PR2/PR4 模式和 `api_client.ReadOnlyClient` 不改。账户数据没有通过 Gateway 外发给 Agent，也没有注入旧 Context/Portfolio/MockExecution。

**observed 不等于风险准入。** 本版始终 risk_ready=false、global_account_complete=false；equity_usd 和 available_margin_usd 保持 null。系统当前不能从账户总余额、hold、withdrawable 或某资产 buying power 推出一个已验证的全局美元可下单资金。后续 PR6–PR7 必须核对估值、资金口径和真实风险政策后才能消费这些字段。

## 文件

| 文件 | 职责 |
|---|---|
| `accounts/client.py` | 显式钱包、固定只读 query allowlist、testnet/par |
| `accounts/capture.py` | 21 次有界查询、前后 probe、deadline/字节预算、错误证据 |
| `accounts/normalize.py` | 余额、buying power 来源数组、持仓/挂单、数量边界及完整性 |
| `accounts/store.py` | 独立 WAL DB、原始+规范化不可变保存、asof/新鲜度读取 |
| `accounts/schema.py` | 单独的 AccountObservation Schema，不扩展旧交易协议 |
| `accounts/cli.py` | capture/query；默认只打印摘要，details 才展示账户数据 |
| `tests/account_support.py` / `test_account_snapshots.py` | 合成账户、41 项数据/失败/并发/崩溃测试 |
| `scripts/validate_account.py` | CI 离线 Schema→合成观察→SQLite→只读读取 |

## 查询来源与采集顺序

1. userRole：只接受明确 user；agent/missing/vault/subAccount/未知角色返回 unavailable，不自动追随主账户，也不更改地址。
2. userAbstraction、spotClearinghouseState、clearinghouseState(dex=par)、frontendOpenOrders(dex=par) 第一轮。
3. metaAndAssetCtxs(dex=par)，以及全部十资产的 activeAssetData。
4. 余额、持仓、挂单、模式与角色第二轮。

实际共 21 次 HTTP；前后两组只核对可变化的账户端点，价格与 buying power 分别保留自己的接收时间。只有 source 对齐、结构有效、前后匹配且时限通过时才可标 observed；结构缺失/变化/端点失败标 partial，角色无法确认则 unavailable。

每个 observation 保存确切 request、请求开始、实际 received_ms、原始 JSON 或固定错误类别。没有可信来源时间的端点不捏造 event/published 时间。perp 响应的 time 单独保留并拒绝未来时间；源 time 比接收早超过工程 max_source_lag_ms 时标 PERP_STATE_STALE、持仓覆盖不完整。

前后匹配称为 bracket_matched_non_atomic，**不是原子快照**。比较忽略列表排列与根时间字段，保留余额、仓位、挂单和资金数据变化；无法检测两次 probe 之间先变后复原的 ABA。返回捕获窗口和组件时间，未来执行前必须重新读取/对账，不能将一次匹配用作交易许可。

## 资金口径

| 字段 | 来源与含义 | 本版不作的推断 |
|---|---|---|
| balances.total / hold | spotClearinghouseState，按 token/coin 保留所有余额 | total 不是 free margin |
| unheld_balance | 同币种 total-hold，允许为负 | 不是可下单额度 |
| available_after_maintenance | tokenToAvailableAfterMaintenance 原样保留、单位为该 token | 不是统一的美元可用保证金 |
| venue_perp_summary | par marginSummary/crossMarginSummary/maintenance/withdrawable | Unified 模式下不将 par accountValue 当成全账户余额 |
| available_to_trade | 每个 activeAssetData 的原始双元素数组，单位标 venue-availableToTrade | 不转换成全局资本或另一资产额度 |
| max_trade_sizes | activeAssetData.maxTradeSzs 原始双元素数组，标 venue-size | 不推导安全仓位，不自动取最大值下单 |
| equity_usd / available_margin_usd | 当前 null | 不猜 USDPR→USD 的估值或抵押资产换算 |

数组顺序原样保留，direction_mapping_verified=false。公开接口文档的数组示例不足以作为本版买卖方向映射的完整验证；执行接入前需要独立核实顺序和资金单位，不能将 index 0/1 的数值直接当成已批准的 buy/sell 额度。

USDPR collateral 必须同时匹配公开 metadata 的 collateralToken 与 spot balance 的 token/coin，不能仅凭显示名称认定。负余额原样保留，不截为零。其他 token 余额也保存，不只看 USDPR；但本版不将它们转换为美元权益。

只支持将 unifiedAccount 作为已观察到的预期模式；disabled/portfolioMargin/未知/变化均报告 partial，不自动设置账户。activeAssetData 中 user、coin、双元素数组、decimal 和 leverage 逐项校验；某资产缺失不会挪用另一资产数据。

## 持仓、挂单和敞口

读取完整 `par` 端点结果，不按这次 Forecast 的资产子集过滤。每个持仓保留带符号数量、entry、独立 mark/报价单位、源 positionValue、PnL、marginUsed 和 leverage。positionValue 仅标 venue-positionValue，不用数量×报价推导通用美元名义价值；valuation_multiplier/notional_usd 均为 null。USDJPY 的价格单位 JPY/USD 与 USDEUR 的 USD/EUR 保持区别。

挂单使用 frontendOpenOrders，保留 oid、cloid（若有）、side、remaining/original、limit/trigger、reduceOnly 和 orderType。所有订单 ownership=unattributed；不能因为缺少本系统 ID 就忽略人工或其他进程的订单。状态 observed_open 只代表该次公开查询看见，不是完整订单生命周期。

十资产分别输出数量敞口；实际出现的未知 par/non-par symbol 也保留并标 unsupported，不静默丢掉。完整且前后匹配的观测数量边界：

- minimum_quantity = 观察持仓数量 − 所有观察到的卖单剩余数量；
- maximum_quantity = 观察持仓数量 + 所有观察到的买单剩余数量。

这里保守计入 reduce-only/trigger，不假定互斥或自动抵消。它只是**已观察数量**的边界，不是带费用/成交/FX/未知订单模型的风险估计；notional_usd 仍为空。

如果持仓/挂单端点失败，缺席资产的 quantity/pending/bounds 不能填零。只有完整的持仓列表才允许把未出现资产表示为观察到的零仓位。账户变化时不提供有效 min/max，即使之后一组列表看起来完整。

两轮之间新增、变化、消失的订单单独列出；消失 ID 为 disappeared_unresolved，不能断言 canceled 或 filled。所有快照保留 inflight_order_exposure=unknown-no-intent-ledger。PR8 尚未建立持久 Intent/交易所对账，不能用“当前 openOrders 为空”证明没有在途订单。

## 覆盖范围与状态

本版覆盖 `par` 持仓/挂单和 spot 返回的 token 余额。它不扫描所有 builder DEX、spot 交易挂单、借贷/质押/vault 等全交易所风险；other_dex_exposure=unknown、global_account_complete=false。即使 `par` 十资产完整，也不能据此批准共享抵押品的全部账户风险。PR7 接入前必须明确允许范围并处理外部风险，不将这些 unknown 解释为零。

账户角色 user 只说明公开 API 的账户类别，不证明调用方控制私钥或它就是队伍账户。wallet_identity_source 为 operator-supplied-public-address，team_ownership_verified=false；已有部署记录与人工核对可作为后续身份确认依据，不通过此 Adapter 发起签名来验证。

| 层次 | 状态 |
|---|---|
| 快照自身 | observed / partial / unavailable |
| asof 读取 | 上述状态，或 missing / stale |
| 交易风险就绪 | 本版恒 false，不能映射成旧 account.status=ready |

组件失败保留错误和完整性标志，不用上次好数据填充当前失败。查询最新记录也不会跳过新的 partial 去找旧 observed。CLI 非 observed 时返回非零，并打印不含私钥/原始财务数据的摘要。

## 时点、持久化与恢复

AccountStore 使用独立 application_id/schema 1，拒绝打开 collector/history/runtime/journal 文件。保存原始 bundle、规范 snapshot、摘要、钱包、完整接收时间和入库写事务采样的 available_ms。snapshot.observed_ms 是本轮结束，started_ms 是保守的最早数据时间边界；它们不声称端点原子一致。

捕获在数据库写锁之外进行。保存时 normalize、摘要与大小校验后一次 BEGIN IMMEDIATE 提交；同 capture_id 同内容重放，换内容冲突。连接各操作独立创建/关闭，WAL/FULL/foreign_keys、有界 timeout，时间回拨和未来捕获拒绝。未提交进程硬退出回滚，不影响先前快照。

读取要求 received_ms、available_ms≤asof；后来读取到的账户状态不能回填旧 Job。年龄用 asof-started_ms 保守计算，不用最后一个新响应掩盖最早响应已旧。读取验证 raw+snapshot 摘要，不重解析源改变旧证据。读句柄 mode=ro/query_only，不修改文件、不使用 immutable。

本版没有将 AccountStore 自动注入 Scheduler 或外发给 Agent；将来接入必须将实际 snapshot/digest 封存并遵守 Job asof。真实账户/订单信息的外发范围仍须单独决定。

## 工程预算与运行

测试默认：单请求 10 秒、至少间隔 1 秒；捕获窗口 60 秒、21 个固定请求（结构上最多 24 observations）；源接收滞后预算 90 秒；单响应仍受公共传输 8 MiB 约束；整轮内容预算 8 MiB；持久化 bundle+snapshot ≤16 MiB。超出窗口/字节后不再发新请求，已发出的有界请求结束后标失败，保留已观察内容。

这些是工程测试数值，不是资本、杠杆、损失、风险阈值或生产实时性承诺。列表最多 1,000 项，超过上限则完整性失败；不分页拼出虚假完整账户。

仓库根目录，使用显式公开地址：

```powershell
.\.venv\Scripts\python.exe -m perpetual_alpha.accounts.cli capture --wallet '<TEAM_PUBLIC_ADDRESS>' --database .cache/account-demo/account.sqlite3
.\.venv\Scripts\python.exe -m perpetual_alpha.accounts.cli query --wallet '<TEAM_PUBLIC_ADDRESS>' --database .cache/account-demo/account.sqlite3
```

query 可以指定 `--asof-ms`、`--max-age-ms`，只有 `--details` 才会输出账户详情。capture 不接受 asof 覆盖，不读取任何 .env。命令单次执行；持续账户服务、刷新频率、数据保留和备份监控留后续部署验收。使用忽略目录，限制文件访问权限；不要把真实账户 bundle 放进 PR、公共日志或 LLM 输入。

## 真实只读验收与离线测试

使用用户本轮提供、且与既有部署记录一致的公开地址进行了只读验收。最终结果：role=user、unifiedAccount；par 持仓 0、挂单 0；十资产 buying power 响应有效；前后 bracket 匹配、status=observed、risk_ready=false。该结果是一次捕获，不代表之后账户状态。没有测试非空真实仓位/挂单或制造交易来生成样本；非空、变化和故障均由明确合成样本覆盖。

真实地址/余额/订单/原始响应不作为本 PR 新增共享资料。原始证据保存在本地忽略目录，仓库仅新增 `account-observation.synthetic.json`：明确合成的钱包、非空多资产持仓和挂单，与真实账户无关。

新增 41 项测试，原 235 项保留，合计 276 项。覆盖角色/模式、资金不混同、全部资产与 FX/未知资产、reduce-only/trigger、消失订单 unknown、前后变化/排序、超时/失败、伪造身份/源时间、数据滞后/预算、只读 WAL/asof、幂等、并发、摘要破坏、进程硬退出；CI 全部无外网/无真实账户。

```text
python -m unittest discover -s tests -v
python -m ruff check . --no-cache
python scripts/validate_account.py
python scripts/validate_schemas.py
python scripts/validate_history.py
python scripts/validate_runtime.py
```

## 回滚与后续依赖

没有迁移旧数据库。停止新账户 CLI/调用方并保留独立 AccountStore 即可回滚到旧入口；保留原始证据，不通过清库解释未知订单。SQLite 备份应使用一致 backup，不热拷贝单个活跃 WAL 主文件。长期保留、损坏恢复、只读容器 sidecar、VPS 资源和跨 VPS 加密仍归运维阶段。

PR6 可基于历史窗口实现风险估计，但不能从本 PR 的 observed 推断账户可交易。PR7 前仍须核对 USDPR/美元估值、各资产合约乘数、其他 DEX/spot/在途风险，以及用户批准的资本、仓位和退出政策。当前不授权真实 Testnet 或主网订单。

## 官方依据（本 PR 开发时核对）

- [比赛账户与查询](https://perpetual-alpha26.github.io/docs/)：par/USDPR/Unified、主账户查询、activeAssetData；total 不是 free margin。
- [账户抽象模式](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/account-abstraction-modes)：Unified 的共享抵押品与 spot balances 口径。
- [Perpetuals API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals)：clearinghouseState、activeAssetData、market metadata。
- [Spot balances](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/spot)：token total/hold。
- [Info API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)：userRole、frontendOpenOrders。

tokenToAvailableAfterMaintenance 的实际响应已保留，但公开说明不足以把它重命名为 free margin。本版只报告原字段，不补造估值模型。
