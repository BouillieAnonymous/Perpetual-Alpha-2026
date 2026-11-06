# PAS All-Asset Data Audit V3

固定真实PAS公共前缀：2026-10-19T13:43:00+00:00—2026-10-30T13:17:00+00:00，15815 UTC分钟点，max_seq=337657。不是BTC/ETH的11月3日数据；不拼接、不新采集。

输入SHA256 `fc5ab7a251de06813e9ab74e366d97faee242da96ef0c0f2cd1e722eb861d898`；canonical prefix `62b88879c03aff3cf6b43561409c4bef962704fa3df0e761097df6bc9cfc338b`；全部337657个payload hash核对。复用原normalize/panel并独立到达重建158150资产分钟点。

## 十资产逐项覆盖

|市场|报价覆盖|A收益|B收益|A零收益|缺报价|最长缺口min|spread中位bps|Oracle覆盖|Funding覆盖|Depth覆盖|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
|pas:BTC|99.848%|99.760%|80.486%|3074|24|20|6.078|99.861%|99.994%|99.848%|
|pas:ETH|99.848%|99.760%|91.255%|1425|24|20|6.390|99.861%|99.994%|99.848%|
|pas:HYPE|99.848%|99.760%|93.955%|979|24|20|7.407|99.861%|99.994%|99.848%|
|pas:SP500|99.848%|99.760%|40.521%|9383|24|20|7.067|99.861%|99.994%|99.848%|
|pas:EWY|99.848%|99.760%|83.445%|2646|24|20|6.433|99.861%|99.994%|99.848%|
|pas:EWJ|99.848%|99.760%|53.048%|7434|24|20|5.988|99.861%|99.994%|99.848%|
|pas:BRENT|99.848%|99.760%|75.781%|3904|24|20|6.286|99.861%|99.994%|99.848%|
|pas:GOLD|99.848%|99.760%|56.880%|6822|24|20|5.753|99.861%|99.994%|99.848%|
|pas:USDEUR|99.798%|99.690%|33.856%|10479|32|20|5.356|99.861%|99.994%|99.798%|
|pas:USDJPY|99.842%|99.753%|41.855%|9236|25|20|5.083|99.861%|99.994%|99.842%|

旧十资产book-only EDA漏掉两条归类为book的请求失败。V3按完整normalizer使USDEUR/USDJPY各减少1个有效报价点；A/B收益计数不变。差异及原因见prior-loader-differences.json，原证据未覆盖。全量字段见coverage.csv：各市场原始/盘口起止、重复事件、回退、同事件复用、静止顶层、完整5/15/30/60m窗、spread分位、event/receipt年龄及延迟。A有效新事件的真实零收益保留；B是活跃样本选择，不能解释为全时段波动修正。缺失切断窗，不补零或插值。

## 所有市场对资格

45对分别报告A/B，共90行。A数据协议规划资格通过 45/45，B通过 0/45。数据门槛不是独立样本数、统计功效或可交易性证明。配对不足仍展示名称和INSUFFICIENT_DATA。详见pair-eligibility.csv与固定protocol。

## 合约与时段语义

[官方参与指南](https://perpetual-alpha26.github.io/docs/#asset-references-and-pricing) 于2026-11-05查阅：PAS合约参考加密、股指/ETF、商品和FX；PAS报价为24/7。USDEUR是EUR/USD，不倒置。实际PAS盘口不等于底层ETF、指数或现货成交。发布的参考标的与统一Oracle机制不能证明独立价格发现或历史可交易时差。

|市场|分组|参考/价格单位|历史缓存 lot|
|---|---|---|---|
|pas:BTC|Crypto|Bitcoin / USD/BTC|[1e-05]|
|pas:ETH|Crypto|Ethereum / USD/ETH|[0.0001]|
|pas:HYPE|Crypto|HYPE token / USD/HYPE|[0.01]|
|pas:SP500|Equity reference|S&P 500 reference / index points|[0.001]|
|pas:EWY|Equity reference|iShares MSCI South Korea ETF reference / USD/share|[0.001]|
|pas:EWJ|Equity reference|iShares MSCI Japan ETF reference / USD/share|[0.001]|
|pas:BRENT|Commodity reference|Brent reference / USD/barrel|[0.01]|
|pas:GOLD|Commodity reference|Gold XAU reference / USD/troy oz|[0.0001]|
|pas:USDEUR|FX reference|EUR/USD (ticker is misleading; do not invert) / USD/EUR|[0.1]|
|pas:USDJPY|FX reference|USD/JPY / JPY/USD|[0.01]|

metadata_versions保留当时实际可得序号和时标；当前官方规则仅补充语义，不证明历史每单实际约束。实际账户费率、成交、滑点与历史最小名义UNKNOWN，不沿用BTC/ETH预算或精度。页面默认仅展示市场自身spread；费用/滑点需逐市场显式输入假设。底层交易所历史时段/假日/DST未提供，不用UTC小时伪装交易日历。

## Funding、Oracle、Depth

Oracle/context有独立receipt和TTL，但无上游生成时间；不能从报价相同时间推出同步。Funding按真实settlement event及首次available溯源，重复receipt不算新结算；修订单列。Funding覆盖为管线2h TTL内已知settled rate，不是账户现金流。完整raw L2可逐seq溯源覆盖，但仅分钟异步快照，不是逐笔队列/可成交深度保证；没有秒级盘口图。component-audit.json保留首可得Funding事件与各类原始质量计数。

## 已见样本与停止

只有约11天历史，未覆盖BTC/ETH版剩余尾段，无新留出。45对共享市场和冲击，不是45次独立实验。本轮不做新假设检验、参数搜索、回测或模型训练。历史可视化不推翻PR15/20/27/38的失败与执行未知结论。