# 参考价偏离策略：首轮离线研究

2026-10-17。本 PR 只做传统 ForecastProducer 与离线采样回放，不部署、不启动服务、不发送订单。假设：比赛卖一显著低于 oracle 时看涨，买一显著高于 oracle 时看跌，等待回归。它是有方向风险的单腿策略，没有外部对冲，不能称为无风险套利。

## 找到的数据

从现有服务器 collector 数据库以 SQLite `mode=ro` 导出公开数据。BTC 25,032 条、ETH 25,018 条，事件窗口为 2026-10-08 01:50:42 至 2026-10-17 01:06:09 UTC。约 30 秒采样，保留双边盘口、深度、oracle、资金费率与接收时间；导出时原采集服务继续运行，未改服务或数据库。6 条没有可信参考价接收时间的记录被排除。

这里的 oracle 是比赛提供的外部综合参考价代理，**不是独立下载的外部合约历史**。比赛官方说明 oracle 使用 SEDA 综合源，市场与价格定义见 [比赛指南](https://perpetual-alpha26.github.io/docs/)。实际外部对冲需要匹配合约、币种、时钟和两边可成交深度，未在本版实现。

已有 candleSnapshot 可以补 K 线，但缺少历史买卖价、深度和同时可用的 oracle，不能代替本实验的盘口输入。[Hyperliquid 历史数据文档](https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data)列出 requester-pays S3 盘口档案；没有验证该档案覆盖 testnet/par，也没有进行付费下载。

## 固定实验与结果

每笔名义金额上限 100，BTC/ETH 数量分别按研究假设 0.00001/0.0001 向下取整（未验证为当前交易规则）。单资产最多一仓；固定持有 5 分钟。单边手续费假设 5 bps、额外滑点 2 bps，不冒充实际账户费率。至少等待 1 秒后在下一张有效快照重新验证信号；超过 90 秒取消信号。入场和平仓均要求顶层数量覆盖整笔，不模拟穿透盘口；平仓不足时等待，可能延长持有期。以实际下一张采样价成交仍可能高估可执行性，不能重建这段时间的撤单、竞速或逐笔成交。

资金费按入场与平仓所见绝对小时费率较大者、实际持有时间计保守压力成本，不是实际结算重建；它也不能保证覆盖期间费率峰值。没有保证金、清算、最小订单金额、全账户资本及经过验证的合约估值适配器，因此不报告可投资收益率或 Sharpe。

按时间中点切成前后两段，各自从空仓开始，参数不根据后段挑选。两段均单独报告，不连接为连续净值。

| 入场阈值 | 前段平仓数 | 前段已实现净损益 | 后段平仓数 | 后段已实现净损益 |
|---|---:|---:|---:|---:|
| 10 bps | 218 | -27.74 | 288 | -58.79 |
| 20 bps | 126 | -16.41 | 3 | -0.47 |
| 50 bps | 24 | -3.60 | 0 | 0.00 |

10 bps 后段另有 1 笔未平仓，标记 incomplete，表中不包含它的未实现损益，不能把该列当作完整组合损益。50 bps 后段没有成交，零值不代表策略稳定或风险为零。第一轮不能支持成本后盈利结论；需要更密集盘口和独立外部参考数据，再检验更短持有期、退出规则或双腿策略，不能把本表当作调参后验证。

## 重现与架构接入

`BasisForecastProducer` 继承现有规则 producer 的数据声明，实现 `requirements()/forecast()`；通过现有 `ProducerRegistry.register()` 显式注册，可交给 `checked_forecast`。默认运行配置未切换。它只发方向 Forecast，数量／组合／独立风控仍由现有下游负责。当前 quote context 不携带 oracle 独立接收时间；producer 仅依赖网关 ready 与报价时间，回放额外检查 oracle 接收时间，因此不是实时运行批准。

```powershell
# 在已有数据库的机器上只读导出；不要热拷贝活跃 WAL 主文件。
python scripts/export_basis_history.py /path/to/market.sqlite3 > quotes.jsonl
.venv/Scripts/python.exe -m perpetual_alpha.basis_research quotes.jsonl --output replay.json
.venv/Scripts/python.exe -m unittest discover -s tests -p test_basis_research.py -v
```

本轮原始导出与逐笔结果在本地 `.cache/basis-market-history.jsonl`、`.cache/basis-replay.json`；公开数据未包含账户或密钥。输入 SHA-256、覆盖范围、固定参数和摘要归档于 [basis-replay-summary.json](examples/basis-replay-summary.json)。完整原始数据未提交 Git，换一轮导出可能增加尾部记录；只有相同输入文件才能复现相同摘要。
