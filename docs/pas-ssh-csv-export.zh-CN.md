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
test -x /opt/alpha-csv-export/venv/bin/python || sudo python3.12 -m venv /opt/alpha-csv-export/venv
sudo /opt/alpha-csv-export/venv/bin/pip install /path/to/reviewed/checkout
# 已有同名账号时先核实用途，绝不把现有管理员/其他业务账号改作导出账号。
if getent passwd alpha_export >/dev/null; then
    test "$(getent passwd alpha_export | cut -d: -f6-7)" = '/var/empty/alpha-export:/bin/sh' || exit 1
    test "$(id -nG alpha_export)" = 'alpha_export' || exit 1
else
    sudo useradd --system --user-group --no-create-home --home-dir /var/empty/alpha-export --shell /bin/sh --password '*' alpha_export
fi
# 经上述核实后，首次安装/从旧文档迁移/重复执行均设为不可用密码哈希；不执行 passwd -l。
sudo usermod --password '*' alpha_export
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
重复部署保留已有公钥文件，不覆盖、不重复追加同一公钥；先检查公钥指纹。
`*` 无法作为有效密码匹配，密码/交互式认证仍由专用 Match 显式关闭。不要用 `passwd -u`、
空密码或 `passwd -l` 代替该创建方式；检查 `chage -l alpha_export` 无账号过期/密码强制变更。
CI 使用 Ubuntu 实际 `/etc/pam.d/sshd`、`UsePAM yes` 和同样的 system-user/`*` 创建参数。
现场 PAM、AllowUsers/Groups、账号有效期等仍须逐项验收，不能把 CI 当作 VPS 已验收。

### 数据 ACL 与 WAL

不要更改 Collector UID 或整个目录的写权限。管理员先 `namei -l`、`getfacl` 检查已有权限，
将原 ACL 保存到受保护的管理员目录用于回滚；首次保存后不覆盖基线，重复 setfacl 设置同一条目不会重复增加权限。按实际目录为 alpha_export 授予父目录 traverse、
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

**模板只放在主 `/etc/ssh/sshd_config` 的最后，所有全局指令之后。**
不要把它装入开头加载的 `/etc/ssh/sshd_config.d/*.conf`，也不要追加 `Match all`：
`Match all` 仍是 Match 上下文，不会恢复全局上下文。模板末尾没有该指令。
以后需要新增全局设置时必须插在受管理区块之前。

`scripts/render_ssh_export_config.py` 从 stdin 生成 stdout 候选配置，不直接写系统文件或 reload。
首次追加带 BEGIN/END 标记的 EOF 区块，重复执行只替换该区块；若标记重复、损坏或区块后
存在其他指令会拒绝，要求人工审阅，不会删除其他人的配置。`--remove` 只移除自己的末尾区块，重复执行无害。

管理员先保存一次主配置、相关 include 文件、ACL、账号状态及当前 commit，存入 root-only 备份目录；
重跑不得覆盖最初基线。若先前按旧文档安装过 `00-alpha-export.conf`，先备份并仅移走这个已确认属于
本功能的旧文件到备份目录；不得批量移动整个 sshd_config.d。配置修复完成前不要 reload。
现有任何较早、同样匹配 alpha_export 的 Match 都可能先取得选项，需检查并解决冲突。

```sh
# 在已审阅 checkout 中；管理员 shell umask 077，候选仅 root 可写。
sudo sh -c 'umask 077; python3 /path/to/reviewed/checkout/scripts/render_ssh_export_config.py < /etc/ssh/sshd_config > /etc/ssh/sshd_config.alpha-export.candidate'
sudo sshd -t -f /etc/ssh/sshd_config.alpha-export.candidate
sudo sshd -T -f /etc/ssh/sshd_config.alpha-export.candidate -C user=alpha_export,host=localhost,addr=<TEAMMATE_IP>
sudo sshd -T -f /etc/ssh/sshd_config.alpha-export.candidate -C user=<ADMIN_USER>,host=localhost,addr=<ADMIN_IP>
```

在安装候选前，对比管理员现有 `sshd -T -C ...` 与候选输出应完全相同；
确认导出用户的 forcecommand、disableforwarding、permittty、passwordauthentication、
authenticationmethods、authorizedkeysfile 均正确，`UsePAM yes` 与现场 PAM 账号检查通过。
检查 AllowUsers/Groups、证书 CA、AuthorizedKeysCommand、环境传入等现有策略；不为导出账号放宽其他访问。
全局有效 `PermitUserEnvironment no`（该选项不能放入 Match）；不能 AcceptEnv PYTHON*/LD_*/ENV/BASH_ENV。

```sh
# 保留原配置的 owner/mode，仅在完成上述对比后同目录原子替换；候选文件 root:root。
sudo chown --reference=/etc/ssh/sshd_config /etc/ssh/sshd_config.alpha-export.candidate
sudo chmod --reference=/etc/ssh/sshd_config /etc/ssh/sshd_config.alpha-export.candidate
sudo mv /etc/ssh/sshd_config.alpha-export.candidate /etc/ssh/sshd_config
sudo sshd -t
sudo systemctl reload ssh
```

保留原管理员 SSH 会话，先验证管理员仍能新建连接。先配置限制并验收，再分发使用说明。
ForceCommand 会覆盖普通命令及 subsystem，scp/sftp 请求也会被白名单拒绝。
全局 MaxStartups 等维持管理员策略；MaxSessions 只限制每连接，真正跨连接并发由 flock 限制。

## 验收与回滚

离线测试：`python -m unittest discover -s tests -p 'test_pas_csv_export.py' -v`。
覆盖全量、UTC 半开区间、资产、funding 版本、逐字段面板对照、原库写操作拒绝、
导出中追加与 WAL truncate checkpoint 成功、新增记录不进入当前导出、命令注入和超时错误。
Linux 测试额外覆盖同锁互斥和资源限制。CI 还复制 Ubuntu 已安装的主 sshd_config，保留
开头 Include → 后续 UsePAM/Subsystem 的实际顺序，用早期错误模板证明回归，再验证 EOF 安装、
真实 PAM 公钥认证、重复生成/移除及管理员 effective config 和新连接不受影响。真实账号/sshd/ACL 验收须日后部署阶段完成，
不能用 Windows 单元测试替代：

- 队友公钥下载三类数据，对照 seq/样本数/质量字段；两条连接同时导出第二条应拒绝。
- `ssh ... id`、空命令、`sh`、`export --all; id`、路径/SQL 参数、scp/sftp 必须失败。
- `ssh -tt` 无 PTY；`ssh -N -o ExitOnForwardFailure=yes -R 0:localhost:22 ...` 必须拒绝转发。
- 强制密码认证应失败；管理员 `sudo -u alpha_export` 验证原 DB/WAL 不可写、目录不可创建文件、
  launcher/venv/authorized_keys 不可写，交易密钥和部署目录不可读。
- 慢下载和断开连接后锁释放；无 CSV 文件残留，Collector 继续追加，checkpoint/磁盘增长正常。

### VPS 现场验收（部署时执行；本 PR 未连接 VPS）

先只配置管理员持有的测试公钥，保留现有管理会话，用另一终端新建管理员公钥连接执行 `id`；
确认无密码提示、无强制导出命令。记录 `sshd -V`/Ubuntu 版本、PAM 配置及上述 effective config。
用测试公钥完成全量/区间/资产/三 dataset 下载，比较原库序号范围和样本数；确认测试账号
shell/SQL/路径参数拒绝、密码拒绝、PTY/转发拒绝，不能读管理员钱包/密钥或部署目录。
只通过现有的只读运维状态检查核对 PAS/PAR Collector 前后仍运行并继续追加，不停止或重启它们。
验证数据库/WAL/SHM 与目录不可写、慢读/断线后锁释放、WAL 增长正常。
sidecar 重建的验证等日后已计划的 Collector 维护窗口完成；不得为本次验收重启 Collector 或交易服务。
全部通过后再按指纹加入队友公钥并撤销测试公钥。任何一步失败先撤销导出入口，不放宽权限绕过。

### 幂等回滚

先撤销本功能的公钥，结束 alpha_export 的已连接会话（撤 key 不会断开旧连接）；保持强制命令
区块直到账号过期/失效并确认无会话。`chage -E 0 alpha_export` 用于禁用账号；仅锁密码不能替代撤销公钥访问。
已不存在账号/公钥时跳过对应步骤，不操作其他账号。重复回滚前检查运行中的导出进程与锁。

用同一 renderer 的 `--remove` 从**当前**主配置生成 rollback candidate，`sshd -t -f` 并检查管理员
配置后原子安装、再 `sshd -t` 和 reload；不会恢复整份旧主配置覆盖后来他人的变更。
如区块后有新配置，renderer 会拒绝，人工仅删除 BEGIN/END 范围并验证。旧错误 drop-in 不应恢复。
根据首次保存的 ACL 恢复本功能相关条目，检查有无后来业务 ACL 变更，不盲目覆盖整目录权限。
最后仅删除本功能专用账号、launcher、venv、tmpfiles 配置；存在时才删除，公用父目录保留。
不删除 PAS 数据库、Collector、交易服务或其他 SSH 配置。仍有导出进程时不删除/重建锁文件。
回滚后重新验证管理员新连接、Collector 只读状态检查、测试公钥被拒绝。

参考：[OpenSSH sshd_config](https://man.openbsd.org/sshd_config)、
[SQLite WAL](https://www.sqlite.org/wal.html)、[SQLite URI](https://www.sqlite.org/uri.html)。
