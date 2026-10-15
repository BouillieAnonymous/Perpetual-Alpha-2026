# 架构 v0.2｜第一轮实现与运行

2026-10-15，用户确认按架构第 11 节实现**模拟骨架**，批准顶层架构、Forecast 最小字段及模块边界用于本轮实施。真实策略、风险预算、退出规则、模型供应商和自动交易仍待后续决定。[架构规范](architecture-v0.2.zh-CN.md)维护职责与开放决策，本文维护实际代码和运行方法。

## 1. 已实现的链路

`Store → ContextBuilder → ForecastProducer → FixedQuantityPortfolio → Decision 1.0 → DemoRiskGate → MockExecution → ExecutionReport 1.0 → Journal`

传统规则与模拟 Agent 读取同一份冻结 Context，均保存 Forecast；`selected_producer` 指定唯一进入 Portfolio 的版本，MockExecution 再检查该版本。传统规则演示 mark 相对 oracle 的方向判断，不代表盈利策略。模拟 Agent 复用演示规则，并按配置对 ETH 弃权；没有实际模型调用。

| 模块 | 实际文件 | 首轮能力 |
|---|---|---|
| 内部契约 | `src/perpetual_alpha/contracts.py` | Forecast/ForecastBatch、RiskSnapshot、RiskVerdict；类型、JSON 往返、字段/状态/时间/数值校验；ForecastProducer、MarketRiskEstimator、AgentAdapter 接口 |
| 数据通道 | `src/perpetual_alpha/context.py` | 已有 SQLite quotes → StrategyContext 1.0；事件/接收时间过滤、独立数据状态、来源、单位、窗口、截断 |
| 预测与风险估计 | `src/perpetual_alpha/forecasting.py` | rule-demo / mock-agent 可对照；严格边界保留结构化失败；MockRiskEstimator 提供显式假风险数据 |
| 注册与数据协调 | `src/perpetual_alpha/producers.py` | v0.2.1 显式白名单、ID/版本保护、请求去重/冲突与 Producer 独立 Context 视图 |
| 组合 | `src/perpetual_alpha/portfolio.py` | 用户批准的固定数量演示政策；阻断不可用上下文、风险或账户；拒绝混合期限 |
| 独立风控 | `src/perpetual_alpha/risk.py` | 拒绝过期、未来信号、非控制资产、超演示限额；行情/账户/风险不明或挂单未完成时 deferred；绑定决策摘要和快照证据 |
| 模拟执行 | `src/perpetual_alpha/simulation.py` | 从独立模拟账户状态计算差额；accepted 与 completed 分开；持久化去重、ID_CONFLICT、BUSY、unknown 保留 |
| 日志 | `src/perpetual_alpha/journal.py` | 独立 SQLite 日志、模拟仓位和执行状态；事务原子保存；反馈按 observed_ms 过滤 |
| 独立研究路径 | `src/perpetual_alpha/research.py` | 任务、候选、实验、人工审核记录；不可变 ID、版本关联、区间校验、保存/读取、批准/退役状态；无部署接口 |
| 组装与入口 | `src/perpetual_alpha/dry_run.py`、`cli.py` | 离线多资产演示；原有 collect/check/status 入口保留 |
| JSON Schema | `docs/schemas/*.schema.json` | 七类批次/风险/研究产物的 Draft 2020-12 结构规范；Forecast 嵌入 ForecastBatch |

JSON Schema 描述结构、固定版本及状态；概率范围、协方差矩阵、时间关系、ID 关联等跨字段约束由 Python 契约校验执行。修改类型后运行 `python -m perpetual_alpha.schemas` 更新规范；测试检查导出的规范与类型定义一致。

## 2. 运行 Dry Run

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m perpetual_alpha.cli dry-run
# 可显式指定演示配置与独立模拟日志
.\.venv\Scripts\python.exe -m perpetual_alpha.cli dry-run --config config/dry-run.toml --journal data/dry-run/journal.sqlite3
```

输出包含 Context、两份 Forecast、选定版本、RiskSnapshot、Decision、RiskVerdict 和 ExecutionReport。默认 BTC 目标 `0.0002`，ETH `abstain`，实际模拟订单 ID 为空。完整[样例输出](examples/dry-run.json)中的数值全部为演示数据，不是市场或账户实测。

所有行情、账户、风险数值、时间及限额由 `config/dry-run.toml` 显式给出，属于**合成 fixture**。风险矩阵是 mock 数据，不是从历史收益估算出来的风险。顶层输出与每条 Journal event 均有 `mode` / `simulated` 标记；协议 1.0 本身不增加字段，通过 report.message 明确模拟。研究日志 mode 为 research，当前 Registry 的审核范围仅 offline-only，不产生真实执行证据。

Journal 默认保存在忽略的 `data/dry-run/journal.sqlite3`，与采集库分开。同一日志重复运行保留已有模拟仓位，第二次达到相同绝对目标时净成交量为零；每轮产生新 Context/Decision ID。演示时间固定在配置的 asof_ms；更改演示时间/初始账户或场景时使用新的 `--journal` 文件，已有模拟账户不会被初始化参数覆盖。不得让采集数据库与此 Journal 共用文件。

## 3. 用户已确认的演示政策

- `up` → 配置的正数量；`down` → 配置的负数量。
- `neutral`、`abstain`、`error` 和未覆盖资产 → 不提出新目标，保留已有仓位；不自动退出或取消挂单。
- Forecast 已过期，必要数据/RiskSnapshot/账户不可确认 → 不生成可执行目标，记录 portfolio-unavailable。
- 决策有效期不晚于用于目标的预测有效期，预测 horizon 必须与演示风险 interval 相同。
- 风控只批准或拒绝/延后，不修改目标。显式限额检查涉及整个已有与目标组合；合规的纯减仓不因原有仓位超演示限额被阻挡。

Forecast 中各资产的期限可以不同；当前可替换的演示 Portfolio 拒绝混合期限，不擅自转换周期。方向与期限足以运行，不要求模型填概率或收益。

## 4. 请求支持范围与替换接口

ContextBuilder 支持 `source=hyperliquid-testnet/par`、`kind=quote`、空 parameters，字段为 bid/ask/mark/oracle/funding。价格单位 `USDPR/base-asset`，base-asset 由每条 record.symbol 确定；funding 是每小时小数率。每个请求可含一个或多个资产，max_records 是**整个数据集**的上限。窗口只使用 event_ms 和 received_ms 均不晚于 asof_ms 的记录。

`lookback_ms=0` 取每个资产最新记录；正窗口要求有当时已收到的历史覆盖证据。记录上限造成某资产完全缺失时报告 missing。unknown source/kind/fields/parameters 报 error（说明 UNSUPPORTED）；无记录/历史不足报 missing；超过接收或行情源时效报 stale；损坏字段或存储错误报 error。必需数据不 ready 时 Portfolio 阻断，不静默用零或其他资产替代。`account=null` 保留“不知道账户”的语义。

可在离线代码中读取已有采集库，连接为只读，不迁移或改写采集服务：

```python
from perpetual_alpha.context import ContextBuilder
from perpetual_alpha.dry_run import open_collector_readonly
from perpetual_alpha.forecasting import RuleForecastProducer

producer = RuleForecastProducer(("par:BTC", "par:ETH"), horizon_ms=3600000, ttl_ms=60000)
connection = open_collector_readonly("data/quotes.sqlite3")  # 替换为实际库路径
try:
    context = ContextBuilder(connection).build(producer.requirements(), "offline-context", asof_ms=1792000000000)
finally:
    connection.close()
# 未提供可信账户时 context.account 为 None，不能据此执行。
```

新增预测实现遵守 ForecastProducer.requirements()/forecast()，在 `checked_forecast` 边界接入，声明 producer_id/version。默认仅注册 rule-demo/mock-agent；v0.2.1 允许调用方通过 ProducerRegistry 显式注册独立实现，再通过 selected_producer/version 和 shadow_producers 选择。不能通过配置任意 import 或执行研究生成代码。各版本使用同一时点的独立数据视图，只有控制视图进入组合与模拟风险估计，影子缺数据独立记录。注册、时间语义及 LT 示例见[集成接口交接](forecast-integration-v0.2.1.zh-CN.md)。

AgentAdapter 已与 ForecastProducer 统一，附有独立 DummyLTProducer 示例。运行器使用可信完成/接收时钟，检查输入年龄及推理返回预算，异常保留结构化失败。同步调用不强制终止阻塞 Harness，实际供应商、网络超时/取消和只读工具执行器仍需后续验收。替换 Portfolio、Risk Estimator 和 Gate 时保留输入/输出契约及独立检查边界。Trading Intent 仅在架构图预留，没有可执行适配器。

## 5. 研究 Registry 的人工审核

ResearchRegistry 保存在独立 Journal 中。研究任务、候选和实验都是不可变产物；同 ID 同内容幂等，同 ID 不同内容拒绝。候选对应已存在的 task；实验对应准确候选版本，记录数据可用时间、train/validation/test 区间、成本/成交假设、搜索次数、指标、失败案例和可复现入口。它验证元数据，不执行回测，不证明研究代码没有未来数据泄漏。

人工状态链为 `draft → evaluated → approved → retired`，draft/evaluated 也可进入 rejected；rejected/retired 为终态，新版本新建 candidate_id。保存 Experiment 不自动晋升。审核必须提供 reviewer、basis、准确 candidate_version 与已有 experiment_id；批准仅 offline-only，Registry 没有将产物部署到交易进程的能力。[研究样例](examples/research-artifacts.json)展示四类独立产物。

```python
import json
from pathlib import Path
from perpetual_alpha.journal import Journal
from perpetual_alpha.research import ARTIFACTS, ResearchRegistry

examples = json.loads(Path("docs/examples/research-artifacts.json").read_text(encoding="utf-8"))
journal = Journal("data/research/demo.sqlite3")
try:
    registry = ResearchRegistry(journal)
    # 仅导入 task/candidate/experiment。审核记录需人类实际审阅后再创建、保存。
    for kind in ("task", "candidate", "experiment"):
        cls, _ = ARTIFACTS[kind]
        registry.save(kind, cls.from_dict(examples[kind]))
    print(registry.status(examples["candidate"]["candidate_id"]))  # draft
finally:
    journal.close()
```

## 6. 异常语义与当前执行边界

| 情况 | 当前可测结果 |
|---|---|
| 模型/适配器异常或错误证据 | 严格运行器记录 producer-unavailable，阻断该版本；旧双参数 fixture API 在可安全构造时仍返回 error items |
| 必要数据/账户/风险缺失或预测过期 | PortfolioUnavailable；不进入 Execution |
| 独立 Gate 明确违反限额 | RiskVerdict rejected；ExecutionReport rejected |
| 最新账户/行情/挂单不可核实 | RiskVerdict deferred；本次 apply 返回 rejected 及对应原因，延后含义保存在 Verdict 中；后续重新判断需新 Decision ID |
| 相同 Decision ID/相同内容 | 返回持久化原回报，不重复改变模拟仓位，即使重试时已过期 |
| 相同 ID/不同内容 | ID_CONFLICT，记录冲突，保留原执行状态 |
| 同资产已有 accepted/running/partial/unknown | BUSY，不接受新目标 |
| 注入模拟未知结果 | apply(..., simulate_unknown=True) 返回 unknown；重启后 apply/reconcile 仍 unknown，不重发、不假装已核对成功 |
| 模拟完成 | accepted 先记录，随后精确更新目标数量并记录 completed；同一事务保存账户与回报 |

MockExecution 是即时、理想化的模拟：不模拟真实订单队列、滑点、费用、部分成交或反向先平再开；unknown 注入只验证停止/持久化语义，没有网络故障。跨资产瞬时更新的 SQLite 事务不代表交易所原子成交保证。**真实签名工具 `execution.py` / `test_order.py` 没有接入本链路。**

真实上线前仍缺：真实账户/订单核对与恢复、部分成交/撤单/反向分阶段执行、多资产单边风险、保证金/杠杆/清算和精度/最小金额规则、生产风险估计与阈值、退出/停止策略、模型调用及超时、人工部署批准和无人值守故障验收。模拟日志与回报不能作为这些能力已通过验收的依据。

## 7. 验证

```powershell
# Windows 沙箱用项目内临时目录
New-Item -ItemType Directory -Force .cache/test-tmp | Out-Null
$env:TMP = (Resolve-Path .cache/test-tmp).Path
$env:TEMP = $env:TMP
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

2026-10-15：首轮 35 项回归测试继续保留；v0.2.1 新增 22 项，当前共 57 项全部通过。覆盖现有协议/只读/手动交易保护的回归、新契约校验、数据时间冻结、单/多资产、规则/模拟 Agent 对照、neutral/abstain/error、混合期限拒绝、缺数据/过期/账户不明、独立 Gate 拒绝/延后、控制版本、去重与冲突、unknown/BUSY 重启持久化、研究人工状态链以及完整离线 Dry Run。端到端测试封锁 urllib 网络调用和签名 Exchange 构造。

首轮架构实施未运行真实账户/行情网络检查、真实下单或服务器部署。随后于 2026-10-15 添加 [GitHub Actions CI](continuous-integration.zh-CN.md)，已用 Ruff 检查代码，并用第三方 Draft 2020-12 验证器检查全部 Schema 与样例；上述 35 项回归测试继续通过。
