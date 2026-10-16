# PR7：纯模拟 Portfolio、独立 Risk Gate 与 Supervisor

## 交付范围

基于 PR6 已合并后的 main `9efa27b`，新增固定资本预算的组合构建、独立全敞口审批、独立于 Agent 的风险监督，以及到既有 MockExecution 的本地合成闭环。保留 `portfolio.py`、`risk.py`、MockRiskEstimator、原 HTTP/Outbox 消费链及所有旧协议，不接入真实账户下单，不增加签名客户端或网络能力。

**这是 PR7 的纯模拟交付，不是完整真实账户风险准入。** 新入口只接受显式 `portfolio-simulation/0.1`、synthetic=true、合成美元/单位估值和模拟完整性断言。PR5 的 `account-observation/0.1` 不会被转换成 ready；即使 status=observed，或调用方将整个观察塞进合成信封，账户结构/完整性仍不满足要求。真实账户的 risk_ready=false 和未验证字段保持不变。

所有示例金额、杠杆、保证金、回撤、亏损及退出开关均为**新建本地 Mock Journal 的合成测试值**，没有获得真实 Testnet/主网使用许可。`approved` 是 RiskVerdict 原协议中的模拟审批，simulated=true；输出另明确 real_execution_authorized/execution_authorized=false。

## 文件与依赖

| 文件 | 职责 |
|---|---|
| risk_policy.py | 显式模拟政策、状态质量校验、完整账户/挂单/过渡敞口计算 |
| portfolio_policies.py | BudgetPortfolio：固定资本预算、步长与预算缩减，输出原 Decision 1.0 |
| risk_gate.py | SimulationRiskGate：重新验证 Forecast、身份/版本/水位、状态绑定和最终目标，不改写目标 |
| runtime/supervisor.py | RiskSupervisor：无 Forecast 也可检查、启动暂停、故障锁存、显式模拟恢复；不发减仓/撤单指令 |
| portfolio_simulation.py | 受限本地 Harness、MockGateBridge、已有 MockExecution/Journal，不提供 HTTP 路由 |
| tests/portfolio_support.py | 明确合成的两资产账户/预算；市场统计来自 PR6 合成样例 |
| tests/test_portfolio_risk.py | 46 项组合、审批、监督、完整性及独立进程测试 |
| scripts/validate_portfolio_risk.py | CI 离线运行正常/暂停两条路径，并校验原契约 |

无新运行依赖、无旧数据库迁移。新模块不以目录重命名替换既有 Demo。PR2/PR4 的 HTTP Consumer 仍使用原 Mock 路径；将新政策接入持久化控制消费需要后续单独集成验收，不能把本地 Harness 当作在线服务已经升级。

## 状态与政策边界

模拟 state 显式包含：context/asof、旧模拟账户、完整性断言、独立 synthetic USD/unit 与 quantity_step、RiskSnapshot、Core 控制身份/epoch/接受与消费水位、数据与执行健康、peak/session 起点权益。完整性断言包括 risk_ready、global_account_complete、other_exposure_known、inflight_orders_known，必须全部为 true；缺失/false 失败，不把空挂单当作已知无在途风险。

这些 true 只是假设**合成世界范围内完整**，不是对真实交易所账户的认证。信封和摘要不是安全认证机制；本地操作者能改合成文件，因此不能用于证明真实资产或数据来源。没有 Agent 端设置风险政策或状态断言的 HTTP 接口。

可预测/控制资产来自 control.symbols，资本预算仅作用于明确方向的 Forecast；风险覆盖还必须包含实际持仓、全部挂单与控制资产。额外 ETH/GOLD/FX 持仓不能因这次只预测 BTC 被遗漏；未知资产或缺乏风险/估值/上限时阻断。市场矩阵按自己的 symbols 顺序索引，不假设排序一致。

SimulationPolicy 所有资金字段、频率/寿命/年龄/样本/模型版本，以及 allow_reversals、allow_explicit_exits 都显式提供；缺失拒绝，mode 只允许 synthetic-only。调用方的配置在组件构造时复制，计算使用独立 Decimal precision=28；当前数值模型仍继承 PR6/旧 RiskSnapshot 的容差限制。

## Portfolio 行为

1. 校验合成状态、整个 ForecastBatch、producer/version、控制角色、asof/有效期、horizon 与风险 interval。任一 error item 阻断整批；不编造预测概率或预期收益。
2. directional ready 按 `预算美元 / synthetic USD-per-unit` 生成绝对有符号数量，向零取整到显式步长。既有 dust 不偷偷舍入；次步长预算不将已有仓位变成归零目标。
3. neutral、abstain、未覆盖、空 Batch 不生成退出目标。未提出的新目标保留原仓位；反向与显式零目标仍受模拟政策开关约束。Supervisor 没有自动退出权。
4. 先检查当前账户及挂单风险；若已有超限或状态未知，不以“生成一个减仓目标”绕过政策，交给监督暂停，等待单独处置政策。
5. 全预算目标超限时，以既有仓位为起点缩减变化量，最多 32 次有界搜索、每步重新计算风险并按步长取整；只保留实际验证可行的候选。没有可用交易步长时可返回空 targets。它是保守可行性搜索，不保证离散非单调情形下求到最大仓位，也不进行资金最优分配。
6. 保存 desired_targets、scale、计算指标、原 Forecast 与 digest、state/policy digest、control epoch；Decision TTL 不超过 Forecast 到期或显式 decision_ttl。

## 完整敞口与模拟指标

对每个持有/挂单/目标资产，将已有仓位 q、最终绝对目标 t（未覆盖则 t=q）、所有买单剩余 B 和卖单剩余 S 形成数量区间：

```text
lower = min(q,t) - S
upper = max(q,t) + B
```

这覆盖调仓过程中单腿先成交、已有订单随后成交等中间情景；把 reduce-only 和人工/未知归属订单全部保守计入，不假设 OCO 或互相抵销。Mock 并不模拟这些订单的实际触发/成交顺序，区间只是上界模型；真实在途订单未知时不能用它代替 Intent/Reconciliation。

按显式合成美元/单位转换后，计算：

- gross 上界：各区间最大绝对美元值之和；net 上界：所有 lower/upper 求和后的最大绝对值。
- sigma 上界：在最多十资产、2^10 个区间角点上计算 `sqrt(x' covariance x)` 的最大值，同时覆盖当前、最终及调仓混合敞口。这里利用 PSD 二次型的凸性；角点覆盖是对区间凸组合的推论，参考 [Boyd/Vandenberghe，凸函数与二次型](https://web.stanford.edu/~boyd/cvxbook/)。不求逆，不因市场矩阵奇异自动拒绝，也不把 sigma 当 VaR、亏损上限或未来最大损失。
- leverage 上界：gross/equity；合成剩余保证金：available_margin − max(0, gross−当前持仓gross)×显式 margin_fraction。该公式只是一种可测试的保守合成预留，不是 Unified 的真实保证金、清算或杠杆规则，未包含费用/滑点/资金费。
- drawdown 与 session loss：只基于显式模拟 peak/session 起点及当前 equity。模块不伪造长期权益账本，也没有自动持久化真实峰值、日界线或跨会话基准。

逐项检查 asset/gross/net/sigma/leverage/margin buffer/drawdown/session loss 上限；等于上限允许、超过拒绝（保证金低于缓冲拒绝）。所有生产阈值及校准仍待用户批准。真实 FX/指数合约估值、价格到美元 PnL 的敏感度和保证金换算尚未解决，不能用本例 synthetic USD/unit 代替。

## 独立审批与失效

Gate 不读取 Portfolio 的 sizing 指标来批准，而是从最终 Decision 重新计算。它检查原 Forecast 摘要/身份/整批状态/到期、控制身份/epoch/role、接受与消费水位、state/policy digest、当前精度、最新可用质量与完整风险覆盖；增加风险的目标也受明确资本预算约束。审批绑定原 decision_digest，拒绝/延后时不缩量、不覆盖目标。

状态/政策/报价/步长/挂单变化、控制切换、消费过同 asof、暂停或数据失效都会阻止使用旧 Proposal。直接解析畸形契约可能抛出校验异常；正常的未知状态在 Gate 返回 deferred，超预算返回 rejected。`approved` 只用于本地合成 Mock，不是对外执行凭证。

MockGateBridge 在现有 MockExecution.apply 内再次读取实际本地模拟账户和 provider，再执行 Gate；旧幂等/事务/unknown 语义保留。应用后立即再次监督。此处没有新增跨进程原子风控状态、水位或政策持久化服务；在线集成必须复用 Runtime/Outbox fencing、稳定 Decision ID，并重新完成检查和崩溃验收。

## Supervisor 与恢复

Supervisor 不接收 Forecast，也不要求 Agent 心跳来检查已有风险。它检查当前状态/账户/挂单风险与 Core 数据、执行健康；启动即 paused。健康恶化、数据陈旧或风险超限会锁存暂停，数据恢复不会自动解除。`resume`/CLI `--resume-supervisor` 是显式本地模拟恢复，且只有检查通过才能执行。

没有自动平仓、减仓、撤单、交易 Kill Switch 权限；automatic_actions 始终为空。当前是可调用的监督组件，尚未部署独立定时循环；暂停锁存在内存，重启重新进入 paused，不自动恢复旧许可。真实运行的峰值账本、持久暂停原因、人工恢复授权和执行联动需后续集成/部署验收。

## 运行、测试与回滚

使用明确的合成样例、新 Journal 和显式模拟恢复：

```powershell
.\.venv\Scripts\python.exe -m perpetual_alpha.portfolio_simulation --fixture docs/examples/portfolio-risk.synthetic.json --journal .cache/pr7-demo-new.sqlite3 --resume-supervisor
```

不加恢复参数时保留暂停并由 Mock 拒绝，退出非零。Journal 必须为新路径，绝不连接现有运行/账户/历史库或复用既有执行记录；文件已存在时拒绝，不通过清库解决 unknown。启动前先验证、规划，建立后写入模拟输入/Proposal/监督/Decision/Report。中断后的 Journal 保留供检查；这不是 PR8 的可恢复 Paper。

新增 **46 项测试**，原 309 项保留，合计 **355 项**：固定资本/缩量、保留持仓、全部挂单、十资产/FX、矩阵顺序、过渡角点、步长/dust、neutral/abstain/error、TTL、退出/反向开关、审批篡改/失效、控制版本/水位、PR5 合同拒绝、完整性 unknown、暂停/恢复/重启、损失/保证金/杠杆、原 Mock 幂等与应用前重检、独立进程闭环。

```text
python -m unittest discover -s tests -v
python -m ruff check . --no-cache
python scripts/validate_portfolio_risk.py
python scripts/validate_schemas.py
python scripts/validate_account.py
python scripts/validate_market_risk.py
python scripts/validate_history.py
```

CI 新增无网络、只读 Linux 容器中的模拟闭环，使用原 Forecast/Risk JSON Schema 和 Decision/Report Python 契约验证，保留旧七业务 Schema、13 DTO、原 Mock/历史 Shadow 双容器检查。没有真实账户在线访问或为测试开仓。

停止新 Harness/调用方即可回滚，无旧入口或数据库迁移。保留 Journal/输入证据，不自动删除。

## 仍须用户决定与后续工作

| 项目 | 当前状态 |
|---|---|
| 工程规则：摘要、版本/epoch/水位、整批 error、缺失失败、全账户覆盖、暂停锁存、目标不可静默修改 | 本轮可直接实现；真实服务接入仍需原持久化边界验收 |
| 生产资本/单资产预算、gross/net/sigma、杠杆/保证金、回撤/会话亏损及日界线 | 未批准；示例数字仅合成测试 |
| 波动模型频率/窗口/最小样本、休市/异常值/高维稳定性与 horizon 政策 | 未批准；不自动加 EWMA/shrinkage 或期限缩放 |
| neutral/abstain/过期/失联后的退出、反向、dust、减仓/撤单、暂停恢复权限 | 真实政策未批准；本轮无自动退出动作 |
| 真实合约估值、全账户保证金、其他 DEX/spot/在途风险和非空账户只读验收 | 待可靠证据；PR5 unknown 不解除，不为验收造仓位 |
| 在线新 Portfolio/Gate/Supervisor 消费、原子状态/水位、持久峰值与恢复授权 | 后续独立集成验收；当前 HTTP/Outbox 链保持原模式 |
| 可恢复 Paper、Intent/Reconciliation、费用/成交模型与 VPS 长期运维 | PR8–PR9，真实 Adapter 启用另行授权 |

因此，本轮证明的是组合预算、独立模拟审批和风险监督的算法及安全边界可运行；完整真实账户风控与无人值守交易仍未完成。
