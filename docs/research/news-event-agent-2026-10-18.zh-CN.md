# 新闻事件 Agent 与传统量化信号映射：研究提案

日期：2026-10-18（Europe/London）  
状态：研究提案，供 PR 审阅；未实现采集器、模型、回测或部署，尚无盈利结论。  
来源：用户提供的 ChatGPT 讨论摘要；本次重新核查原始资料与从 main 创建的研究分支源码。

## 1. 要验证的想法

从市场新闻和官方公告中提取可验证的事件事实、语义变化和不确定性，再用传统规则／统计模型将这些特征映射成资产预测，复用现有组合与风控链。

这是一条合理的工程研究路线。基础结构本身不是 Alpha；优势若存在，应来自更及时的数据、相对市场预期的新增信息、合适的持有周期，以及成本后的资产响应。第一版目标是测出新闻信息是否给现有策略带来增量，而不是让模型自主交易。

长期范围包含宏观、公司／行业、加密生态、供需和突发新闻。首轮只研究 FOMC 声明，优先 BTC、ETH；原因是来源可追溯、任务定义明确、已有分类基准，并非认定它是最赚钱或样本最充分的方向。CPI／就业数字发布、讲话、纪要与媒体解读需各自建标签，之后再扩展。

## 2. 三层职责与 Agent 的边界

| 层 | 输入 → 输出 | 首轮候选 |
|---|---|---|
| 事件理解 | 原文与当时已知上下文 → 类型、事实、鹰鸽倾向、证据、不确定性 | 数字／关键词规则、FOMC 分类器、受约束的生成式 LLM |
| 资产响应 | 事件特征、发布前预期与行情 → 各资产各期限的收益／概率预测或弃权 | 固定规则、正则化线性回归、逻辑回归 |
| 组合与执行 | Forecast → 目标持仓 → 独立风控 → 模拟执行 | 复用现有 Core |

Agent 可在受控预算内检索白名单来源、比较前后声明、拆解事件并输出结构化记录。固定单次分类 Worker 已足够完成首轮，不必先构建多 Agent 或开放式自主规划系统。轻量分类模型是对照候选，不应被写成已完成的 LLM Agent。

一个通用事件抽取器配资产级响应模型，优先于十个独立语言模型。分类置信度、事件相关性和价格上涨概率是不同量，不能相互替代；生成式模型自报 confidence 也不等于校准概率。

## 3. 已核查的相关工作

以下链接于 2026-10-18 核查。公开研究支持任务可做，不能证明在 par 市场盈利；本提案没有运行这些模型。

| 资料 | 可借鉴内容 | 证据与使用边界 |
|---|---|---|
| [Shah、Paturi、Chava，Trillion Dollar Words，ACL 2023](https://aclanthology.org/2023.acl-long.368/)；[代码](https://github.com/gtfintechlab/fomc-hawkish-dovish) | FOMC 鹰／鸽／中性分类、标注与市场分析 | 最佳基准为 RoBERTa-large；论文摘要声明其数据、模型、代码为 CC BY-NC 4.0。研究资源许可与论文页面许可应分开核查 |
| [DistilBERT FOMC Classifier](https://huggingface.co/achen0525/DistilBERT_FOMC_Classifier) | 67M 参数的轻量句子分类候选，可实测 CPU 推理 | 模型卡标 Apache 2.0，未提供充分独立评测；其训练数据包含 FOMC 数据，不能据权重标签推断所有上游数据可不受限使用 |
| [ProsusAI FinBERT](https://github.com/ProsusAI/finBERT) | 通用金融情绪基准 | positive／negative／neutral 不等于资产涨跌。仓库 2026-10-12 提醒 Apache 2.0 覆盖代码，权重与 Financial PhraseBank 许可须另核查 |
| [FinGPT](https://github.com/AI4Finance-Foundation/FinGPT) | 金融 NLP 数据管线与微调／评测流程 | 可借鉴组件；不将整套框架引入首版。具体基础模型、数据和适配权重须逐项固定版本 |
| [TypeSafe Jev](https://docs.typesafe.ai/introduction) | typed questions、结构化判断及概率输出 | 托管 API 候选；文档功能描述不是 FOMC 准确率、延迟或交易收益的独立验证 |
| [美联储 FEDS 2025-048](https://www.federalreserve.gov/econres/feds/gauging-the-sentiment-of-federal-open-market-committee-communications-through-the-eyes-of-the-financial-press.htm) | 媒体感知的政策情绪及其意外变化 | 研究显示文本指标具有额外的资产价格解释信息；不能直接转成低延迟可交易收益结论 |
| [Gürkaynak、Sack、Swanson，Do Actions Speak Louder Than Words?](https://www.federalreserve.gov/pubs/feds/2004/200466/200466abs.html) | 区分当前政策目标与未来路径信息 | 给 surprise 特征设计提供依据，不预设资产方向 |

优先阅读顺序：ACL 的任务与标注 → FEDS 的文本 surprise → 当前目标／未来路径分解；随后做规则、FOMC-RoBERTa、DistilBERT 对照。FinBERT 用作领域错配基准；生成式 LLM 和 Jev 在同一冻结输入上作为可选挑战者。模型选择由独立时间外结果、延迟、成本与许可共同决定。

## 4. Surprise 与资产映射

建议把政策事实和市场预期分开保存。例如：实际利率变化 −25 bp、发布前预期 −25 bp，数字 surprise 为 0；若预期为 0，则 surprise 为 −25 bp。这只是计算示例。预期需带发布前时间戳与来源；可用完整概率分布计算期望，不能把事后调查当作当时预期。

文本的“鹰派”也可能已被定价。分别记录政策立场水平、相对前次声明的变化、相对发布前预期的残差。后两者不是同一概念。拿不到可信预期时标记 unknown，只报告语义特征，不伪造 surprise。

对资产 a、期限 h，首轮可比较：

- 规则：预先冻结的特征阈值与符号，成本过滤后决定是否输出方向。
- 回归：预测简单收益 r[a,h] = 截距 + 事件特征系数 + 发布前市场状态系数。
- 分类：以可执行价格和成本定义 up／down／neutral 标签，时间外校准概率。

训练与标准化仅使用过去数据。历史不足时使用资产分组／共享系数或弃权，避免小样本拟合十套复杂模型。鹰派对 BTC、股票、黄金等的影响均只作机制假设；汇率、增长信息与流动性状态可能改变符号。所有资产先由版本化 metadata 确认跟踪标的、报价方向和单位，特别是 par:USDEUR 不能按名称猜方向。

## 5. 数据与时间可得性

首选[美联储官方 RSS](https://www.federalreserve.gov/feeds/feeds.htm)和[FOMC 历史日历／发布页](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)。媒体报道作为独立来源，后续评价其传播延迟、重复和增量；不假设公开网页具有可用的历史首次到达记录。

建议新增研究记录 EventRecord（下表为设计草案，尚非现有 wire schema）：

| 字段组 | 最小内容 |
|---|---|
| 身份／来源 | event_id、event_cluster_id、document_id、source_url、source_type、content_hash、revision_id |
| 时间 | event_time、published_at、first_received_at、revision_received_at、extraction_completed_at；UTC，保留原始时区与精度 |
| 事实／特征 | event_type、文档类别、实际值／单位、预期值与 expectation_asof、立场与语义变化、关联资产 |
| 证据／质量 | 原文片段位置、正文存档引用、缺失／冲突／截断标记、可得性等级 |
| 可复现性 | 模型 revision、prompt／tokenizer／代码版本、参数、输入输出摘要、推理延迟／费用、状态与弃权原因 |

时间规则：

1. 冻结 Job asof 后，只读当时已收到的正文版本、预期和行情。后来更正另存版本，绝不覆盖旧内容或回填接收时间。
2. 决议、记者会、讲话、纪要、媒体转述分别计时；官方说明纪要通常在决议后三周发布，不能用于决议当天的预测。
3. 历史回填正文没有实测 first_received_at：区分“按发布时间与假设延迟回放”和“实际到达记录回放”，结果分开报告。
4. 同一次会议的多句、多篇转载和多资产反应共用事件簇；重复传播不会生成独立统计样本。
5. 历史大模型／现成分类器可能已见过测试文本。记录训练数据覆盖和模型发布日期；未知污染时仅作探索，前瞻 Shadow 才是关键补证。

在预测完成并经过模拟传输延迟之后，才用下一条可执行盘口定义入场。禁止用发布前价格、整根发布后 K 线的最低价或最高价模拟成交。历史外部 BTC／ETH 可用于响应研究；外部资产、主网和 par 合约的流动性、价格与资金费不同，不得合并成同一个收益结论。

## 6. 与当前仓库的接入

本次在从 main 创建的研究分支核对以下源码；实际代码仍以对应开发 PR 的新基线为准：

| 现有位置 | 已有能力 | 后续缺口 |
|---|---|---|
| [contracts.py](../../src/perpetual_alpha/contracts.py) | ForecastProducer、ForecastBatch 与 Forecast 数值／状态字段 | 独立事件抽取记录和 mapper 尚未实现 |
| [context.py](../../src/perpetual_alpha/context.py) | quote 来源、event／received 双时间冻结 | 原 ContextBuilder 不支持 macro_event |
| [transport/data_gateway.py](../../src/perpetual_alpha/transport/data_gateway.py) | 已封存历史 candle 的受控查询与证据 manifest | 需新 provider／能力策略／快照语义，不能将新闻塞进 candle |
| [forecast_validation.py](../../src/perpetual_alpha/forecast_validation.py) | 校验身份、时间、声明数据、资产和证据引用 | 新闻须进入合法 dataset／manifest，URL 不能直接当 evidence_refs |
| [Forecast 集成规范](../forecast-integration-v0.2.1.zh-CN.md) | selected／shadow 分离 | 新 Worker 首轮固定 shadow，不修改 selected producer |

建议管线：

```mermaid
flowchart LR
    SRC[官方公告 / 后续媒体] --> COL[独立采集与版本存档]
    COL --> GW[冻结事件数据与证据]
    GW --> NLP[事件抽取 Worker]
    NLP --> MAP[传统资产响应模型]
    PX[行情 / 发布前预期] --> MAP
    MAP --> FC[ForecastBatch]
    FC --> SH[Shadow 记录与评价]
    FC -.后续审核后的模拟实验.-> PORT[Portfolio]
    PORT --> RISK[独立 Risk Gate]
    RISK --> PAPER[Synthetic Paper]
```

实现必须保留现有协议语义：asof_ms 对齐 context 截止时点；expires_ms 是预测接收有效期，horizon_ms 是预测期限；expected_return 使用简单收益小数字符串，prob_up／prob_down 为资产价格结果的概率，且总和不超过 1。证据引用是已声明且 ready 的 dataset ID，不是原始 URL。模型版本、映射版本和校准版本需可追溯。

缺数据、冲突或低置信度时按既有契约使用 abstain／error；不能用 ready、neutral 隐藏失败。若传统策略只需要事件风险过滤，首轮在离线评估器中组合事件特征与 baseline，不给 Forecast 新增“减仓百分比”或绕开 Portfolio／Risk Gate。

## 7. 首轮实验协议

以下均为待审核的预注册方案，不是已完成实验。实施前冻结事件清单、时间切分、主要指标、成本和参数文件；记录全部试验而非只留最好结果。

| 对照 | 固定条件 | 回答的问题 |
|---|---|---|
| A0 无文本 | 原传统策略 | 原策略表现如何？ |
| A1 日历过滤 | 仅会议日历的固定事件窗口 | 改善是否只是少交易？ |
| A2 规则抽取 | 利率数字、关键词与同一 mapper | 不用模型的基线如何？ |
| B 轻量 NLP | FOMC-RoBERTa／DistilBERT，其他输入与 mapper 固定 | 分类是否改善？ |
| C 事件 + 预期差 | 事件特征加入固定传统响应模型 | 文本是否有价格预测增量？ |
| D 可选生成式 LLM／Jev | 相同正文、截止时点和输出字段 | 复杂抽取是否值得额外成本？ |

两条用途分别验收：一是事件对原策略的开仓过滤，比较 A0／A1／语义过滤，并做相近交易次数／风险暴露的对照；二是独立资产方向预测，比较固定 mapper 下规则与模型输入。过滤收益和方向预测收益不能混写。

时间划分：以会议为组按时间前推训练／验证／测试；同场会议所有文本、资产与重叠持有窗口必须同组。训练标签窗口不得跨入验证／测试，按最大持有期设置 purge／embargo。保留最终时间外集，仅在设计冻结后使用一次；阈值、持有期与模型选择均在训练／验证阶段完成。

首轮建议主期限 60 分钟，5／15 分钟仅作辅助敏感性；这些参数仍须在看测试结果前冻结。输出全事件覆盖、弃权和失败样本，不只统计产生交易的事件。

指标：

- 抽取：macro-F1、各类 precision／recall、混淆矩阵、事实与引用正确率、覆盖与弃权；概率模型另报校准与 Brier score。
- 工程：采集到达、排队、推理和端到端 p50／p95 延迟、超时、修订／去重、成本、CPU／内存。
- 策略：相同资金与时间范围的成本后收益、回撤、换手、敞口、交易数、分资产／时期结果；推理成本另计并给部署规模下摊销假设。
- 成本：双边手续费、bid／ask、额外滑点、持有期资金费和延迟压力场景。报价稀疏时报告数据限制，不声称重建真实成交。

按会议簇计算配对增量与 bootstrap 区间，报告独立会议数，而非将句子、资产或逐笔价格当成独立样本。FOMC 常规一年八次会议，短期 Shadow 更适合检查采集和接口，不能证明稳定 Alpha。官方日历列出的下一场为 2026-10-27–28；此日期不构成上线期限。

## 8. 继续／停止标准与后续小 PR

下表是研究建议，需在实际实验前冻结数值门槛与资源预算；当前研究文档不修改既定开发路线或真实交易权限。

| 阶段 | 交付物 | 验收／停止条件 |
|---|---|---|
| R1 数据与标签 | 官方事件清单、版本存档、许可清单、双人抽样标注与分歧记录 | 时间来源不可追溯则仅列探索数据；缺预期不得宣称完整 surprise |
| R2 离线抽取 | 规则与轻量分类器，冻结输入与输出 artifacts | 时间外 macro-F1、事实／证据质量和延迟有可解释结果；无改善则保留简单规则 |
| R3 响应／过滤回放 | 相同成本与切分下的 ablation、全部失败结果 | 仅分类改善而成本后无增量：不进入方向交易；只优于 A0 而不优于 A1：不能归因于语义 |
| R4 Shadow 接入 | 新数据 provider、证据封存、Worker、弃权与超时记录 | 旧 quote／candle 回归通过；shadow 无 selected 侧效应；重复事件不重复作用 |
| R5 模拟评审 | 前瞻 Shadow 与时间外结果、资源／成本报告 | 稳健改善须优于规则／日历基线，成本压力下不消失，且非单次会议支撑；证据不足则继续观察或停止 |

每项单独 PR 审阅，再决定下一项；接口改动需更新开发基线／决定记录。本轮只新增研究正文与文档导航，不实现 R1–R5，不下载模型或购买数据。

未来实现的关键验收用例：晚到与更正不污染冻结快照；纪要不可穿越到决议时点；相同正文／转载去重；抽取超时／无效结构／低置信度弃权；文本中的指令仅当数据处理；事件 evidence_refs 可在封存 manifest 中解析；shadow 故障不切换 selected；Forecast 期限、单位与概率合法。

## 9. 需要优先补齐的证据

- 有多少可取得原文、发布前预期和可执行行情的独立会议？按来源／年份出覆盖表后再决定模型复杂度。
- 当前 par 采样能覆盖哪些事件窗口？历史外部市场研究与本地前瞻实验分别报告。
- 谁提供标签复核、预期数据和实验审核？负责人尚未指定，不在此虚构团队承诺。
- LLM 相比规则／轻量模型究竟提升哪一项：事实抽取、语义变化、覆盖还是收益？没有可测增量就不增加系统复杂度。

本提案的下一步建议是 R1 的数据可得性审计与 R2 的最小分类对照。先回答“数据和标签是否可信、模型是否增加信息”，再决定是否开发完整新闻 Agent。
