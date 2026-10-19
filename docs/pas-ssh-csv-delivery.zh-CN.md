# PAS CSV 下载指南与部署交付记录

2026-10-19：独立 Python 3.14 Exporter 已部署，服务端验收通过，队友公钥已授权。队友电脑首次登录和下载尚待本人确认；不要上传或分享私钥。

## 队友直接使用

SSH 地址：`alpha_export@57.129.179.248`，端口 22。请在持有已授权公钥对应私钥的电脑运行。
首次连接核对服务器 ED25519 主机指纹：

```text
SHA256:HIcAhbvF2yYThqYHCMaLjPFb8w1SgZ7vI+QL5EwoBXM
```

### Windows CMD

全部已采集历史：

```bat
ssh -T alpha_export@57.129.179.248 "export --all" > pas_all.csv.part && move /Y pas_all.csv.part pas_all.csv
```

任意指定 UTC 区间（替换时间，左闭右开）：

```bat
ssh -T alpha_export@57.129.179.248 "export --from 2026-10-19T12:00:00Z --to 2026-10-19T18:00:00Z" > pas_selected.csv.part && move /Y pas_selected.csv.part pas_selected.csv
```

按资产或数据类型导出：

```bat
ssh -T alpha_export@57.129.179.248 "export --all --symbol BTC" > btc.csv.part && move /Y btc.csv.part btc.csv
ssh -T alpha_export@57.129.179.248 "export --all --dataset funding" > funding.csv.part && move /Y funding.csv.part funding.csv
ssh -T alpha_export@57.129.179.248 "export --all --dataset minute-panel" > panel.csv.part && move /Y panel.csv.part panel.csv
```

默认 dataset 为 `observations`，`--symbol`、`--dataset` 可与时间区间组合；`--raw-json` 可额外保留原始 JSON。分钟面板的时间边界须对齐整分钟。observations/funding 按 available_ms 筛选，不是资金费结算时间。

密钥不在默认位置时，在 `ssh` 后加 `-i "%USERPROFILE%\.ssh\你的私钥文件名"`。遇到 `Permission denied (publickey)`，检查是否使用授权公钥对应私钥；不需要密码或服务器管理账号。

以上命令请在 CMD 执行。Windows PowerShell 5.1 的重定向可能改变编码；PowerShell 7.4+ 支持原始字节重定向，但应检查 `$LASTEXITCODE -eq 0` 后再改名。

### Linux / macOS

```sh
ssh -T alpha_export@57.129.179.248 'export --all' > pas_all.csv.part && mv pas_all.csv.part pas_all.csv
ssh -T alpha_export@57.129.179.248 'export --from 2026-10-19T12:00:00Z --to 2026-10-19T18:00:00Z' > selected.csv.part && mv selected.csv.part selected.csv
```

成功才把 `.part` 改名。失败的文件可能只有 CSV 前缀，不能作为完整数据使用。`--all` 包括请求开始时已提交的历史，不包括未来记录。每次最多 300 秒；大历史请按时间切片。账号只允许一份并发导出，遇到 `Another export is active`，等上一个完成后重试。

## 已部署版本与验收

- PR [#24](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/24) 和 [#26](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/26) 已合并；Python 3.14 适配提交 `0815dab` 通过 CI #167。
- 已部署 main：`cf5c0f392a4bc608dea5782d7dee63c19874d6a0`。
- 独立 runtime：`/opt/alpha-csv-export/venv`，Python 3.14.4，SQLite 3.46.1；只安装 Exporter 0.1.0 与 pip，没有交易 SDK 或主项目依赖。
- 已安装 `python3.14-venv`、`acl` 及其 wheel 工具依赖；未升级或删除现有软件包。
- 管理员测试公钥经真实 SSH 下载：全量 observations 9,097 行、选定区间 105 行、BTC 历史 867 行、ETH 区间 10 行、funding 130 行、minute-panel 3,750 行；另验证资产筛选及 raw JSON。数字为验收时样本，后续采集会增长。
- 全量 observations 的序号前缀和原始哈希摘要经独立只读 SQL 对照一致；资金费行数与原始响应对照一致。
- Shell、注入、任意路径参数、SFTP、密码、PTY、转发均被拒绝；专用账号不能写 DB/WAL/SHM，不能遍历管理员 SSH、交易配置与受保护部署目录。
- 并发导出锁拒绝第二份导出，释放后重试成功；PAS 持续追加，SQLite 保持 WAL。没有停止或重启 PAS/PAR Collector、交易或风控服务。
- PAS Collector 验收时 healthy、重启数 0。原有 PAR `deploy-collector-1` unhealthy / IndexError 已记录，本次未修改或修复。
- 队友公钥已按 `restrict` 授权；管理员导出验收测试公钥已撤销并验证登录被拒绝。管理员原有登录密钥未改，新建管理员 SSH/sudo 连接成功。最终仍需队友本人用其私钥确认客户端连接。

## 运维与回滚入口

安装细节、SSH EOF Match 区块、ACL 默认继承、资源限制和幂等回滚见[部署运维说明](pas-ssh-csv-export.zh-CN.md)。运行时、launcher 和公钥文件由 root 管理；不提供 sudo、Docker、Shell 或部署凭证。

本次 root-only 配置、ACL、安装和验收备份在：

```text
/var/backups/alpha-csv-export/20261019T195151Z-cf5c0f3
```

回滚先撤销导出公钥、禁用专用账号并处理其既有会话；保持管理员连接。随后从当前 SSH 主配置生成移除本功能区块的候选配置，验证 `sshd -t` 和管理员有效配置后才 reload，并验证新的管理员连接。只撤销本功能 ACL，保留后续业务权限变更；不操作数据库内容，不重启 Collector。完整步骤以运维说明为准。

备份中的完整验收记录和密钥管理记录仅供管理员，不将私钥、公钥原文、数据库、下载 CSV 或本地测试产物提交到仓库。
