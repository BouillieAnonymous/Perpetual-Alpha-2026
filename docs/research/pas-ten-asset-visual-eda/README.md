# PAS 十资产可复用 Visual EDA

[打开离线 Dashboard](output/dashboard.html) · [观察报告](output/observations.md) · [完整统计](output/summary.json) · [事件明细](output/events.csv)

用本地浏览器打开 `output/dashboard.html`，无需服务器或联网。顶部下拉框可选 BTC、ETH、HYPE、SP500、EWY、EWJ、BRENT、GOLD、USDEUR、USDJPY；勾选 BTC 对照、Strict B；图1可逐条开关十资产曲线。拖动任一时序图同步缩放、双击恢复，也可输入UTC范围或选择末尾24小时。悬停查看UTC及原精度读数。切换资产保留当前时间范围；事件分布固定使用全历史，并明确标注不跟随缩放。

五类独立PNG：[价格](output/figure-01.png)、[收益](output/figure-02.png)、[RV](output/figure-03.png)、[BTC冲击事件分布](output/figure-04.png)、[数据质量与摩擦](output/figure-05.png)。十资产分面，避免十条收益曲线相互遮挡；RV列对应5/15/30/60分钟。小图各用自己的纵轴，跨资产幅度比较应读数。

## 数据边界

区间 **2026-10-19 13:43—2026-10-30 13:17 UTC**，15,815个决策分钟。唯一来源是既有 PAS Testnet 十资产冻结公共REST快照，文件和canonical prefix指纹均匹配先前研究。它比BTC/ETH版短，未拼接11月3日尾段，也没有新采集。PAS衍生报价不等于对应底层指数、商品或外汇现货。

先读[固定方案](protocol.md)。复用Stage 1的原版本normalize/panel；只读book，派生SQLite仅在内存。A保留有效新事件零收益，B要求顶层价改变和30–90秒事件间隔；缺失不补零、不跨缺口。BTC冲击Q05/Q95来自固定开发段，与原两资产EDA数值相同；其余九资产分别统计未来5/15/30分钟有符号收益。标签缺失不影响事件入选，重叠事件及簇不称独立样本。本轮无统计检验、训练、参数搜索或回测。

## 复现与验证

仓库根目录，使用现有项目Python及 `.cache/plot-libs` Matplotlib；不安装或修改生产依赖：

```powershell
.\.venv\Scripts\python.exe docs/research/pas-ten-asset-visual-eda/build_eda.py --output docs/research/pas-ten-asset-visual-eda/reproduction
node docs/research/pas-ten-asset-visual-eda/verify_dashboard.cjs docs/research/pas-ten-asset-visual-eda/reproduction
```

输出目录必须不存在。缺少或哈希不符的冻结缓存立即阻断，无网络或合成替代。需要保留十资产原始快照、Stage 1 BTC/ETH panel（用于重叠核对）、`.cache/pas-launch-prep`原管线、现有 `analyze_stage02a.py` 的RV/分布函数。脚本不调用模型或交易入口。`--input`可指定同哈希的原始快照副本。

已逐点独立核对 **158,150** 个资产分钟的报价seq、有效性、A收益和B资格，BTC/ETH重叠 **31,630** 个点与Stage 1冻结panel完全一致。文件哈希、逐payload哈希、canonical prefix均检查。五张PNG目视检查。Node DOM/Canvas mock验证全部十资产切换、BTC对照、Strict开关、时间范围/拖动缩放、悬停及46,062个事件结果/缺失和重叠标记；另独立复算80条RV序列的1,265,200个有效值/缺失点，确认共同完整窗A/B完全相等。[验证记录](output/dashboard-validation.json)。尚未做真实浏览器端到端测试。

原BTC/ETH版及Stage 1/2A证据保留；不触及钱包、交易、Collector/Monitor/风险配置，不合并部署。交付止于EDA。
