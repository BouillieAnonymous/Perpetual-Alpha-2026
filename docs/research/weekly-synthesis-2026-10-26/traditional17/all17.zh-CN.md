# 原17算法逐项结果

小额盘口情景：每个资产×算法独立研究资本500 USDP，目标上限100名义；实际bid/ask、每边9bps费+2bps滑点。
净收益不是十万比赛账户回报；各行最佳资产是事后描述，不是选择策略。USDJPY不估值。

|算法|中文|有成交资产/9|净正资产/9|最佳资产及净损益USDP|说明|
|---|---|---:|---:|---|---|
|DONCHIAN|通道突破|9|3|BRENT +1.6504|完整逐资产/分段见CSV|
|ATR|ATR大幅变动|9|3|GOLD +2.3831|完整逐资产/分段见CSV|
|DONCHIAN_COMPRESSION|压缩后突破|0|0|无有效成交|预热不足，不缩短168小时压缩参数|
|DONCHIAN_EXPANSION|扩张确认突破|9|3|GOLD +1.3963|完整逐资产/分段见CSV|
|DONCHIAN_COMPRESSION_EXPANSION|压缩+扩张突破|0|0|无有效成交|预热不足，不缩短168小时压缩参数|
|EMA|均线趋势|9|2|BRENT +0.3706|完整逐资产/分段见CSV|
|TSM|时间序列动量|9|2|BRENT +2.7027|完整逐资产/分段见CSV|
|RISK_MOMENTUM|风险调整动量|9|3|BRENT +1.5496|完整逐资产/分段见CSV|
|SIMPLE_MR|短期反转|9|0|USDEUR -1.3723|完整逐资产/分段见CSV|
|ROLLING_Z|标准化均值回归|9|2|ETH +1.6925|完整逐资产/分段见CSV|
|BOLLINGER|布林带（与Z等价）|9|2|ETH +1.6925|与ROLLING_Z相同，不重复计证据|
|REGIME-TREND|趋势状态过滤|9|3|BRENT +0.8876|完整逐资产/分段见CSV|
|REGIME-RANGE|震荡状态过滤|9|5|BRENT +2.1576|完整逐资产/分段见CSV|
|REGIME-COST|成本尺度过滤|8|3|BRENT +0.9198|完整逐资产/分段见CSV|
|REGIME-RANDOM|随机过滤对照|9|3|BRENT +1.5103|随机负对照，不当Alpha|
|FUNDING-LAST|最近Funding方向|9|4|EWJ +2.6720|预测的是价格方向，不是无风险套利|
|FUNDING-MEAN24|24小时Funding方向|9|3|GOLD +3.3305|预测的是价格方向，不是无风险套利|
