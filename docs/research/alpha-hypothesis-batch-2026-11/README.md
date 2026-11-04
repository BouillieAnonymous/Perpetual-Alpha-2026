# Offline Batch Alpha Research

入口：[summary.md](summary.md)。证据登记：[hypothesis-registry.md](hypothesis-registry.md)；事前规则：[frozen-protocol.md](frozen-protocol.md)；冻结时间/摘要：[freeze.json](freeze.json)。生产交易不在本目录范围。

## 复算

Python 3.11+，仅标准库。代码不导入项目运行时、账户SDK或网络库。当前仓库根目录执行以下命令；必须选择新的输出目录，不能覆盖已归档结果：

```powershell
.venv/Scripts/python.exe docs/research/alpha-hypothesis-batch-2026-11/run_batch.py --output .cache/alpha-batch-new-reproduction
.venv/Scripts/python.exe docs/research/alpha-hypothesis-batch-2026-11/verify_batch.py --output .cache/alpha-batch-new-reproduction
```

跨设备可使用 `python run_batch.py --cache /path/to/stage01-cache --supplemental /path/to/pas-prefix.jsonl.gz --output /path/to/new-output`。保留本目录协议、registry、freeze及证据JSON；verify_batch.py与run_batch.py同目录。无第三方依赖，无生产服务和环境变量要求，不读取.env。

必需输入为input-hashes.json列出的三份已有公共冻结缓存。主cache含normalized-pas-btc-eth.jsonl.gz及causal-minute-panel.jsonl.gz；补充pas-prefix.jsonl.gz含公共盘口、metadata、context和Funding。缓存默认在仓库忽略的.cache目录，没有复制到交付研究目录；跨设备需另行交付相同哈希的公共文件。没有这些输入，脚本明确失败，不重新下载或用其他数据替代。

## 输出与字段

|文件|用途|
|---|---|
|results.csv|93行，包括每个配置/时间段、无效或不足配置、H06/07暂缓、7个FINAL总状态；FINAL为总判断，其他行为各配置的支持/诊断结果|
|events.csv|5,246行候选事件/锚点，包括未通过窗口资格的行、UTC/毫秒时标、特征、标签、原因、报价和条件成本；不同配置可能共享时点，不可直接相加|
|model-comparison.csv|2,681行Baseline/Candidate完全同点比较；H01是预测和误差，H04是同点固定状态与ALL趋势的价格收益；不混淆两种指标|
|daily-comparison.csv|按UTC日聚合的配对误差/价格增量；不称独立日期显著性|
|points.csv.gz|42,756行，A/B各21,378个分钟点，供后续人工可视化；过去特征、未来标签、动态beta/残差/hedge-log-level及其变化独立列出|
|models.json|仅开发样本训练系数、训练日期/计数/时点摘要；支持不足的模型为null|
|thresholds.json|仅开发过去特征确定的BTC abs15 Q90和ETH RV60 median；A阈值供B敏感性共同使用|
|data-audit.json|收益零值/覆盖、补充源身份/metadata精度、Funding去重数量、标准化缓存字段缺失限制|
|input-hashes.json、run-manifest.json|输入和全部计算输出SHA256、运行脚本及冻结协议摘要|
|verification.json、reproduction.json|独立算术/时序核验及两次最终确定性复算结果|
|prior-pr-snapshots.json、source-evidence.json|7个PR固定head说明，以及固定head PR15/20报告和PR25公共配置正文/Git blob|
|implementation-audit/|首次与修正前摘要、实现修复披露；不是额外策略或参数尝试|

时间统一UTC；`t`为Unix毫秒，`i`为主网格索引。窗口收益均为有符号对数收益bps；`beta`无单位；货币条件算式为USDP线性研究单位。`valid`为完整价格/特征资格，不等于可成交；`invalid_reason`保留跨边界/缺口原因。CSV空单元格表示缺失/未知/不适用，绝不代表0；JSON用null。`utc_dates`只统计不同自然日，`nonoverlap_event_n`只计不重叠标签/按规则去重事件，不是IID有效样本估计。

`sampled_touch_pnl_usdp`为异步真实报价的条件算式；`conditional_*`按9bps手续费预算和5bps滑点预算计算，未重建实际账户费率、Funding现金流或双腿成交，深度不足亦保留并标原因。它们都不是可实现收益。`executable_net_usdp`全部空，`verified_trade_n`全0。没有组合资本/保证金或实际策略收益率结论。

`dynamic_hedge_log_level`使用当时beta，变动含再估计项；`fixed_previous_beta_level_change`单独保存固定前一期beta的价格变化，不将动态level变化当交易利润。H03 beta可用split前历史预热，`hedge_uses_pre_split_warmup`明确标记，标签不会进入训练。

图表/看板不在本轮交付范围；事件和minute points可直接用于后续人工可视化。全部历史已见，无干净样本外、统计显著性或部署许可声明。
