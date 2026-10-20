# PR6：历史市场风险统计与冻结输入回放

## 范围

基于 PR5 已合并后的 main `f0b9de4`，落实开发基线 PR6：历史 simple returns、N 资产协方差、单位和顺序、缺失/样本不足、奇异矩阵及可重复计算。

新增 `market_risk.py`、`risk_replay.py`，保留 `risk.py`、MockRiskEstimator、DemoRiskGate、Portfolio、账户 Adapter、Agent Gateway 和所有旧业务/Transport Schema。未组装线上 RiskSnapshot 服务，不自动注入原 Mock 链路，不开放账户外发，不引入额外运行依赖。

输出复用 **RiskSnapshot risk-snapshot/0.1**；`ready` 只表示显式研究配置下的市场统计可计算，不表示账户 `risk_ready=true`，也不表示资金风险准入。PR5 的未知美元估值、保证金、其他 DEX 和在途订单继续保留，真实交易未授权。

## 模型与单位

首版方法为 `historical-sample-covariance`，模型版本 `sample-covariance/0.1`。对相同已收盘窗口、同一频率和明确顺序的资产，使用收盘价：

```text
r[t,i] = close[t,i] / close[t-1,i] - 1
mean[i] = sum(r[t,i]) / n
cov[i,j] = sum((r[t,i]-mean[i]) * (r[t,j]-mean[j])) / (n-1)
vol[i] = sqrt(cov[i,i])
corr[i,j] = cov[i,j] / sqrt(cov[i,i]*cov[j,j])
```

样本协方差的 n−1 分母及相关系数公式依据 [NIST covariance](https://itl.nist.gov/div898/handbook/pmc/section5/pmc541.htm)、[NIST correlation](https://www.itl.nist.gov/div898/software/dataplot/refman2/auxillar/correlat.htm)。这是可审查的基准模型，不声称等权样本模型最适合真实交易，也不声称样本独立或风险恒定。

- `symbols` 完全保留调用方顺序，矩阵行列、volatility、correlation 使用同一顺序；支持 1–10 个已配置比赛资产，未知资产拒绝，不自动缩小集合。
- units 保持 `simple-return-per-interval`、annualization=`none`；协方差为该收益率单位的平方。没有年化、杠杆、美元敞口或 Forecast horizon 换算。
- n 个收益需要 n+1 根完整 candle；`estimation_window_ms=n*interval` 是首末收盘点距离，读取区间本身覆盖 n+1 根 candle，二者明确区分。
- 每个资产使用相同完整窗口，不做 pairwise deletion、不前向填充、插值、收益截断或删除异常收益。仅依赖历史层有效且已收盘的数值；有效但剧烈的价格变化仍进入统计。经济合理性/异常值政策需另行研究。
- USDJPY 的 JPY/USD、USDEUR 的 USD/EUR 等报价沿用历史 catalog；这里计算报价序列收益，不把它宣称为某种合约的美元 PnL。合约乘数、FX 转换与持仓方向的资金风险映射仍属后续。
- 零方差的波动率是零，但相关系数为 null，包括对角项；不能解读为该资产没有未来风险。
- 完美相关、样本数少于资产维度等可能产生奇异矩阵。允许有效 PSD 的市场统计，不求逆、不加 ridge、不自动 shrinkage；`matrix_invertibility=not_assumed`。下游优化不能假设可逆或估计精度充分。

计算使用独立固定 Decimal precision=28 的 Context，避免调用方改变精度/舍入造成重放漂移；无 float、NumPy 或新增依赖。复用现有 RiskSnapshot 的对称性、对角/vol 一致性、Cauchy 及 LDL PSD 校验。数值校验失败返回 `error/NUMERICAL_VALIDATION_FAILED`，不输出矩阵、不修补负特征值。原协议的绝对容差不等于生产数值稳定性保证；没有条件数诊断或经过校准的误差界，极端尺度允许安全拒绝。

## 显式研究配置

`RiskConfig` 要求五项全部显式提供，无生产默认窗口：

| 参数 | 含义与工程上限 |
|---|---|
| symbols | 1–10 个有序、唯一的比赛资产 |
| interval | 历史层既有固定 interval；不实现周/月历法或交易时段聚合 |
| return_count | 完整窗口目标收益样本数，2–1,000；不是最少样本的生产推荐 |
| min_samples | 本次研究要求的最低样本数，2–1,000；高于目标窗口则返回 insufficient |
| max_age_ms | asof 到最新闭合窗口末点的最大年龄，非负整数；不代表生产 SLA |

首版严格要求整个窗口完整，因此不会因设置较低 min_samples 而容忍缺口。样本不足时不降为单资产、对角矩阵或缩短窗口；降级为无有效估计，留待未来明确政策。

代码/样例中的 1m、3 个收益、最少 3 个样本、60 秒年龄仅用于可手算的**合成测试**，不是实际交易参数。测试还覆盖 5m/1,000 个收益。生产频率、窗口、最小样本量、估计方法、异常值/休市处理和预测期限换算必须另行确认。

## 冻结、质量与回放

`freeze` 只使用现有 HistoricalStore：

1. asof 由调用方显式传入，取 `end=floor(asof/interval)*interval`，不纳入形成中的 bar。
2. 只 pin 一次，记录 asof、snapshot_seq、anchor；每个资产读取时复用同一 pin。后续即使相同毫秒发生入库，也不能越过 seq 混入该次输入。
3. 历史层继续要求 close/received/available≤asof；统计侧再次检查版本、窗口/资产/报价单位、时间网格、接收/可用时间、revision_seq 和正有限收盘价。
4. 不只检查总体 status，还检查 gaps、invalid、truncated、closed_only、not_yet_closed_records、expected/available/实际记录数。过期和缺口同时存在时保留两个原因。
5. 原样封存实际查询返回（含 provenance、修订与质量），不在回放时重新查询数据库或外网。未提供或失败的资产不作为零收益。

市场结果状态：完整且校验通过为 ready；缺失/不完整/不足为 missing；过期为 stale；数值失败为 error。非 ready 一律无 vol/covariance，sample_count=0 表示**没有共同获准计算的样本**，实际记录数量仍在输入证据中。非法身份/信封/摘要抛出校验错误，而非包装成有效市场数据。

`seal` 保存 inputs、result、input_digest、result_digest 和 artifact_digest；inputs 包含完整配置、model_version、synthetic 声明、pin 和所有返回。`replay` 先校验摘要，再重新估计并比较完整结果。输入/回放包各限 32 MiB，版本未知拒绝；CLI 拒绝覆盖已存在文件。写入中断的 JSON 无法通过回放，不能当成功证据；不承诺该文件写入具备 SQLite 级耐久性。

摘要用于完整性与可复现检查，不是签名或来源真实性证明。具有写权限的人可重写整个输入和摘要；`source-kind` 是操作者显式声明，不能据此认证真实行情。合成样例使用 synthetic=true；真实采集模式 false 也不产生交易许可。

这个最小 Replay 只复算给定 asof 的风险统计，**不是收益回测、walk-forward、策略评价或成交模拟**；多时点研究可分别保存多个包，不混入在线控制任务队列。

## 运行与验证

可直接复算仓库中的纯合成包，无数据库/网络：

```powershell
.\.venv\Scripts\python.exe -m perpetual_alpha.risk_replay replay --input docs/examples/market-risk-replay.synthetic.json
```

对已有本地历史库冻结（以下数字也仅为研究示例，asof 必须是实际研究时点）：

```powershell
.\.venv\Scripts\python.exe -m perpetual_alpha.risk_replay freeze --database data/history/history.sqlite3 --asof-ms <ASOF_MS> --symbols par:BTC par:ETH --interval 1m --return-count 120 --min-samples 120 --max-age-ms 60000 --source-kind observed --output .cache/risk-replay.json
```

freeze 使用 mode=ro 的 HistoricalStore，不自动建库、采集或补抓。输出摘要包含 status/sample_count/snapshot_id/execution_authorized=false；非 ready 退出码 1，正常 ready 为 0，损坏信封/文件等失败为非零。

本地还使用 PR3 留存的公开 BTC/ETH 历史库、按其既有接收/入库时点执行了 observed 模式冻结，结果 stale、0 个共同有效样本，没有用合成数据补成 ready；没有为本 PR 发起新的在线抓取。该本地证据不进入仓库。ready 路径、十资产和非默认间隔由明确合成数据测试，不据此宣称已有长期/十资产真实数据覆盖。

新增 **33 项测试**，原 276 项保留，总计 **309 项**。包括手算 n−1 协方差与正/负相关、零方差/奇异、顺序、十资产 FX、最大窗口、缺口/坏修订、时间边界/迟到、资产读取间写入竞争、极端数值拒绝、来源标记、摘要/模型篡改、删除数据库后的独立进程回放和输出不可覆盖。

```text
python -m unittest discover -s tests -v
python -m ruff check . --no-cache
python scripts/validate_market_risk.py
python scripts/validate_schemas.py
python scripts/validate_account.py
python scripts/validate_history.py
python scripts/validate_runtime.py
```

CI 新增无网络、只读 Linux 容器中的冻结样例复算，并使用**原 RiskSnapshot Schema**校验。原七业务 Schema、13 Transport DTO、Mock 与历史 Shadow 双容器验收保留。

## PR7 之前仍未解决

- 可交易、预测和实际敞口集合不同；PR6 只计算显式 universe，不从 Forecast 猜全账户风险，也不忽略无法估值的额外持仓。PR7 必须验证覆盖范围。
- 市场统计 ready 与账户风险准入分离。PR7 必须检查 PR5 risk_ready 及各项完整性，不能只凭 observed 或本模块 ready 放行。
- 资本预算、敞口/杠杆/保证金/损失阈值、neutral/abstain/到期退出、减仓与暂停恢复权限仍需用户决定。
- 真实非空账户只读验收可以在自然存在非空状态时补充，不为验证制造交易；执行前刷新、在途 Intent/Reconciliation 仍归 PR7–PR8。
- 协方差方法校准、交易时段/休市导致的缺口、最少长期历史、条件数及高维稳定性、报价到合约美元风险映射尚未完成。

回滚只需停止新风险 CLI/调用方；无数据库迁移、无旧入口替换，保留封存研究包供审计。
