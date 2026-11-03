# 同步入口：Final Sprint Audit 与 BTC → ETH Stage 1

本目录保存 2026-11-03 的研究审计成果；仅完成 Stage 1，没有启动预测拟合、策略回测或生产变更。

## 其他工具从这里开始

1. 阅读 [Stage 1 数据审计](stage-01-data-audit.md)：CONDITIONAL GO，仅支持探索；可靠样本外增量预测及交易改进结论暂不具备条件。
2. 读取 [statistics](stage-01-statistics.json) 与 [diagnostics](stage-01-diagnostics.json)：机器可读计数、H1 窗口与固定分段。
3. 读取 [预先固定口径](stage-01-protocol.md)、[真实数据范围](stage-01-source-range.json)、[发布指纹及依赖边界](publication-manifest.json)。
4. 需要比赛运维背景时阅读 [Final Sprint Readiness Audit](../../final-sprint-2026-11-03.md)。该报告是当时快照，不能当作未来实时状态。

核心结果：共同有效盘口 99.8784%；保守 BTC/ETH 有效分钟收益分别 17,539 / 18,675；H1 含 ETH 自身15分钟基准时，2,335 个重叠窗口、72 个固定半小时标签不重叠窗口、25 个固定90分钟块。块之间仍有依赖，不能当作独立样本。

## 同步边界

本私有仓库 PR 包含完整 Final Sprint 审计及 Stage 1 报告、脚本、统计和来源指纹，且已移除 VPS 地址与主机标识。不包含此前未提交的其他研究、行情数据库或缓存、账户文件、秘密或部署修改。报告引用但未在 main/本 PR 中的历史背景文件已明确标注，不能假定可在新 checkout 找到。

阅读报告和 JSON 不需要 VPS、密钥或额外依赖。**完整离线复算还需要公共行情冻结缓存，PR 本身不包含该数据**。它原位于项目忽略目录 `.cache/btc-eth-volatility-stage01-20261103/normalized-pas-btc-eth.jsonl.gz`，SHA256 为 `c606ebb4b5a273f9385438fa3c3d47e64bc341ec5107aec714e4f0233d2b63db`。跨设备应另行安全传递这份公共行情数据；不要复制任何钱包或 `.env`。

## 复现依赖与脚本

`audit_stage01.py` 的 `fetch` / `analyze` 都需显式调用；`audit_diagnostics.py` 只做离线检查。不要因为打开目录就自动执行 fetch。fetch 使用已有 SSH 主机记录和既有只读导出 runtime，不安装或部署服务；重新抓取会得到新前缀，不能替代原快照复现。

脚本保留原研究工作区路径约定。在新的独立仓库中，按项目固定依赖准备 Python 3.12 环境，并先将已核实的管线版本建立到预期路径（路径必须不存在；不要覆盖现有 checkout）：

```sh
git worktree add --detach .cache/pas-launch-prep 1b9bc798ac8d6109d124a9e5ee4022b579148f1b
```

离线复算时将经过哈希验证的冻结公共行情缓存放入上面的原缓存路径，且结果输出路径必须是新的。脚本拒绝覆盖已有统计；不要删除原始证据绕过保护，可在独立 clone 中运行。使用项目 Python：

```sh
python docs/research/btc-eth-volatility/audit_stage01.py analyze
python docs/research/btc-eth-volatility/audit_diagnostics.py
```

已提交的 JSON 保留最初执行源码指纹。发布版脚本仅作 Ruff 格式、导入、未使用变量和 lambda→def 清理；已在隔离缓存中完整重算，除源码哈希元数据外，全部统计和诊断与原结果完全一致。原执行源码仅保留在本地，其历史哈希仍保存在结果 JSON 中；原始源码因包含主机地址不上传。发布版同时将 SSH 目标改为运行时从 `PAS_AUDIT_SSH_TARGET` 读取；环境变量值由本地操作员提供，文档不填写实际地址。对应关系见 publication manifest。

## 后续任务限制

本 PR 不授权 Stage 2、不授权恢复交易，也不授权任何部署。其他工具若继续研究，应先读取报告中的重复报价/真实零收益歧义、未来标签可用性选择偏差、小样本与已见数据限制，再由用户明确下一阶段范围。

`fetch` 仍需本地已校验的 SSH known-hosts 记录；不降低主机校验，不自动设置环境变量或连接服务器。离线 analyze 不需要 SSH 目标。
