# Perpetual Alpha 2026｜文档入口

更新：2026 年 10 月 19 日。项目维护四份核心正文，另设架构规范、开发基线、实现说明、独立学习资料与按用户要求保存的讨论分析；本页仅导航，不复制进度和参数。

| 文档 | 回答什么 | 放什么信息 |
|---|---|---|
| [PAS CSV 下载与交付记录](pas-ssh-csv-delivery.zh-CN.md) | 队友怎样下载全部或指定区间的数据？ | 已部署入口、Windows/Linux 命令、三类 dataset、验收结果与回滚入口 |
| [PAS Research Data Pipeline](pas-research-pipeline.zh-CN.md) | 原始数据如何变成可复现研究集？ | 只读增量提取、point-in-time 分钟网格、质量/波动/Funding 日报和 CSV；合成示例 |
| [PAS 十资产只读采集](pas-public-collection.zh-CN.md) | 正式比赛数据如何独立记录？ | 每分钟行情、每 30 分钟 Funding、质量标签、独立数据库与部署记录；无交易授权 |
| [PAR/PAS 环境隔离](rehearsal-competition-separation.zh-CN.md) | 排练与正式比赛如何隔离？ | 配置、来源、抵押品、目录身份；预留配置与独立采集入口的区别 |
| [项目总览与任务](project-plan.zh-CN.md) | 谁做什么、现在到哪一步？ | 分工、节点、任务、会议与决定 |
| [开发基线与分阶段实施计划 v0.3](development-baseline-v0.3.zh-CN.md) | 架构审查后决定怎样开发？ | 已确认架构与工程政策、审查反馈、PR0–PR2 首轮范围、后续阶段及验收与权限边界 |
| [Agent Transport 0.1 / PR0](agent-transport-v0.1.zh-CN.md) | 跨进程接入的消息、状态和校验怎样定义？ | 纯契约与 Ingress、三轴状态、Schema、幂等/事务职责和 HTTP 映射；数据库见 PR1，网络待 PR2 |
| [SQLite 任务与 Outbox / PR1](runtime-jobs-pr1.zh-CN.md) | 任务如何原子领取、去重、调度与恢复？ | 真实文件数据库、事务接受与回执、触发合并、Outbox fencing、并发/崩溃测试；HTTP 与执行仍待后续 |
| [双进程 HTTP 闭环 / PR2](http-loop-pr2.zh-CN.md) | Core 和 Dummy 如何独立运行与恢复？ | HTTP 身份与消息、固定 fixture、Worker spool、冻结 Mock Decision、双容器及故障验收；仅模拟 |
| [历史市场数据 / PR3](historical-market-data-pr3.zh-CN.md) | 历史数据何时可见、如何保留修订和质量？ | 独立公开采集、十资产 metadata、已收盘 candle、asof/快照、BTC/ETH 实样；动态 Gateway 待 PR4 |
| [多轮 Data Gateway / PR4](dynamic-data-gateway-pr4.zh-CN.md) | Agent 如何在一次 Job 内多轮查询历史？ | Capability、固定快照、跨 attempt 幂等/额度、质量与 manifest 封存、Shadow 独立进程及容器验收 |
| [只读账户 Adapter / PR5](read-only-account-pr5.zh-CN.md) | 账户信息有哪些可信来源与未验证项？ | Unified 余额/buying power 来源数组、par 持仓/挂单、前后核对、独立账户证据与 unknown；风险就绪仍为 false |
| [市场风险与回放 / PR6](market-risk-replay-pr6.zh-CN.md) | 历史收益与协方差如何可重复计算？ | 显式研究配置、完整窗口、顺序与单位、PSD/奇异、冻结输入封存和离线复算；不批准账户交易 |
| [纯模拟组合与独立风控 / PR7](portfolio-risk-pr7.zh-CN.md) | 组合如何缩量、独立审批和暂停？ | 显式合成预算、完整敞口与过渡角点、审批绑定、默认暂停/显式恢复和旧 Mock 闭环；不接真实账户风险许可 |
| [可恢复合成 Paper / PR8](paper-execution-pr8.zh-CN.md) | 执行中断后如何核对与恢复？ | 同库 Intent/分段成交/报告/Outbox、持久暂停/权益/操作回执、独立 Paper Core HTTP 集成；无真实 venue |
| [隔离 Paper 运维 / PR9](paper-operations-pr9.zh-CN.md) | 如何部署、观察及验证故障恢复？ | 独立容器、存活与就绪分离、WAL 一致备份、隔离恢复、资源及局部故障验收；仅 Synthetic Paper |
| [架构 v0.3 技术审查报告](architecture-v0.3-review.zh-CN.md) | 当前代码与设计还有哪些差距？ | 固定 main 基线的代码证据、19 项问题、兼容性与恢复建议；后续决定见开发基线 |
| [系统架构设计 v0.2](architecture-v0.2.zh-CN.md) | 研究与交易模块如何分工？ | 用户批准用于首轮模拟的架构、Forecast／Portfolio／Risk 边界与开放决策 |
| [架构首轮实现与运行](architecture-implementation.zh-CN.md) | 架构哪些部分已运行，如何替换？ | 离线 Dry Run、演示政策、契约与样例、研究 Registry、异常处理、测试与真实交易缺口 |
| [GitHub Actions 自动检查](continuous-integration.zh-CN.md) | 每次提交自动检查什么，失败如何处理？ | CI 触发条件、服务器同镜像环境、测试与静态检查、查看结果与本地复现 |
| [Forecast 集成稳定化 v0.2.1](forecast-integration-v0.2.1.zh-CN.md) | LT 如何独立接入与对照？ | 时间语义、统一 Adapter、白名单注册、请求协调与 selected/shadow 隔离、可运行 Dummy 示例 |
| [系统接口与部署](technical-plan.zh-CN.md) | 程序怎么接、怎么跑？ | 三段结构、信号接口、成本与风险检查、环境、服务器、部署和恢复 |
| [数据来源与交易规则](data-development.zh-CN.md) | 能交易什么、数据从哪里来？ | 资产、官方接口与限制、外部和历史数据、标准格式与质量 |
| [策略开发与头脑风暴](strategy-development.zh-CN.md) | 哪些想法值得验证、结果怎样？ | 三份外部资料、宏观假设、全部策略候选、Agent／RL、筛选与实验 |
| [Agent 与交易系统学习资料](agent-learning-resources.zh-CN.md) | Agent 怎么工作，传统框架怎样接入？ | 44 个资料条目、逐项说明、结构解释、学习安排；新增 Fin-R1／DeepSeek／Jev |
| [10 月 14 日讨论总结与评论](meeting-2026-10-14-analysis.zh-CN.md) | 本次讨论收敛到什么，还有什么问题？ | 录音独立分析、与网页 ChatGPT 评论的对照、核实结果与首版建议 |

第一次阅读：项目总览 → 系统与部署；查市场和数据时看数据文档，讨论策略时看研究文档。发给队友也使用这套文档，不另写内容重复的版本。

**开发入口：** 先读[开发基线 v0.3](development-baseline-v0.3.zh-CN.md)，再看各 PR 的实现和验收。PR0–PR8 已合并；本分支新增 [PR9 隔离 Paper 运维](paper-operations-pr9.zh-CN.md)，须经过 PR 审核。真实账户/行情风险准入与多日 VPS 运维验收仍未完成。

**找程序结构：** [技术文档第 8 节](technical-plan.zh-CN.md#8-实际程序结构与后续模块)列出本地与服务器路径、实际文件、启动链路和已实现范围。实际代码在项目根目录的 `src/perpetual_alpha/`，不是 `docs/`；启动入口是 `cli.py`。

**与队友对接接口：** [技术文档第 12 节](technical-plan.zh-CN.md#12-三段之间的固定接口协议-10)集中规定可扩展数据需求、输入上下文、目标仓位 Decision 和 ExecutionReport 的 JSON 格式及固定语义。

**10 月 11 日录音讨论：** [会议纪要与会前交付](project-plan.zh-CN.md#9-10-月-11-日队友讨论录音纪要)记录 Agent 方向、未决事项及周三讨论安排；[策略研究拆解](strategy-development.zh-CN.md#17-10-月-11-日录音agent-自适应策略讨论)记录 baseline、自适应与 RL 的待验证问题。

**10 月 14 日录音讨论：** [最新会议纪要](project-plan.zh-CN.md#10-10-月-14-日队友讨论录音纪要)维护事实与交付意向；[独立总结与评论](meeting-2026-10-14-analysis.zh-CN.md)保存录音分析、网页评论对照和建议。Agent 输出与仓位责任见[技术文档第 15 节](technical-plan.zh-CN.md#15-10-月-14-日agent-信号与传统仓位底座对接讨论)。

**Agent 学习与资料：** 独立的[学习资料文档](agent-learning-resources.zh-CN.md)解释 Agent 结构、传统框架接入位置，并逐项介绍综述、论文、公开项目和官方课程；第 10 节已按用户后续要求加入 4–5 小时学习安排。

## 更新规则

- 项目消息放进对应核心文档的现有章节；按用户要求，学习资料集中在独立学习文档维护，本次讨论的分析另存日期明确的评论文档。
- 同一事实只在一个地方详细维护，其他文档用链接引用。例如服务器状态只写技术文档，负责人只在项目总览统一确认。
- 区分官方要求、已确认团队决定、待验证建议和实际完成。配置建议不是官方规则，命令存在不等于部署可用。
- 改动相关章节，清除被替代的旧说法，不靠叠加“以新版本为准”的段落维持多个现行方案。
- 数据、策略或接口发生重要变更时，在项目总览的决定记录中注明；细节仍留在对应文档。

## 原文合并位置

| 旧文档 | 现在的位置 |
|---|---|
| 总规划、队友讨论稿 | 项目总览；技术和研究细节分别归入对应正文 |
| 技术方案、基础结构与部署 | 系统接口与部署 |
| 决策框架 | 技术文档的成本／账户检查；研究文档的策略筛选 |
| 数据开发、资产与交易信息 | 数据来源与交易规则 |
| 研究想法 | 策略研究与实验 |

重组前的八份原稿已保存在仓库本地 `.cache/document-history/` ZIP 快照中。该目录不进入 Git，不作为团队共享文档；项目共享和后续维护以当前四份核心正文为准，学习资料以独立学习文档为准。

原 research-ideas.zh-CN.md 只保留跳转入口；策略正文统一维护 strategy-development.zh-CN.md，学习资料统一维护 agent-learning-resources.zh-CN.md。
