# PAS All-Asset Visual Discovery V3

[打开离线Dashboard](release/dashboard.html) · [全资产数据审计](release/all-asset-data-audit.md) · [市场覆盖](release/coverage.csv) · [45对资格](release/pair-eligibility.csv) · [假设登记](hypothesis-registry.md) · [使用指南/数据字典](usage-guide.md)

把 **整个release目录** 保存在本机，用现代Chrome双击dashboard.html。主HTML约87KB；每个市场的压缩数据在assets/下独立JS，首次需要时加载。不是单独发送一个HTML；无需CDN、网络、服务器或生产依赖。矩阵按需加载十市场，只做历史相关；页面最多选四资产比较，pair特征缓存最多两对。

## 固定来源与版本

真实公共PAS十市场prefix截止2026-10-30 13:17 UTC，15,815共同决策分钟，max_seq337657。不是BTC/ETH的11月3日快照，不拼接尾段。原文件SHA256、canonical prefix、pipeline源码LF指纹和所有payload逐条检查，见protocol.md与审计metadata。

复用PR23研究normalizer/panel的已验证本地checkout `.cache/pas-launch-prep`（原Stage1源码指纹329055…），仅在内存派生SQLite。本轮没有新采集、SSH/VPS连接、账户/秘密读取或生产修改。所有历史已见，不是干净OOS。

本轮与旧book-only十资产EDA有明确差异：完整管线纳入两条normalized book request-error，USDEUR/USDJPY各少1个有效报价点，收益计数不变；见prior-loader-differences.json。旧文件与PR36/38/39均未覆盖。

官方参考市场、24/7与价格/数量规则于2026-11-05核对；这是当前公开规范，不证明历史每笔实际约束。尤其USDEUR=EUR/USD，不倒置；USDJPY为JPY/USD。实际费用/滑点及历史最小名义未知，不沿用BTC/ETH参数。

## 复现

项目已有Python环境；不安装生产依赖。完整复算还需同SHA256的公共原始缓存，缓存不随Git提交，缺失/不符立即阻断。

```powershell
.\.venv\Scripts\python.exe docs/research/pas-visual-discovery-v3/audit.py --output .cache/v3-audit-reproduction
.\.venv\Scripts\python.exe docs/research/pas-visual-discovery-v3/build_v3.py --audit .cache/v3-audit-reproduction --output .cache/v3-view-reproduction
.\.venv\Scripts\python.exe docs/research/pas-visual-discovery-v3/verify_v3.py --audit .cache/v3-audit-reproduction --release .cache/v3-view-reproduction
node docs/research/pas-visual-discovery-v3/verify_interactions.cjs .cache/v3-view-reproduction
```

`audit.py --input`指定冻结prefix，`--pipeline`指定同LF指纹的pas_research.py。默认约定沿原研究缓存；跨设备须保留原模块相对包目录（包括venue_collection/venues/runtime依赖），不要只复制单个模块。新output必须不存在。audit-verified/是最终审计目录，audit/是首次发现旧loader差异时的未完成中间输出，仅本地保留、不作为发布证据。

## 输出与验收

- audit-verified：完整因果panel、metadata、原始组件覆盖、Funding首次可得/修订、覆盖/配对CSV、审计报告及输入指纹。
- release：轻量页面、十资产lazy bundles、指标有效数、审计副本、独立数字参考、验证记录、真实Chrome截图。
- hypothesis-registry.md：H08–H13逐项机制、反证、历史特征/未来标签候选、baseline、混杂、重复与数据/执行阻断；没有计算候选标签。

A45对通过固定工程门槛；B45对均未通过90%共同活跃覆盖。该门槛不是功效或可交易性证明，B仍可看单资产数据和缺失。H09仅优先协议规划；H08及H10的普通版本保留重复状态，不为凑三个而推荐。

验收使用Chrome GPT扩展，未使用Edge或Windows Computer Use技能。具体通过项和剩余限制见[acceptance.md](acceptance.md)。所有算术/mocks与真实浏览器记录分开。完成后停止，不合并、部署或自动进入假设检验。

Phase1的validation.json保留当时独立截断核验尚未运行的记录；最终状态以release/final-validation.json、independent-validation.json和chrome-acceptance.json为准，不覆盖阶段记录。
