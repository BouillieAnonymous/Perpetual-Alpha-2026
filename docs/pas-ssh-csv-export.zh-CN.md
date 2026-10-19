# PAS 只读 SSH CSV 导出（待部署）

本 PR 只提供代码、受限 SSH 模板和验收步骤；不创建线上账号、不部署、不修改 Collector 或交易系统。
队友未来使用独立的 `alpha_export` 公钥账号，只有导出能力，没有管理 shell、sudo、Docker 或部署凭证。

## 使用

```sh
ssh -T alpha_export@<VPS_IP> 'export --all' > pas_all.csv.part && mv pas_all.csv.part pas_all.csv
ssh -T alpha_export@<VPS_IP> 'export --from 2026-10-19T12:00:00Z --to 2026-10-19T18:00:00Z' > selected.csv.part && mv selected.csv.part selected.csv
ssh -T alpha_export@<VPS_IP> 'export --symbol BTC --all --dataset funding' > funding.csv.part && mv funding.csv.part funding.csv
ssh -T alpha_export@<VPS_IP> 'export --all --dataset minute-panel' > panel.csv.part && mv panel.csv.part panel.csv
ssh -T alpha_export@<VPS_IP> 'export --all --raw-json' > raw.csv.part && mv raw.csv.part raw.csv
```

上例为 POSIX shell；Python subprocess 应使用参数数组、`shell=False` 并检查 returncode，成功后才使用 CSV。
Windows 使用支持原始字节重定向的 PowerShell 7.4+，或 Python 以二进制文件接 stdout。
失败可能已经输出 CSV 前缀；非零退出必须丢弃 `.part`，不能当作完整下载。

默认 observations 保留每次收据，调用已有 normalize 校验 identity、市场、时钟、SHA256 和盘口质量。
`--raw-json` 额外保留原始请求/响应 JSON；CSV 的引号转义由标准 csv 模块处理。
funding 保留重叠响应和修订，绝不按结算时间静默去重。
observations/funding 的时间筛选均为 **available_ms**，不是资金费结算 event_ms；UTC `[from,to)`。
minute-panel 按 decision_ms 筛选，边界须整分钟，复用已有 panel 的独立 as-of/TTL/空值/收益算法，
起点前一分钟预热；不是 K 线，不是账户/订单数据。
`--all` 表示此次导出开始时已提交的全部序号前缀，排除后来新增的记录。
全量分钟面板覆盖最早 available 所在分钟至最后 available 所在分钟，最后一分钟不使用后来数据。

## 一致性、限制

固定唯一源 `/opt/perpetual-alpha-pas-data/pas/public.sqlite3`，无调用者路径、SQL 或输出文件参数。
只用 URI mode=ro、query_only，不使用 immutable、不修改 schema、不 checkpoint、不迁移、不创建原库。
每批最多 256 个观测，fetch 完立即关闭游标，然后才写 stdout；慢客户端不持原库读事务。
依赖 Collector 的 append-only 约定；导出期间不得替换、截断或修改已有记录，否则短事务不能保证一致快照。

全账号一份 root 管理的 flock 锁（跨 SSH 连接）；忙时立即失败。墙钟 300 秒（含慢 stdout）、
CPU 120 秒、地址空间 512 MiB、32 个文件描述符、禁止 core dump，SQLite busy 1 秒、每条 SQL 5 秒。
导出仍会使用 CPU/I/O；不是无限历史的固定耗时保证。大库超限应按日期切片重试。
minute-panel 在内存 SQLite 保存滚动窗口；因原库无时间索引，时间排序分页会重复扫描只读前缀，
大库可能首先达到查询时限。这是保持 Collector/schema 不变的取舍，不声称全量大库压测通过。
不会在服务器保存 CSV 或派生数据库；SQL 排序 temp_store=MEMORY 也受内存上限控制。

## 部署（管理员日后手动执行）

先选定审核后的 commit，在独立、root 持有且队友不可写的源码目录构建安装；不用现有交易 venv：

```sh
sudo install -d -o root -g root -m 0755 /opt/alpha-csv-export
sudo python3.12 -m venv /opt/alpha-csv-export/venv
sudo /opt/alpha-csv-export/venv/bin/pip install /path/to/reviewed/checkout
sudo useradd --system --user-group --home-dir /var/empty/alpha-export --shell /bin/sh alpha_export
sudo passwd -l alpha_export
sudo install -d -o root -g root -m 0755 /var/empty/alpha-export
sudo install -o root -g root -m 0755 deploy/ssh-export/alpha-csv-command /usr/local/bin/alpha-csv-command
sudo install -o root -g root -m 0644 deploy/ssh-export/tmpfiles.conf /etc/tmpfiles.d/alpha-export.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/alpha-export.conf
sudo install -d -o root -g root -m 0755 /etc/ssh/authorized_keys
```

`/bin/sh` 仅供 sshd 执行固定 launcher；不能设 nologin，否则 ForceCommand 也不能执行。
账号无可写 home、无 sudo/docker 组；不得加入管理组。root 写入队友**公钥**到
`/etc/ssh/authorized_keys/alpha_export`，每行 `restrict ssh-ed25519 AAAA... label`，root:root 0644。
文件不能含私钥，队友无权改公钥文件、launcher、venv 或其父目录。

### 数据 ACL 与 WAL

不要更改 Collector UID 或整个目录的写权限。管理员先 `namei -l`、`getfacl` 检查已有权限，
将原 ACL 保存到受保护的管理员目录用于回滚。按实际目录为 alpha_export 授予父目录 traverse、
PAS 数据目录 read/traverse；给 `public.sqlite3`、`-wal`、`-shm` 只授予 read。
例如（需已有 acl 工具，数据路径核实后执行）：

```sh
sudo setfacl -m u:alpha_export:--x /opt/perpetual-alpha-pas-data
sudo setfacl -m u:alpha_export:r-x /opt/perpetual-alpha-pas-data/pas
sudo setfacl -m u:alpha_export:r-- /opt/perpetual-alpha-pas-data/pas/public.sqlite3
sudo setfacl -m u:alpha_export:r-- /opt/perpetual-alpha-pas-data/pas/public.sqlite3-wal
sudo setfacl -m u:alpha_export:r-- /opt/perpetual-alpha-pas-data/pas/public.sqlite3-shm
sudo setfacl -m d:u:alpha_export:r-- /opt/perpetual-alpha-pas-data/pas
```

目录应仅存放 PAS 公共数据，default ACL 会作用于未来新文件；核查 ACL mask 不扩大其他用户权限。
WAL/SHM 由 Collector 创建和维护。SQLite 支持已有且可读 sidecar 下的只读 WAL；缺失/重建窗口可能读取失败，
此时非零退出，稍后重试，绝不能给导出账号目录写权限或采用 immutable 绕过。
在 Collector 持续运行、重启、sidecar 重建三种情况下复验 ACL 和读取能力。

### SSH 配置

审阅部署模板后，安装为 `/etc/ssh/sshd_config.d/00-alpha-export.conf`（确保主配置包含该目录）。
模板以 `Match all` 结束。检查没有重叠 Match、AllowUsers/Groups、证书 CA、AuthorizedKeysCommand、
环境传入等设置扩大此账号权限；全局有效 `PermitUserEnvironment no`（该选项不能放入 Match）；只允许必要 locale，不能 AcceptEnv PYTHON*/LD_*/ENV/BASH_ENV。

```sh
sudo sshd -t
sudo sshd -T -C user=alpha_export,host=localhost,addr=<TEAMMATE_IP>
# 确认 forcecommand、disableforwarding、permittty、passwordauthentication 等有效配置后：
sudo systemctl reload ssh
```

保留原管理员 SSH 会话，先验证管理员仍能新建连接。先配置限制并验收，再分发使用说明。
ForceCommand 会覆盖普通命令及 subsystem，scp/sftp 请求也会被白名单拒绝。
全局 MaxStartups 等维持管理员策略；MaxSessions 只限制每连接，真正跨连接并发由 flock 限制。

## 验收与回滚

离线测试：`python -m unittest discover -s tests -p 'test_pas_csv_export.py' -v`。
覆盖全量、UTC 半开区间、资产、funding 版本、逐字段面板对照、原库写操作拒绝、
导出中追加与 WAL truncate checkpoint 成功、新增记录不进入当前导出、命令注入和超时错误。
Linux 测试额外覆盖同锁互斥和资源限制。真实账号/sshd/ACL 验收须日后部署阶段完成，
不能用 Windows 单元测试替代：

- 队友公钥下载三类数据，对照 seq/样本数/质量字段；两条连接同时导出第二条应拒绝。
- `ssh ... id`、空命令、`sh`、`export --all; id`、路径/SQL 参数、scp/sftp 必须失败。
- `ssh -tt` 无 PTY；`ssh -N -o ExitOnForwardFailure=yes -R 0:localhost:22 ...` 必须拒绝转发。
- 强制密码认证应失败；管理员 `sudo -u alpha_export` 验证原 DB/WAL 不可写、目录不可创建文件、
  launcher/venv/authorized_keys 不可写，交易密钥和部署目录不可读。
- 慢下载和断开连接后锁释放；无 CSV 文件残留，Collector 继续追加，checkpoint/磁盘增长正常。

回滚先从 root 管理公钥文件撤销该 key，管理员结束 alpha_export 现有会话（撤 key 不会结束已连接客户端）；
保留强制命令配置直到账号失效并确认无会话。锁定/过期账号，撤销新增 ACL/default ACL（按保存的 ACL 恢复），
再移除该专用 SSH 配置并 `sshd -t`、reload。最后移除专用 launcher、venv、tmpfiles 配置和账号；
不删除 PAS 数据库、Collector、交易服务或其他 SSH 配置。不得在仍有导出进程时重建锁文件。

参考：[OpenSSH sshd_config](https://man.openbsd.org/sshd_config)、
[SQLite WAL](https://www.sqlite.org/wal.html)、[SQLite URI](https://www.sqlite.org/uri.html)。
