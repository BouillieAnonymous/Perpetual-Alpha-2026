# Forecast 集成稳定化 v0.2.1｜LT 接口交接

基线为 2026-10-15 获取的最新 main `c6ca2ab`；按用户提供的 Integration Stabilization v0.2.1 任务书修复四项 P0，供 10 月 18 日团队集成。Architecture v0.2 保留。本页是实际接口和验收说明，不构成真实自动交易批准。变更通过独立分支和 PR 交付，等待团队审核，不直接合并 main。

## 1. 唯一预测接入边界

传统策略与 LT Trading Agent 的外层 Adapter 均实现 `contracts.ForecastProducer`：

```python
producer_id: str
producer_version: str
requirements() -> tuple[DataRequest, ...]
forecast(context: dict) -> ForecastBatch
```

`AgentAdapter` 继承相同协议。Adapter 不需要继承 Rule/Mock 类，可以包装 LT 已有的 Harness；内部如何推理由 LT 实现。它只输出 `forecast/0.1`，不返回订单或 Decision，不接触签名权限。旧 `interfaces.Strategy.requirements()/decide()` 协议继续保留，但不是本次 LT 的推荐接入点。

`Forecast`、`ForecastBatch`、DataRequest/Decision/ExecutionReport 1.0 及 RiskSnapshot/RiskVerdict 的字段、类型和 JSON Schema 均未变更。可选收益/概率/波动/分位数仍不必填写，旧合法载荷继续能解析。v0.2.1 是集成实现版本，不是交易或 Forecast 协议升版。

## 2. 时间与错误边界

| 时间 | 含义与校验 |
|---|---|
| Context.asof_ms | 可见信息截止时点；账户、反馈、记录的接收时间均不能晚于它 |
| Forecast.asof_ms | 本轮使用的 Context 截止时点，必须与 Context.asof_ms 相同 |
| ForecastBatch.created_ms | 输出完成时间，允许晚于 Context，但必须位于 Context.asof_ms 与可信接收时间之间 |
| Forecast.expires_ms | 信号用于新决策的截止时间，必须晚于生成、接收和最终消费时刻；不因延迟重新延长 |
| Decision.created_ms | 预测完成后再次读取可信运行时钟，不能直接复用旧 Context 时点 |

外部时钟和预算通过调用参数注入，属于运行配置，不增加 Forecast 字段：

```python
from perpetual_alpha.forecasting import ForecastUnavailable, checked_forecast

try:
    batch = checked_forecast(
        producer, context,
        clock=trusted_epoch_ms_clock,  # 调用方提供可信 UTC epoch ms；测试用可推进时钟
        max_inference_ms=10000,
        max_input_age_ms=60000,
        raise_on_error=True,
    )
except ForecastUnavailable as error:
    journal.append("producer-unavailable", error.to_dict(), trusted_epoch_ms_clock())
    # 本轮不调用 Portfolio 或 Execution。
```

示例预算仅为离线集成配置。检查调用开始/返回时的输入年龄及推理耗时；过期、未来生成时间、信息时点不匹配、身份/证据/声明异常、required 数据缺失均失败关闭。返回时再检查预算，包含“Adapter 在超预算后抛出异常”的情况。`ForecastUnavailable` 的 reason_code / reason 是调度错误信息，不是给 Forecast 或 ExecutionReport 新增字段或原因码。

主要调度原因：INVALID_PRODUCER、INVALID_CONTEXT、INVALID_CLOCK、IDENTITY_MISMATCH、INVALID_TIME、EXPIRED、INFERENCE_TIMEOUT、STALE_INPUT、ADAPTER_TIMEOUT、ADAPTER_ERROR、INVALID_FORECAST、INVALID_EVIDENCE、UNDECLARED_ASSET、DATA_UNAVAILABLE。显式 error Forecast 也记录其原始输出，并阻断该 Producer 的执行路径。

requirements() 在计划阶段读取一次并保存快照，失败时不在异常处理器中再次调用。结构或身份不完整而无法安全生成 error Forecast 时直接保留结构化失败，不伪造成功批次。为保留旧测试和调用方式，不带时钟的双参数 checked_forecast(producer, context) 仍采用 Context 合成时钟，在能安全构建时返回带原因的 error items；它仅适用于旧合成 fixture，LT 和运行器使用显式时钟与严格异常模式。

**超时限制：**当前同步调度在 Adapter 返回时拒绝迟到结果，不会强杀卡住的 Python 调用，也没有增加线程/进程沙箱。LT 的 Harness 必须给每个阻塞调用配置有限超时并允许退出。Dummy 通过推进时钟模拟 8 秒推理，不调用真实 API，不等待实际 8 秒。真实网络超时、取消及服务商行为仍未验收。

## 3. 显式注册与唯一控制版本

`producers.ProducerRegistry` 只接受调用方在代码中显式注册的实例；没有配置驱动的任意 import、路径执行、插件发现或研究代码加载。

```python
from perpetual_alpha.dry_run import built_in_registry, load_config, run
from perpetual_alpha.examples.lt_adapter import DummyLTProducer, SimulationClock

config_path = "config/dry-run-lt.toml"
config = load_config(config_path)
clock = SimulationClock(config["asof_ms"])
registry = built_in_registry(config, clock=clock)
registry.register(DummyLTProducer(clock, lambda: clock.advance(8000)))
result = run(config_path, "data/dry-run/lt-example.sqlite3", registry=registry, clock=clock)
```

注册需要完整 ID、版本和两个方法。相同 ID 重复注册或注册另一版本均拒绝；要升级请在新组装的 Registry 中显式替换。resolve(id, version) 拒绝不存在的 ID、版本不符及注册后身份变化。运行器将唯一选定的注册 ID/版本交给 MockExecution 作为控制身份；影子输出没有执行权。

原配置和 CLI 命令保持可用：默认 Registry 注册 rule-demo / mock-agent，并对照非控制版本。可选运行配置如下；它们不是协议字段：

```toml
selected_producer = "dummy-lt"
selected_producer_version = "offline-0.1"
shadow_producers = ["rule-demo", "mock-agent"]
max_inference_ms = 10000
```

省略 selected_producer_version 时采用该白名单实例注册的唯一版本；正式集成建议显式填写。省略 shadow_producers 时运行全部其他已注册实例，填写空列表可关闭影子对照。未注册 dummy-lt 时，主 CLI 直接读取 LT 配置会明确拒绝；请使用显式组装的示例入口，不通过字符串自动加载代码。

## 4. 多 Producer 请求与隔离

ProducerPlan 汇总选定及启用影子版本的全部 DataRequest，完全一致的同 ID 请求去重；同 ID 任一字段不同（含 required、parameters）均明确报冲突，整轮拒绝，不静默合并。仅遍历第一个 Producer requirements 的旧实现已移除。

ContextBuilder 在共同 context_id / asof_ms 构建联合数据快照；context_for(id, shared) 产生只包含该 Producer 声明 datasets 的深拷贝视图。每份输入与数据状态写入 producer-input Journal，联合 input 也保留，便于对照和追溯；对象修改不会污染另一 Producer 的视图。

- **控制版本缺 required 数据/不支持的请求：**记录 producer-unavailable；不进入 Portfolio 或 Execution。
- **影子版本缺数据、不支持请求或 requirements 抛错：**保存独立状态/原因，不进入其预测或执行路径，不使控制版本的 RiskSnapshot 或 Portfolio 因影子需求失败。
- **合法影子版本：**保存 Forecast，用于对照；始终不生成该版本的 Decision。
- **optional 数据缺失：**如实保留 missing/stale/error；不自动阻断，但引用不可用数据作为有效 evidence 会被拒绝。

控制预测先消费，随后运行影子；影子的延迟不把已批准的控制决策拖到过期。所有版本仍使用决策前冻结的账户与反馈。MockRiskEstimator 和 Portfolio 只接收控制版本视图。执行前行情由独立 quote 请求读取，不再假设 Producer 一定需要 mark 等执行字段；独立 Risk Gate 和唯一 MockExecution 保留。

ContextBuilder 仍只支持原有比赛报价及字段；不新增新闻、K 线或外部价格数据。联合 Context ID 的详细视图归属记录在 Journal 中；完整 Forecast→Decision 血缘模型留待后续。

## 5. LT 可直接运行的例子

可复制的独立 Adapter 位于 [`lt_adapter.py`](../src/perpetual_alpha/examples/lt_adapter.py)，没有继承 Rule/Mock Producer。它声明 BTC bid/ask 和 ETH funding 两类不同请求，输出 BTC/ETH 的合成预测，保留真实信息时点并以注入时钟填写生成时间。替换内部 Dummy 推理即可包装已有 Harness，外层协议不需要改动。

```powershell
# 原有离线入口不变
.\.venv\Scripts\python.exe -m perpetual_alpha.cli dry-run
# 显式注册 DummyLT，并模拟完成时间比 Context 晚 8 秒
.\.venv\Scripts\python.exe -m perpetual_alpha.examples.lt_adapter --journal data/dry-run/lt-review.sqlite3
```

后一个命令使用 config/dry-run-lt.toml，仅 DummyLT 控制 BTC/ETH 模拟目标，Rule/Mock 产生影子输出。时间、行情、账户、方向和仓位全部为合成示例。result 增加的 failures / producer_contexts 是运行器诊断信息，原始 Forecast/Decision/Report 内部没有新增字段。使用重复 Journal 时应维持一致的时间场景：不得将完成后账户倒灌到更早 Context；切换或倒退模拟时间请使用新 Journal。

## 6. 回归与 CI

首批 14 项回归先在基线运行并失败，保存于提交 `50b37e9`，之后实施修复。最终新增 **22 项**测试，原 35 项未删除，总计 **57 项**。测试文件为 tests/test_integration_stabilization.py。

| 验收 | 覆盖 |
|---|---|
| T01/T02 | 8 秒迟到合法输出；输入/接收/使用时效，错误/未来时点、ID/版本、来源、证据、Adapter 异常和超预算拒绝 |
| T03/T05 | 无继承的独立 Producer、完整协议、注册/重复/版本冲突与身份变化 |
| T04 | 有效影子记录但无控制权；影子超时不拖延控制决策 |
| T06/T07 | 全部请求联合、完全相同去重、所有字段冲突、独立视图与修改隔离 |
| T08/T09 | 影子缺失/不支持/声明失败隔离；控制版本缺失/不支持安全阻断 |
| T10/T11 | neutral/abstain/error/未预测资产语义，BTC/ETH 多资产链路，1.0 决策与报告 |
| T12 | 原 35 项测试、原配置和可复制 Dummy 示例，网络和 Exchange 构造被测试封锁 |

完整 CI 继续使用服务器同镜像、Python 3.12.15 和 31 个锁定运行依赖，执行 Ruff、全部 unittest、原七份 Schema/样例验证，以及**原入口和 LT 示例两个离线 smoke run**。容器仍禁止网络、只读根目录，仅 /tmp 可写。具体运行结果以 PR Checks / [Actions](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/actions/workflows/ci.yml) 对应提交的记录为准。

本次未访问交易私钥，未发送订单，未改动真实签名路径、交易授权开关、服务器采集容器或部署配置。真实 LLM 调用、正式市场风险估计、历史数据覆盖/回测、订单恢复、退出政策和无人值守交易仍在本次范围之外；模拟集成成功不能解释为真实自动交易已通过验收。

## 7. 修改文件清单

| 文件 | 作用 |
|---|---|
| src/perpetual_alpha/contracts.py | AgentAdapter 与 ForecastProducer 对齐；未改动数据类字段 |
| src/perpetual_alpha/forecasting.py | 可信时钟、输入/推理预算、来源/身份/证据校验、结构化失败、内置 Producer 完成时间 |
| src/perpetual_alpha/producers.py | 新增显式注册及请求协调/作用域隔离 |
| src/perpetual_alpha/dry_run.py | 注册选择、控制/影子调度、独立执行行情请求、诊断日志 |
| src/perpetual_alpha/portfolio.py | 消费时验证 Forecast 信息截止时间与 Context 对齐 |
| src/perpetual_alpha/examples/__init__.py | 离线示例包；不自动注册 |
| src/perpetual_alpha/examples/lt_adapter.py | 可复制的独立 DummyLT Adapter 与显式组装入口 |
| config/dry-run-lt.toml | LT 控制版本/影子版本及合成参数示例 |
| tests/test_integration_stabilization.py | 22 项 P0 回归与集成测试；原测试不删减 |
| .github/workflows/ci.yml | 原有 CI 检查保持，追加 LT 示例 smoke run |
| README.md | LT 集成入口导航 |
| docs/README.md | 交接文档入口 |
| docs/technical-plan.zh-CN.md | 第 17 节稳定化接口引用 |
| docs/architecture-implementation.zh-CN.md | 更新注册、时间、失败语义及测试状态 |
| docs/continuous-integration.zh-CN.md | 更新两条模拟验证流程 |
| docs/forecast-integration-v0.2.1.zh-CN.md | 本次接口规范、交接、限制与验收说明 |
