# PAS 首次正式比赛策略准备：独立 VPS 验收报告

验收时间：2026-10-19 16:00:55 UTC。状态 **WAITING ACTIVATION**。
代码/部署镜像版本 `9db22a25a7c403003c823c61f981490582cb1d5b`；
镜像 `perpetual-alpha-pas-trading:9db22a2`，本机 immutable image ID
`sha256:c48856e2926a85a31cc343d9c4649d0d2fcdef36760228cc95afbcf0bf948ff1`。
后续报告提交不改变该运行版本。完整机器证据见 [JSON](pas-launch-2026-10-19.json)。

## 源码集成

最新 main 重新读取为 `d2e924408a2c0adbe2b4285dd438a287acb2a1f4`，包含已合并 #22/#23。
#21 仍开放，head `99736bf1a9685b7d5846c64d0decba583cdf7caf`，基于旧 isolation 分支。
新 [PR #25](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/25)
将其合并到 main 基线，并增加 PAS 独立准备配置；未合并 PR 或修改 main。
唯一合并冲突为 docs/README 导航，全部入口均保留。
对 main 的 Collector/Research 核心文件与其测试、Collector Compose diff 为零。

## VPS 实际状态与权限边界

新增根 `/opt/perpetual-alpha-pas-trading`，仅启动 `alpha-pas-trading-preparation-1`，
启动于 `2026-10-19T16:00:30.243099171Z`，healthy。
只读挂载 public.env、PAR 公开 signer 地址、notifications 目录；仅自己的 state 可写。
没有挂载任何私钥、Collector 原始库、Research 派生库或 PAR 账本。
secrets/pas.env、approval/trading.json、PAS live funding.sqlite3 均不存在。
本次 PAS 签名操作 **0**；没有设置杠杆、创建订单、取消或平仓。

PAS Collector `perpetual-alpha-pas-data:b2e0d19` healthy，启动时间前后均为
`2026-10-19T13:56:45.147295858Z`；没有重启、清空或迁移数据库。
PAR runner 镜像 `43dad96`，启动时间仍为 `2026-10-18T21:40:34.190115751Z`。
既有 `deploy-collector-1` 在操作前即 unhealthy，本次保持原样；这不是 PAS Collector。
既有 synthetic Paper Core healthy；不启动 LLM 或其他未验收策略。
未观察到 Research 常驻容器；保留其 main 代码，不把代码合并误写成服务已部署。

## 只读账户与市场验收

| 项目 | 实际结果 |
| --- | --- |
| PAS 抵押品 | USDP，spot total/equity 100,000.0 |
| 真实可用保证金 | 100,000.0；双边 availableToTrade 均 100,000.0 |
| 当前 ETH 仓位/挂单 | 0 / 无；共享 USDP DEX 无其他仓位或挂单 |
| unified account / account role | 通过 / user |
| 当前公开 API 钱包授权 | 有效，但与 PAR 共用，不满足本配置独立 signer 要求 |
| 实际杠杆 | **20× Cross，未改为 1×** |
| 手续费 | 9.00000 bps，等于上限 |
| ETH bid / ask | 2390.4 / 2391.8；快照时价差、oracle、盘口年龄检查通过 |
| Funding | 最新实际收到已结算值 −0.0000690757；仅预览方向为多 |
| 条件性 1× 订单预览 | 0.0104 ETH，IOC 限价 2392.9，约 24.89 USDP；深度/保证金检查通过 |

该预览明确假设杠杆已确认 1×，**不是实际杠杆验收、订单或决策窗口执行**。
当前窗口外不触发交易，不修改信号以制造交易。盘口、费用、余额和授权必须在激活时重读。

## 参数、恢复与通知

冻结策略和完整限制见 [运行准备手册](../pas-launch-preparation.zh-CN.md)。
名义目标/单笔各不超过 25 USDP、1× Cross、权益比例上限 5%、可用保证金保留 90%；
5 USDP 累计亏损/回撤停止，不扩大预算。UTC 00/12，每次窗口 15 分钟。
订单意图先持久化，未知不重发；成交与持仓不一致时停止。
假 I/O PAS 验收覆盖幂等、部分成交不追单、暂停、显式 reduce-only 退出、
超时后重启对账、外部持仓变化、在线 WAL 备份及恢复后暂停。
首次真实 PAS 签名订单验收尚未进行，不能用 PAR 或离线结果替代。

**停止进程和暂停均不会自动平仓。** 显式退出依赖授权、流动性与交易所接受。
独立 monitor 能发现 trader 暂停/停止/未知意图，但主机或 monitor 自身离线需要站外监控；
当前无站外监控送达验收，不宣称存在托管止损。

用户提供的收件邮箱已写入 VPS notifications/recipient.txt（不在仓库提交个人地址）。
目前无 PAS SMTP 配置，无邮件发送；outbox 已持久保存 1 条告警、0 次发送尝试。
TLS SMTP adapter 与失败保留队列已离线验证；SMTP 接受和用户收件箱送达均未验收。

## 测试与上线门槛

运行版本在 VPS 无网络、只读容器完整源码挂载下 **495 项 unittest 全通过**。
本地 Funding 42 项（Windows symlink 跳过 1 项）、PAS 16 项、Research 19 项、Collector 9 项通过；
Ruff 通过。首次误用缺少配置的 runtime 包装导致测试失败，改用完整源码只读挂载后全通过，
保留 VPS 原始失败和成功日志，不将包装错误隐藏为策略通过。
GitHub Actions 已触发；在本报告初始记录时仍排队，最终状态以 PR checks 为准。
VPS CI 对应检查全部通过：固定 runtime/dependencies、pip check、Ruff、7 套验证脚本、
dry-run、LT adapter、PAS disabled 与无审批启动拒绝、Mock/History/Paper 三套双容器链，
以及独立 Paper operations 60.8 秒 soak、SIGKILL、网络断连、在线备份、恢复暂停、SQLITE_FULL。
测试使用随机项目名和临时卷，结束后已清理测试容器；现有服务没有重启。
VPS 日志 `/opt/perpetual-alpha-pas-trading/evidence/ci-equivalent-9db22a2.log`，SHA256
`29afd26481a7c62c0a54aaf3a2d542441bcbc899e0bf17e96a17f70fee629def`。
GitHub [CI run 39281999676](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/actions/runs/39281999676)
截至 16:05 UTC 仍 queued；不把 VPS 结果冒充 GitHub CI 成功，PR 保持 draft。

仍需完成：GitHub CI、独立 PAS signer/授权/私钥归属确认、SMTP 和外部送达验证；
随后单独取得 **PAS ETH 20× → 1× Cross** 授权，执行并从交易所读回确认；
再取得独立 **正式签名下单** 授权，启动后显式 resume、按正常窗口运行。
截至本报告，无条件执行下一窗口首单的条件 **不具备**。
下一正常窗口是 **2026-10-20 00:00 UTC（英国时间 01:00）**；
上述门槛若未全部满足则跳过，不提前、不补造订单、不改变交易规则。
