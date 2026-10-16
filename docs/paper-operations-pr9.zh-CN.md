# PR9：隔离 Synthetic Paper 部署与运维验收

## 范围

基于已合并 PR #11 / main `86926e9`，只运行独立 Synthetic Paper Core 与 Dummy Worker。既有 Collector 的镜像、容器、配置、历史库不修改。没有真实账户接入、LLM、签名、真实订单或撤单。所有资金、风险及成交参数继续使用明确的合成 fixture，不构成生产参数批准。

新增 `deploy/Dockerfile.paper`、`deploy/compose.paper.yaml`，使用独立镜像 tag、随机独立 Compose project、内部网络与两个独立卷。无宿主端口、无互联网出口、UID1000、根文件系统只读、cap_drop ALL、no-new-privileges；Core 256MiB/0.5CPU、Worker 128MiB/0.25CPU、各100 PID，临时目录有界，日志10MiB×3。资源数值是本轮工程测试配置。Worker 只有自己的状态卷和 token，无 Core 数据库、Core credential 或管理路由。

## 存活、接任务与授权

原 `/healthz` 的200/503含义保持不变，仍表示能否领取新任务。Core 暂停或有活动计划时，503是预期状态。

可选 `--progress-file /tmp/paper-progress.json` 在完整 Core 循环成功结束后每秒原子发布进度，包含 PID、单调时钟、启动代次、paused/ready。Docker healthcheck 通过 `paper_health` 校验进程存在且进度不超过15秒。它不授予风险权限，不会自动 resume。主循环阻塞或停止推进时探测失败；Docker 的 restart 策略只重启退出的进程，不会自动重启 unhealthy 容器。需要操作者调查，不能用自动恢复交易掩盖异常。

每次启动仍生成新 generation 并暂停；恢复使用既有本地 `paper_operator`，需当前 generation、pause epoch、新 request ID 与 actor。禁止增加启动自动恢复选项。

## 一致备份与隔离恢复

`python -m perpetual_alpha.runtime.paper_ops --database /data/runtime.sqlite3 inspect` 使用只读事务，输出完整性检查、配置摘要、状态计数、主库/WAL大小，不迁移、不更新风险状态，不导出 token、租约或原始请求。

`... backup --destination /data/backup-<UNIQUE>.sqlite3` 使用 SQLite online backup，包含已提交 WAL；不是复制活跃主文件。最多30秒的分段备份进度预算，数据库连接等待最多5秒；严重存储故障仍需外部超时。先生成同目录0600临时文件，切换独立备份到 DELETE journal，再在临时恢复副本中检查 SQLite identity/完整性、配置摘要、不可变成交/账本以及重新启动暂停。通过后以不覆盖已有文件的原子操作发布，输出 SHA-256。POSIX 同步文件及目标目录，Windows 使用拒绝覆盖的 rename。失败不会发布未经验证的最终文件。

`... --database /data/backup-<UNIQUE>.sqlite3 verify` 在临时副本中重新启动并核对账本，保持原备份不变。这是恢复演练，不会替换运行数据库，也不恢复交易权限。临时目录需足够空间；备份本身也必须限制访问，因为运行库包含通信与审计证据。

备份内部恢复演练使用目标目录的临时空间，避免历史库增长后受容器64MiB `/tmp` 上限约束；独立 `verify` 可在子命令前指定 `--work-directory /data`。至少预留主库、备份和恢复副本的空间，不对宿主磁盘进行填满测试。异常退出可能留下0700临时恢复目录，需停服务后由操作者辨别并清理，不能按通配符删除运行库。

备份应复制到独立受控存储并独立保存摘要；本轮不配置远程备份服务、自动删除或承诺 RPO/RTO。运行状态与 Worker 状态不跨文件原子备份；灾难恢复必须先停止两者，保留 Worker 旧状态，隔离启动恢复 Core，并通过原有反馈/幂等机制核对 pending submission。不能把旧 Worker 文件与新库随意组合后直接 resume。

实际恢复步骤：停止该 Paper project；保留现有卷和失败证据；将已验证备份恢复到**新的**空 Core 卷，使用相同不可变镜像及配置；Core 启动必须暂停；检查 ledger、unknown、Outbox、控制代次与 Worker pending 状态；需要时显式 reconcile；最后由本地操作者显式 resume。本轮不提供覆盖活动卷的一键恢复脚本。schema v4 不能交给只认识 v3 的旧镜像；回滚要恢复对应版本的一致备份，不能改 user_version。

## 可重跑容器验收

```bash
docker build -f deploy/Dockerfile -t perpetual-alpha-runtime:<COMMIT> .
docker build -f deploy/Dockerfile.paper --build-arg RUNTIME_IMAGE=perpetual-alpha-runtime:<COMMIT> -t perpetual-alpha-paper:<COMMIT> .
docker tag perpetual-alpha-runtime:<COMMIT> perpetual-alpha-runtime:ci
docker build -f deploy/Dockerfile.ci -t perpetual-alpha-checks:<COMMIT> .
PAPER_IMAGE=perpetual-alpha-paper:<COMMIT> PAPER_CHECK_IMAGE=perpetual-alpha-checks:<COMMIT> python3 scripts/validate_paper_ops_containers.py --soak-seconds 60
```

默认随机 project 与临时凭据，结束只删除自己的测试卷。VPS 使用 `--directory <新的私有绝对目录> --keep` 保留证据、凭据及暂停的 Core，Worker 停止。失败则停止两者，保留现场。目录必须全新，脚本不会覆盖以前部署。`project.json` 记录 project/image/Compose 路径，后续运维必须选这个 project，不能操作 Collector project。凭据目录0700、挂载文件0444以兼容 UID1000读取，仅包含随机合成认证，不读取钱包文件。

验收包含：暂停但存活、显式合成 resume、至少一个完成计划、周期性状态/主库/WAL/容器CPU-RSS-IO-PID与inspect命令延迟采样、Worker真实断网/恢复及重启、Core SIGKILL后暂停、无额外成交、在线备份/隔离恢复、无公开端口与外网出口、Worker无Core库/凭据、只读根文件系统、资源/日志上限。磁盘满通过临时测试库 `max_page_count` 触发真实 SQLITE_FULL，验证事务回滚与账本，不填满 VPS 根盘。保持原395项测试并新增8项运维测试。

CI 增加第四条双容器验收，使用本部署 Compose，默认60秒合成短跑。`report.json` 保留样本与起止时间，`acceptance-backup.sqlite3` 保存独立一致备份；这些在私有目录，不提交仓库。报告中的命令延迟包含 docker exec/启动及查询开销，不是 HTTP p95，也不是交易延迟。数据库证据会持续增长；日志轮转不代表数据库已设置保留期。正式长期运行前需确定磁盘报警、证据归档/保留、备份周期与RPO/RTO。

新增采样每轮原子保存报告，包括主循环进度、宿主证据目录所在磁盘可用空间；Docker block I/O 是累计字节，不冒充 IOPS。该脚本是有界验收工具，不是全天候监控或报警服务。没有设置未经确认的定时任务。跨服务器部署时镜像应使用注册表 digest；本轮本地 commit tag 与实际 image ID 一并记录，不能把可变 tag 当密码学证明。

## 本轮外部验收状态

实际 VPS 结果见后续本文件更新。短跑通过不能替代多日运行、磁盘老化、主机断电/重启、异地备份与故障恢复演练。跨 VPS 仍需 TLS、认证和网络访问控制，本轮只使用同机内部网络。
