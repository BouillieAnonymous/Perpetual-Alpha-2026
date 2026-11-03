# 同步入口：Final Sprint Audit 与 BTC → ETH Stage 1

本目录保存 2026-11-03 的 Stage 1 数据审计与 Stage 2A 离线关系诊断。Stage 2A 仅执行预先固定的相关与低维 OLS；未开展交易策略回测或生产变更。

## 最新结果：Stage 2A

- [Stage 2A 报告](stage-02a-report.md)、[预先固定方案](stage-02a-protocol.md)。
- [完整统计 JSON](stage-02a-results.json)、[关系指标 CSV](stage-02a-metrics.csv)、[同点评估预测 CSV](stage-02a-predictions.csv)。
- [离线分析脚本](analyze_stage02a.py)、[独立校验脚本](verify_stage02a.py)、[验证记录](stage-02a-validation.json)。

A/B 在共同完整时点的三元 RV 数值完全一致；全段差异来自样本选择。控制 ETH 自身波动后的 BTC 增量较弱且对采样间隔敏感。A 半小时历史验证 MSE 改善2.03%，末段恶化0.63%；B 非重叠样本不足，时序比较标记 INSUFFICIENT DATA。当前不建议进入正式预测回测，未计算显著性。

Stage 2A 复用 Stage 1 冻结行情，不追加数据；PR #36 的 Stage 1 基线固定于 `8c00ec8c0201e70c22cf610c21282f50cb09d7bb`。运行方式、输入路径及缓存限制见报告，脚本拒绝覆盖已有结果。

## Stage 1 入口

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

用户已授权并完成 Stage 2A 离线诊断，研究在此停止。本 PR 不授权正式预测回测、交易策略开发、恢复交易或部署。其他工具继续前应读取两阶段报告中的样本选择、小样本、依赖及已见数据限制，再由用户明确下一阶段范围。

`fetch` 仍需本地已校验的 SSH known-hosts 记录；不降低主机校验，不自动设置环境变量或连接服务器。离线 analyze 不需要 SSH 目标。
