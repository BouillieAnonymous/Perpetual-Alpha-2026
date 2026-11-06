# Visual Alpha Discovery Dashboard V2

入口：[离线终端](release/dashboard.html) · [使用指南](usage-guide.md) · [数据字典](release/data-dictionary.md) · [完整指标资格](release/metric-catalog.json) · [验收记录](acceptance.md)

下载目录后用现代 **Chrome** 双击 `release/dashboard.html`。所有脚本、样式与真实行情都内嵌，**无需服务器、CDN或网络**。首次解压约12MB的HTML数据，使用浏览器内置DecompressionStream；不支持时显示明确错误，不取外部替代数据。GitHub源代码预览不执行HTML。

## 独立范围与证据

新增目录，不覆盖V1 / PR36 / PR38冻结成果。`release/v1-frozen/`为PR36 reviewed产物的原字节副本，含全部五组图、五份PNG、事件、报告、manifest与核验记录。默认导航不再无限堆叠：四工作区、常驻主价格图、最多四辅助图、共享UTC范围和十字光标、past-only历史信息侧栏。全精度特征为features.csv.gz；HTML显示约8位有效数字。

固定来源：PR36 `7322df45a1d057a304794c6739a204a94cd12e33`，PR38 `25730c16019cef4b255758e25c29c01a3635273d`。Stage1 raw/panel原SHA256固定；PR38 points SHA256为`8eeeee4f858197bcbb19eda70648b3ceedadf80294a4dd049b01d6be1345af14`。脚本缺失/不匹配即阻断，不联网抓取或用合成替代。

本轮独立重建42,756资产分钟点，并核对PR38 213,780过去特征单元格；不导入其未来标签列。PR38 beta的历史窗和同期滚动beta不同，UI单独命名。PR38没有PROMISING_EXPLORATORY；这里不重跑旧策略、不建立新Alpha结论。

## 离线复现

用现有项目Python。生成和独立核验仅标准库；PNG使用项目已有`.cache/plot-libs` Matplotlib，不更改生产依赖；交互mock需本机已有Node.js。

```powershell
.\.venv\Scripts\python.exe docs/research/btc-eth-volatility/visual-eda-v2/build_v2.py --output .cache/visual-v2-reproduction
.\.venv\Scripts\python.exe docs/research/btc-eth-volatility/visual-eda-v2/verify_v2.py .cache/visual-v2-reproduction
node docs/research/btc-eth-volatility/visual-eda-v2/verify_interactions.cjs .cache/visual-v2-reproduction
.\.venv\Scripts\python.exe docs/research/btc-eth-volatility/visual-eda-v2/render_png.py .cache/visual-v2-reproduction
```

参数`--cache`指定Stage1 raw/panel目录，`--batch`指定PR38研究目录（须有原points.csv.gz），`--v1`指定PR36原reviewed目录。默认沿本机缓存约定；新设备应显式传入三个已核实目录。PR38不在PR36分支时应使用PR38的独立checkout作输入，不合并其研究或覆写冻结文件。`--output`必须不存在。首次归档是本目录`output/`，交付版是`release/`；仅交付release。输入原始公共行情缓存不随Git提交。

## 验证与停止

独立核对1,136,796单元格，包含中心化OLS斜率、直接协方差相关/Beta、直接样本标准差Z及10/27未来截断下所有历史字段不变。交互mock验证各标签、A/B、资产、固定窗口、UTC、拖动缩放处理与六类事后视图。**这不是Chrome真实验收**，连接问题见acceptance.md。

完成于探索性可视化。没有统计显著性、预测模型训练、参数搜索、策略回测、钱包/生产修改、合并或部署。
