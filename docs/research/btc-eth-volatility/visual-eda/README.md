# BTC/ETH reusable visual EDA

入口：[离线 Dashboard](output-reviewed/dashboard.html) · [观察报告](output-reviewed/observations.md) · [事件统计](output-reviewed/summary.json) · [逐事件数据](output-reviewed/events.csv)

双击 `output-reviewed/dashboard.html`，选择本地浏览器打开。HTML 无 CDN / 网络依赖；时序图拖动选择范围、双击恢复全历史，或输入 UTC 起止时间；复选框切换 BTC/ETH 和 A/B，悬停显示 UTC 与原精度数值。事件分布点显示事件 UTC、收益及重叠簇。静态图为 output-reviewed/figure-01.png 至 figure-05.png。

## 复现

在仓库根目录使用已有项目 Python 与已有离线绘图库缓存，不安装或修改生产依赖：

```powershell
.\.venv\Scripts\python.exe docs/research/btc-eth-volatility/visual-eda/build_eda.py --output docs/research/btc-eth-volatility/visual-eda/reproduction
```

参数：`--cache` 指定冻结缓存目录；`--plot-libs` 指定已有 Matplotlib 模块目录。若 Python 已有 Matplotlib，可指定不存在的模块目录，让解释器使用自身环境。输出目录必须不存在。冻结数据缺失或 SHA256 不匹配立即阻断，不重新抓取、不使用合成替代。工具会逐点核对既有 Stage 2A arrival reconstruction 的 A/B 收益与报价序号，复核 Stage 1 全部 16 组 RV 计数。

## 研究与实现边界

先读 [固定方案](protocol.md)。使用 PR #36 固定 head 下载副本 `.cache/btc-eth-stage02a-pr36-input/README.md`（研究根目录 README 本地未落盘）和本地 Stage 1 / Stage 2A 文档、脚本及 JSON。未联网获取最新 PR 状态。本工具导入既有 analyze_stage02a.py 的离线收益/RV函数，不调用其拟合、统计检验或主入口。

数据区间：2026-10-19 13:43—2026-11-03 10:00 UTC，21,378 决策点，公共 PAS Testnet BTC/ETH 异步 REST 报价；真实交易所生产市场的代表性未建立。每张 PNG 页脚与 HTML 顶端注明相同区间和时点规则。归一化基点为首次共同有效 2026-10-19 13:44 UTC；价格比例不是跨缺口可交易累计收益。

图 2 将 missing/expired、同事件复用、时间前进但顶层价不变分开；后者不能被直接认定为陈旧经济信息。图 5 展示所选原始 book 的 event age，即使报价过期仍保留其年龄以展示缺口，收益仍严格无效。有效收益还同时要求 receipt TTL。原始 payload 完全重复、事件 timestamp 重复与顶层价不变不是同一概念；Stage 1 原始事件时间重复计数为零。共同覆盖率采用尾随60个UTC决策点，开始不足60点时使用实际点数，分母未压缩掉缺失。

事件 Q05/Q95 仅由开发段 BTC A 的有符号收益固定，没有搜索阈值。事件是否入选不依赖未来 ETH；只有结果分布按各 horizon 的完整收益可用性取值。CSV 保留每个事件各 horizon 的缺失原因（tail/invalid return）。重叠按任一方向相邻事件间距 <30 分钟标记，方向内簇数量不可相加作为全体簇数。无显著性、置信区间、预测或盈利结论。

既有 `output/` 初版完整保留；本次核验后的默认产物在 `output-reviewed/`。页面另提供三个推荐观察时段的快捷缩放。

## 验证

已校验两份冻结输入 SHA256、42,756资产分钟点的报价序号及A/B收益，以及16组RV窗口数量。已目视检查五份 PNG。HTML 脚本在 Node DOM/Canvas mock 中执行，13个时序面板、2,181事件，验证缩放、UTC悬停、恢复范围；未执行真实浏览器端到端验证。manifest.json 记录输出指纹。

额外独立复核全部 6,543 个事件结果及缺失原因、事件入选不依赖 ETH 未来标签、179 个重叠簇。记录见 [verification.json](output-reviewed/verification.json)。复核命令（需要本机已有 Node.js）：

```powershell
.\.venv\Scripts\python.exe docs/research/btc-eth-volatility/visual-eda/verify_eda.py
```

完成后停止；不覆盖 Stage 1/2A，不触及钱包、服务或交易配置，不合并、部署或启动新统计研究。
