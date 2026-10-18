# ETH Funding-LAST 双环境交付

目标：同一确定性信号和测试网执行模块接入 PAR（USDPR）与 PAS（USDP）。PAR 完成验收后运行；PAS 仅准备配置、只读验收和运行手册，本次不启用订单。

基于 PR #18 的环境隔离。研究依据 PR #15（6832145）；不复制历史收益为上线预期，不改变原研究文件。固定每 12 小时决策，最新已结算且实际已收到的同市场 Funding，正费率空、负费率多、零目标平仓，超过两小时或未来时间拒绝。

交付顺序：冻结配置 → 双市场只读 Adapter → 目标仓位与独立风险检查 → SQLite 订单意图/成交/对账 → 故障测试 → Linux CI → PAR 隔离部署与观察 → 更新验收证据。PAS 激活属于后续操作，本次保持禁用。

新运行政策使用账户权益至多 5% 名义仓位、1 倍 cross 杠杆，以及显式绝对金额/订单/亏损/数据年龄/执行摩擦上限。这是试运行政策，非回测最优参数。启动、重启和未知订单默认暂停，必须核对订单/仓位后显式恢复。暂停不自动平仓，紧急退出为独立 reduce-only 操作。

任何未通过项目均在 PR 与验收记录列明，不以 synthetic Paper 成功代替交易所测试网成交验收。旧 Collector、Paper 和其他研究服务保持独立。

## 模块与边界

`funding_live/policy.py` 只做确定性方向、Decimal 目标数量和独立下单检查；`venue.py` 固定 Testnet URL、显式 dex/coin 与同市场 Funding，不支持主网。`engine.py` 负责当前策略的单写者执行；`store.py` 使用独立 SQLite application_id/schema 与身份绑定，不打开旧 Runtime、Paper 或 Collector 库。本次是独立确定性策略 runner，没有声称已完成所有 ForecastProducer/HTTP Agent 的真实执行升级；既有协议不变。

`par:ETH`/USDPR 与 `pas:ETH`/USDP 使用相同代码、不同配置与独立账本。PAS 的只读接口可以检验，但仓库配置 `enabled=false`，Compose PAS 服务仅 `inspect`、无网络、无凭据。后续正式启用需要明确授权、核实 USDP 资金和账户、复制新的启用政策并使用新的政策身份/账本；不能改名或迁移 PAR 数据冒充 PAS。

行情末次读取，整次采集至多 30 秒、盘口年龄至多 10 秒；价差至多 15 bps、执行触价滑点上限 5 bps、mid/mark 相对 oracle 偏离至多 50 bps、taker 实际费率上限 9 bps。读元数据确认抵押品 token、szDecimals、退市和费率模型；`allPerpMetas` 找到所有共享抵押品 DEX，再查它们的仓位与挂单。2026-10-18 现场发现 PAR 与 parx 共用 USDPR、PAS 与 pa 共用 USDP。其他持仓、挂单、collateral hold、账户在采集期间变化、未知字段均阻断。

统一账户的权益以 spot total 为准，**不能再加 ETH 未实现损益**；部署验收确认该字段已经包含未实现损益。保守取采集前后两个 spot total 的较小值；不把清算所 `accountValue=0` 误认成无资金，也不把 spot total 当作 free margin。free 使用 tokenToAvailableAfterMaintenance 的前后较小值，并要求 activeAssetData 的两边 size/buying-power 都足够，不猜数组方向。至少保留权益 90% 的可用抵押缓冲。自己的 cross 仓位会形成 spot hold，按前后 own marginUsed 上界解释、允许一个抵押品数量单位的异步舍入误差，超出部分仍阻断。非零仓位的 mark 变化会改变 spot total/hold，不把正常浮盈变化当作外部活动；仓位数量/杠杆仍须前后匹配。共享抵押品其他仓位不自动采纳。

常规时钟为 UTC 00:00/12:00，最多迟到 15 分钟；信号使用实际决策时刻已收到的最新同市场结算记录，记录结算、available 与决策时间，不用预测 funding 充当已结算值。PAR 首次部署可显式 `--bootstrap` 在当前时刻做一次初始决策；只在整个账本没有决策时有效，是上线初始化例外，之后恢复固定时钟。错过窗口不补单。

每个时钟槽只接受一个决策；不追单，不因重启重新发同一槽。仅 IOC 限价，反向先 reduce-only 平旧仓、确认平仓后刷新行情/账户再开新仓。25 单位订单预算限制新增敞口；经过数量/归属核对的 reduce-only 退出可以平掉因价格上涨而超过入场名义预算的原有仓位。每笔先 FULL/WAL 持久化 deterministic cloid 再发送；任意崩溃窗口均保留 unknown，不假设未发送。网络超时后查原 cloid、真实 fills/feeToken、实际仓位；不能确认则保持暂停，绝不换 ID 重发。部分成交/未成交停止并等待人工检查，避免重复追价。数量不足 $10 不放大预算凑单，退出 dust 如不能成交则明确保留风险。

启动、重启均 paused，显式 resume 重新核验账户并设置 1x cross。generation 与 pause_epoch 防止旧请求或恢复检查期间的新暂停被覆盖。baseline/peak 跨重启保留，累计亏损或回撤达 5 个抵押品单位、敞口超过 25 名义上限的 110% 或数据/订单故障均暂停。**暂停和停止进程不自动平仓**；`flatten` 是独立的 reduce-only 请求，可越过亏损准入但仍检查新鲜报价和可执行深度。这里没有服务器失联后的交易所托管止损，不应将 5 单位暂停线理解为保证最大损失。

## 本地验证与双环境预检

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -p test_funding_live.py -v
python -m perpetual_alpha.funding_live inspect --config config/funding/par.toml
python -m perpetual_alpha.funding_live inspect --config config/funding/pas.toml
python -m perpetual_alpha.funding_live preflight --config config/funding/par.toml --env-file .env
python -m perpetual_alpha.funding_live preflight --config config/funding/pas.toml --env-file .env
```

使用项目固定 Python 3.12.15。inspect 无文件/网络副作用；preflight 只读交易所，将原始证据保存于所选 root/dex 的新文件，不加载签名密钥。preflight 非零代表存在阻断项，并非强制失败后仍可发送订单。

新增测试覆盖两环境同方向、未来可得时间/过期/冲突 Funding、价格/数量/深度/保证金、固定时钟幂等、请求级 ok 内嵌拒单、成交后超时、实际子进程 os._exit、部分成交、错误 feeToken、外部仓位、重启暂停、陈旧恢复请求、恢复期间暂停竞争、单进程锁、异库拒绝与在线 WAL 备份恢复。测试中全部交易 I/O 为 fake；现场成交证据另列。

## VPS 运维

独立根目录 `/opt/perpetual-alpha-funding`：`releases/<commit>` 保留不可变代码，`state/par` 与 `state/pas` 隔离，`secrets` 目录 700/文件 600、只读挂载，`evidence` 留存构建和验收日志。运行用户 UID1000，根文件系统只读、全部 capabilities 丢弃、资源/PID/日志上限、无公开端口。API 钱包只挂 PAR；主钱包私钥不进入程序。不同签名进程须使用不同 API 钱包；本 runner 在共享 state 根持有签名进程锁，但不能防止另一个目录/主机/旧手动工具使用同一密钥，禁止并行运行它们。

部署的 Compose 不会自动 resume。Docker `unless-stopped` 只恢复进程、每次重启重新暂停。`health` 是循环存活；是否允许交易必须看 `status.paused/reason`。异常会写入 SQLite 和容器日志，尚未接通站外报警。

```bash
cd /opt/perpetual-alpha-funding/releases/<已验收commit>
export FUNDING_IMAGE=perpetual-alpha-funding:<已验收commit>
export FUNDING_STATE=/opt/perpetual-alpha-funding/state
export FUNDING_SECRETS=/opt/perpetual-alpha-funding/secrets
# sudo 可能过滤 shell 环境，显式传递非敏感部署参数。
sudo env FUNDING_IMAGE="$FUNDING_IMAGE" FUNDING_STATE="$FUNDING_STATE" FUNDING_SECRETS="$FUNDING_SECRETS" \
  docker compose -f deploy/compose.funding.yaml --profile rehearsal up -d par
sudo docker exec alpha-funding-par-1 python -m perpetual_alpha.funding_live status --config config/funding/par.toml --root /state
# 每次从 status 读取当前 generation，不复用示例数字。
sudo docker exec alpha-funding-par-1 python -m perpetual_alpha.funding_live resume --config config/funding/par.toml --root /state --generation <当前generation>
sudo docker exec alpha-funding-par-1 python -m perpetual_alpha.funding_live pause --config config/funding/par.toml --root /state --generation <当前generation>
sudo docker exec alpha-funding-par-1 python -m perpetual_alpha.funding_live flatten --config config/funding/par.toml --root /state --generation <当前generation>
sudo docker exec alpha-funding-par-1 python -m perpetual_alpha.funding_live cancel --config config/funding/par.toml --root /state --generation <当前generation>
```

`cancel` 只取消本账本 unknown cloid，不取消人工或其他策略的订单；之后仍需对账。未知 oid/证据截断/对账不一致不能靠删库恢复。操作请求一般在下一轮 60 秒轮询处理，订单一旦发送不能由本地暂停撤回。紧急情况先暂停新增敞口，再核对真实账户，按场所规则处置未完成订单/剩余仓位。

### 备份、恢复和回滚

`python -m perpetual_alpha.funding_live.ops --source <funding.sqlite3> --destination <新备份.sqlite3>` 使用 SQLite online backup、完整性检查及 exclusive-create，禁止覆盖源和旧备份。也可备份 unsigned observer 库。不能只复制活动数据库主文件而漏掉 WAL。

先暂停、确认挂单/仓位、备份，才停止服务或升级。保留原镜像、release 和完整 state。恢复至独立目录校验身份/完整性，启动仍 paused，先以交易所 cloid/fills/仓位补齐备份之后的事实，再显式恢复。旧备份可能丢失后续成交与意图，因此 unknown 或非零外部仓位会阻断；不保证任意旧备份能够无人介入恢复。回滚选择支持同一 schema/policy 的镜像，不能通过清库、降低 schema 或改身份消除订单事实。

## 验收记录

最终代码、CI、现场订单与部署状态见同目录的 `funding-release-acceptance.json`。短期验收不能代替多日稳定性或证明正收益；外部报警、异地恢复、托管止损、PAS 非空账户/真实成交仍需后续验收。

2026-10-18 完成：运行代码 `43dad96`，451 项 Linux VPS 回归和同提交 GitHub CI 全部通过。PAR 实际买入 0.0105 ETH@2358.7，显式验收退出以 reduce-only 卖出 0.0105 ETH@2358.8；总费用 0.044579 USDPR、这轮执行验收净损益 −0.043529 USDPR，期间没有跨小时结算。退出是操作验收，不是冻结策略的 12h 平仓，不能当作策略收益样本。最终仓位归零、两笔 Intent 均 terminal，无未决订单，恢复后的 generation 5 服务健康且 unpaused，等待下一正常 UTC 00:00（英国 10 月 19 日 01:00）决策。后续任何风险故障仍会重新锁存暂停。

首笔成交使统一账户 hold/浮盈语义的旧假设暴露，系统暂停且保留原 cloid。修正后在原账本上完成对账、确认成交/费币、没有重新入场。该问题及其他抵押品 mark 变化导致的误拒绝均添加回归。43dad96 是修正后的部署版本。真实入场发生在旧版本、真实退出与最终运行使用最终版本；不能声称所有真实成交都由最终提交从头产生。

现场在线备份、独立无网络/无 signer 恢复探测以及持仓状态下重启暂停均通过。原 Collector 与 Synthetic Paper 服务没有重启。PAS 公开行情、Funding 与元数据已读通；缺少 USDP 余额，保持 disabled、无签名凭据、零订单。代码故障测试验证取消/未知/部分成交；没有为了验收人为制造真实超时、真实部分成交或挂单撤单。

接口依据：[比赛参与指南](https://perpetual-alpha26.github.io/docs/)、[官方 SDK](https://github.com/hyperliquid-dex/hyperliquid-python-sdk)、[Hyperliquid 查询 API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)。现场元数据优先于文档静态精度；实际 feeToken/fee 与真实账户 Funding 支付单独留存。
