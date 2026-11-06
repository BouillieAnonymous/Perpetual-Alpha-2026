# PAS 十资产 Visual EDA 固定方案

2026-11-04。沿用 BTC/ETH visual EDA 研究纪律，仅作描述性图像观察，不检验假设、不拟合模型、不回测、不采集数据。

输入为既有 `.cache/validation-2026-10-30/pas-prefix.jsonl.gz`，SHA256 `fc5ab7a251de06813e9ab74e366d97faee242da96ef0c0f2cd1e722eb861d898`；canonical prefix SHA256 `62b88879c03aff3cf6b43561409c4bef962704fa3df0e761097df6bc9cfc338b`。来自 PAS Testnet 十资产公共 REST 行情。与 Stage 1 BTC/ETH 的 11月3日快照不同，不拼接两份快照伪造统一历史。

复用 Stage 1 同版本 `pas_research.normalize` 和 `panel`。仅将 book 观测加载到内存派生库；本轮不使用 context/funding。available=max(requested,received,stored)，最新记录无效不能回退，event/receipt TTL 90秒。UTC分钟网格从首个 book 可得时刻所在分钟开始（初点可缺失），结束于十资产最后 book 可得时间的最小值向下取整，包含终点。共同比价在首次十资产共同有效分钟设100，早于基点不展示指数。

A 保留新有效事件的零收益；B 额外要求 bid/ask 改变、事件间隔30–90秒。缺失不补零、不插值、不压缩网格。RV=sqrt(sum(r²))*100，5/15/30/60分钟必须完整连续，不年化。每资产单独面板，Dashboard 可选择资产并对照 BTC；全十资产标准化价格可开关曲线。

事件沿用既有开发区间 [2026-10-20,2026-10-27) UTC BTC A 有符号分钟收益的 Q05/Q95，索引 round(q*(n−1))；阈值只计算一次。按 BTC 当时收益入选，不按任何目标资产未来标签筛选。其余9资产分别报告 t+1…t+h 累计有符号对数收益，h=5/15/30，缺一整窗无效，尾段与内部缺失分开。上下冲击分开；30分钟窗口相交（事件间距<30分钟）标重叠及连通簇，簇不宣称独立。无阈值搜索、显著性、预测或交易收益结论。

全部图注明来源、实际区间、UTC as-of 和缺失规则。全spread仅为 Mid 不变时跨价往返的摩擦参考，不含费用、滑点、深度和延迟。此历史已见，不是干净样本外。保留原两资产产物及所有冻结证据，不触及钱包、交易与服务配置，不合并部署。
