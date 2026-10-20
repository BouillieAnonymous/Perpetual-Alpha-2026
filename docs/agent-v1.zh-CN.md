# Strategy Agent V1.1：Top-p 概率门控

基于 `nopperab0/Perpetual-Alpha-2026` 的 main，基线提交：
`f2cecbadadfec8ba0048609f438180d07027871b`。本版完成可运行的策略研究／离线 Paper 闭环；Jev 官方 HTTP 调用与响应解析已实现；使用者只需在本地 `.env.jev` 填写 TypeSafe API Key。尚未用真实凭据验收服务。

## 1. 本次落实的设计

```text
PAS 派生数据库 / 分钟 CSV / 冻结快照
  → 严格 as-of 因子 → 八个具体策略及成本预筛
  → Jev Choice 完整分布 / 显式 Python 对照
  → Top-p 累计门控，默认 0.85，保留原始概率
  → 统一风险尺度、按资产净额合并
  → 受限小概率乐观探索 → 独立敞口检查
  → 下一条有效报价模拟成交 → 费用/资金费/退出/审计
```

这里的“完整”指该研究闭环。没有添加在线自我改进、RL 训练或多 Agent 编排。程序不加载交易密钥，不签名，不发送交易所订单；Jev 接通后仍然是研究／Paper 模式。

现有 `contracts.Forecast`、`interfaces.TargetPosition` 和通用 Paper 风控仅接受 `par:`。因此未把 `pas:` 改名为 `par:`，也未改宽旧风险协议。新增 PAS 研究入口复用 `pas_research.checked_derived/panel`；原 Collector、通用 Runtime、独立 Funding-LAST runner 及部署配置保留原职责。真正的多策略 PAS 账户风控、签名执行和订单对账仍需原仓库后续执行升级，不能声称填入 Jev API 就能实盘。

## 2. 概率怎样变成仓位

默认 `gate="top_p"`，从大到小保留最短前缀，使累计概率 **达到或超过** `top_p=0.85`；包含越过阈值的最后一项，概率相同时按固定候选顺序处理。另提供 `top_k` 和 `all` 作为消融。

保留 **原始** Choice 概率，不把剩余质量归一化到交易策略。例如讨论中的分布：

|选项|原概率|累计|新增预算权重|
|---|---:|---:|---:|
|S02|0.32|0.32|0.32|
|S03|0.21|0.53|0.21|
|N01|0.16|0.69|0.16|
|S04|0.11|0.80|0.11|
|NO_TRADE|0.10|0.90|0|
|其余四项|合计 0.10|1.00|0|

选择前五项，实际新增策略预算最多使用 80%。请求只包含当轮有合格动作的策略和 `NO_TRADE`；响应必须覆盖且仅覆盖这些选项。加权后不能通过数量／成本／风险检查的动作会被拒绝，原概率质量不再分配。`NO_TRADE` 只阻止新增目标，已有持仓继续依照到期／止损／止盈规则管理。正常策略之间净额恰好抵消时，显式零目标可以产生退出，这与 NO_TRADE 不同。

每个策略先在其合格资产之间平分一份**组合级**研究预算，再按该资产历史波动缩小：

```text
leg_notional = direction × risk_budget_usd / eligible_leg_count
             × min(1, target_vol_bps / holding_period_vol_bps)
             × original_choice_probability
```

随后按资产求和、向零按数量步长取整、检查再平衡差额和现有持仓总敞口。预算不会在每个币种重新完整使用。未被本轮目标覆盖的旧持仓保留到原退出时间；它们仍占用总额度。

`risk_budget_usd=1000`、单资产 500、总敞口 1000 等都是合成研究参数，不来自账户余额或现有 Funding runner 的 25 USDP 批准额度。

**准确率与 Top-p 没有直接换算关系。** [TypeSafe 官方评测](https://evals.typesafe.ai/) 衡量四种业务工作流与模型共识标签的一致性；[官方首页](https://typesafe.ai/) 的速度／成本宣传也有特定任务边界。此前讨论的约 68% 不能直接推导成 `top_p=0.68`，通用分类表现也不证明交易获利概率。0.85 是待验证的工程初值，不是 85% 盈利保证。Jev Choice 原本是互斥选项分布，用作组合权重属于本项目的研究假设。

## 3. 八个策略与调整理由

|ID|已实现动作|主要前提／收紧|
|---|---|---|
|S02|15 分钟与 60 分钟方向一致的趋势交易|60 分钟涨跌幅超过持有期波动尺度|
|S03|价格偏离过去 60 分钟均值后反向交易|偏离至少 1.5 个标准差，并要求长期趋势较弱|
|S04|本地中间价相对 Oracle 偏离后反向交易|单腿方向策略，不称无风险套利；只使用新鲜收到的 context|
|S14|收取资金费的方向仓位|使用已结算且已收到的费率；收益估计扣价格风险，不假装有现货对冲|
|N01|低波动压缩后的 20 分钟区间突破|压缩窗口与基准窗口分离，突破点不放入自身参考区间|
|N02|15 分钟极端涨跌后的反向动作|必须观察到最近 3 分钟反转确认，不能只因跌得多就买|
|N03|BTC／ETH／HYPE 内相对强弱双腿|必须三者都有完整历史，两腿成本均合格；不直接横排外汇与加密收益|
|N04|观察到 Oracle 明显变化后的延续动作|用已知 context 更新与短期同向价格确认，不伪造新闻／开盘日历|

独立情绪因子可以放入 Jev 请求，格式见第 6 节。未接新闻供应商或生成式 LLM，缺少情绪时明确为空。S07 盘口等属于输入、S13 属于风险／成本控制；未把这些因子或方法凑成 17 个可执行策略。

当前美元仓位模型不对 `pas:USDJPY` 生成目标：该品种的报价币种换算及合约估值尚未提供，其数据保留但被明确拒绝。其他资产也须提供正确市场数量步长；示例数字不能当作实时元数据。所有策略的优势估计都是冻结规则的启发式量，尚无真实市场盈利验证。

## 4. 小概率探索

默认 ε=0.02，是**机会抽样概率**，不是资金比例。每个“策略＋资产＋方向＋该策略持有期时间桶”只抽一次；确定性哈希抽样使用配置 seed，已抽机会、冷却期和每日尝试数持久化。默认最多两次选中探索／UTC 日、冷却一小时，风险拒绝也消耗选中次数。

只使用决策时点以前的、互不重叠的完整持有期收益；至少 20 个样本才允许探索。以过去收益的上侧经验情景扣成本，再除以事前持有期波动来排名。它是**历史乐观情景**，没有冒充已经校准的未来 q90。短历史没有足够证据时探索关闭，正常路由照常运行。

探索发生时替代本轮正常新增目标，以独立 `exploration_usd=50` 上限和波动缩放定量；已有正常持仓仍保留并计入组合额度。一次最多持有一个探索资产，不把普通仓位重标成探索，也不把探索仓位在下一轮放大为常规额度。N03 双腿不参与单动作探索。NO_TRADE-only、路由异常、熔断时均不能触发探索。

## 5. 快速运行

在仓库根目录、已准备 Python 环境后运行；本模块本身只用标准库。以下命令在 PowerShell 和常见 shell 中均可使用，设置源码路径即可：

```powershell
$env:PYTHONPATH = "src"
python -m perpetual_alpha.agent demo --config config/agent-v1.toml --journal data/agent/demo.sqlite3 --out data/agent/demo.json
python scripts/validate_agent.py
python -m unittest discover -s tests -p "test_agent*.py" -v
```

`demo` 使用确定性合成行情和显式 `RuleRouter`，从不假冒 Jev。重复执行相同输入和 journal 会返回已提交记录，不重复成交。更换配置、模型版本或回放数据文件须使用新的 journal。正式复现环境仍以仓库 Python 3.12.15 和依赖锁文件为准。

### 实际 PAS 派生数据库

`metadata.json` 是 `{ "pas:BTC": 0.00001, ... }` 形式的数量步长映射，必须来自所选环境的实际 metadata，覆盖输入中所有有有效报价的资产。数据库只读，禁止把输出／journal 路径指回输入或其 SQLite sidecar。

```powershell
python -m perpetual_alpha.agent replay --config config/agent-v1.toml --derived-db data/pas/research.sqlite3 --start-ms 1792368000000 --end-ms 1792454400000 --metadata data/pas/metadata.json --journal data/agent/pas-rules.sqlite3 --out data/agent/pas-rules.json
```

也可把 `--derived-db/--start-ms/--end-ms` 换成 `--panel-csv data/pas/2026-10-19-panel.csv`，使用 `pas_research` 生成的分钟 panel，不是 observations/funding 原始 CSV。需至少 241 个连续、新鲜且 event time 前进的分钟点，任何断档均重新预热；没有未来填补或插值。

## 6. 填入密钥即可使用的 Jev 官方接口

`agent/jev_api.py` 已实现 TypeSafe 直连 HTTP：固定 `https://api.typesafe.ai/v1/systemone`，按官方 Choice 的 `state / model / questions` 请求和 `answers.strategy.probabilities` 响应做映射。来源：[官方 Choice](https://docs.typesafe.ai/primitives/choice)、[官方模型版本](https://docs.typesafe.ai/models)。

打开仓库根目录 `.env.jev`，只在空白处填入你自己的 TypeSafe Key：

```dotenv
TYPESAFE_API_KEY=
JEV_MODEL=jev-1.14.0
JEV_TIMEOUT_SECONDS=1.8
```

`.env.jev` 已由现有 `.gitignore` 的 `.env.*` 规则排除；共享模板为 `config/jev.env.example`。环境变量优先于文件。这里使用 TypeSafe 直连密钥，不能混填其他代理平台的密钥；不要把密钥写进 Python 源码。

在项目根目录运行（下列实际 Jev 命令会发送模型请求，由你填写 Key 后自行运行）：

```powershell
$env:PYTHONPATH = "src"
python -m perpetual_alpha.agent fixture --out data/agent/snapshot.synthetic.json
python -m perpetual_alpha.agent decide --snapshot data/agent/snapshot.synthetic.json --router jev --journal data/agent/jev-v11.sqlite3 --out data/agent/jev-decision.json
```

第一条只生成合成快照，第二条调用模型并给出 Paper 目标，不会签名交易所订单。`--model-version` 可以省略并使用本地 `JEV_MODEL`；显式传入时须与该配置一致。别名如 `jev-latest` 被拒绝，以免模型版本静默漂移。

API 返回的内部信封是：

```json
{
  "schema_version": "agent-routing/1",
  "request_id": "原 request_id",
  "model_version": "jev-1.14.0",
  "probabilities": {"S02": 0.7, "S14": 0.2, "NO_TRADE": 0.1}
}
```

上例仅适用于当轮 options 恰好是 S02、S14 和 NO_TRADE。概率键必须与当轮 `request.options` 完全一致，不能补回无效策略的零概率。有限、非负、各不超过 1，总和容差 1e-6；拒绝缺项、多项、重复 JSON key、NaN、实际模型不匹配。原 V1 的九项捕获响应不能直接复用到候选已改变的新请求。

没有自动重试或重定向。HTTP 异常／429／非法响应阻止新增目标；错误日志不回显服务端正文或认证头。连接有 socket 超时，读取按剩余 deadline 缩短 timeout，响应体上限 64KiB；系统 DNS 解析仍依赖操作系统，Harness 也会拒绝超出 inference budget 的返回。当前没有实测 API 账单或模型路由准确率。

捕获分布回放仍支持 `--router captured --responses response.json --model-version jev-1.14.0`，格式为一个上述信封或以 request_id 为键的信封字典。`request` 命令可导出当前动态选项请求。所有旧 journal 继续封存；V1.1 使用新的 journal。

情绪仍是可选结构化输入：`{"available_ms": ..., "expires_ms": ..., "score": -0.2, "source_id": "..."}`。此版本不声称已有新闻抓取、LLM 新闻解析或深度盘口因子。非法情绪阻止新增路由，但已有仓位的退出管理继续执行。

## 7. 执行、恢复与记录

N03 的审批和模拟成交按双腿篮子处理：任一腿因数量步长、最小订单、缺报价、超限或执行价差失败，两腿都不成交；已有配对仓位的到期／止损／止盈联动退出。正常策略净额若把 N03 变成同向或缺腿组合，N03 整份贡献被抑制且不重新分配其概率。组内已有仓位恰好符合目标的腿可作为已满足目标，不伪造成交。这里的原子性仅属于离线账本，不是交易所跨市场原子订单承诺。

Paper 目标至少等到下一条 event time 前进的有效报价才成交，使用买侧 ask／卖侧 bid，并另扣滑点与 taker 手续费。下单前及模拟成交前分别检查价格／数量步长、单资产／总名义额度、最小订单和熔断。减少风险的退出不受普通再平衡带限制，但仍不能伪造成交不足最小金额的订单；无法清理的 dust 保留并记录拒绝。

`[agent.strategy_horizon_minutes]` 可给各策略配置持有分钟数；没有配置则继承 `horizon_minutes=60`。同一资产混合多个策略时采用贡献中最短周期，历史风险尺度、Funding 成本、探索样本和开仓到期时间都使用对应周期。没有数据时不宣称这些周期经过校准。

止损、止盈和最长持有时间由 Python 管理，同方向调仓不会无限重置到期时间。净值回撤或已知仓位超限会锁定熔断并提出平仓目标；重启不能清除该状态。未知报价会阻止新增风险，无法执行的退出等待后续有效报价。模拟不包含盘口深度、部分成交、排队、真实保证金和清算，因此不能替代既有真实执行验收。

资金费按首次收到的结算事件去重，用结算时实际模拟持仓而非收到数据时的仓位计费；没有已知结算参考价或欠缺应有结算事件时，报告 `funding_coverage_complete=false`，不捏造支付。Oracle 与 mid 权益分别输出，缺少新鲜估值时显式标记。末尾未成交目标、未平仓头寸都会披露，不假设最后一秒能成交平仓。

独立 SQLite journal 在一次事务内保存状态、目标、模拟成交、概率、候选／拒绝原因和探索记录。历史 Observation 按 symbol/asof 冻结一次；每轮保存窗口摘要和内容哈希，既可审计也避免重复存储整段历史。相同时间同输入幂等，冲突输入、历史修订、回退时间、配置／模型变更会拒绝，使用新的实验 journal 重跑。

## 8. 与原 ForecastProducer 的连接

`agent/forecast_adapter.py::AgentForecastProducer` 实现现有 `requirements()/forecast(context)`，可注册进 `ProducerRegistry`，通过 `checked_forecast` 验证。它仅接受 PAR，按已收到报价重采样成分钟观测，用 `last_analysis` 保留完整概率与加权目标，Forecast 提供方向和审计引用。没有历史 Funding 输入就不生成 S14 信号。

该入口是 **shadow forecast 对照**。原 `FixedQuantityPortfolio/BudgetPortfolio` 会用固定数量／资本重新定量，丢失 Jev 的组合权重，不能把适配器接进去就称为本策略的加权执行。完整权重与持仓生命周期由新的 Harness/Paper 路径运行；未来接入原执行链时，须保留该目标协议并独立再验风险。

## 9. 验证和消融

新增测试覆盖：对话中的 0.85 例子、精确等于阈值、Top-p=1、固定排序、非法分布、双向抵消、组合总预算、因果时点、数据断档与重复事件、各策略触发、成本门槛、探索最少样本／次数／恢复、到期退出、NO_TRADE 持仓管理、迟到路由、情绪失败、手续费、延迟 Funding、实际 PAS pipeline 输入及接口适配。

`scripts/validate_agent.py` 做 360 分钟合成闭环与重复回放幂等验收。合成测试不证明策略盈利，真实 Jev 尚未调用，因此没有宣称 Jev 超过 Python Router。

对同一未参与调参的时间段，保存一组固定的 Choice 分布，然后用独立 journal 比较：Python baseline、Top-1/2/3、Top-p 0.80/0.85/0.90/0.95、全部权重；再分别开关情绪与 ε。成本、候选、风险预算保持一致。报告净收益、日权益波动、回撤、换手、费用与探索成交数；不足完整日样本时日波动返回 null，不能用分钟波动冒充官方指标。

官方规则核对入口：[比赛评分](https://perpetual-alpha26.github.io/about)、[Participant Guide](https://perpetual-alpha26.github.io/docs/)。本版采用保守 taker 双边成本及最小名义订单过滤，实际费用、数量步长及市场状态应按交易所读取。本地验证结果及已知平台差异见随交付的说明和 PR 描述。
