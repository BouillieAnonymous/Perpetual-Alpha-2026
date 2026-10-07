# Perpetual Alpha 2026｜可交易资产与交易信息

核对日期：2026 年 10 月 7 日。本文依据比赛官方公开指南整理，供资产筛选与开发规划使用，不代表已连接接口、配置账户或选定策略。市场规格可能更新，实施时以实时接口为准。

## 1. 当前可交易资产

这些是参考下列标的的永续合约，不是直接购买实物、股票或 ETF 份额。全部市场全天交易，以 `USDPR` 结算。

| 比赛代码 | 参考标的 | 报价单位 | 当前数量步长 | 当前杠杆上限 |
|---|---|---|---|---|
| `par:BTC` | 比特币 | USD／BTC | 0.0001 | 40 倍 |
| `par:ETH` | 以太坊 | USD／ETH | 0.0001 | 25 倍 |
| `par:HYPE` | HYPE 代币 | USD／HYPE | 0.01 | 10 倍 |
| `par:SP500` | 标普 500 指数 | 指数点 | 0.001 | 50 倍 |
| `par:EWY` | iShares MSCI 韩国 ETF | USD／份 | 0.001 | 20 倍 |
| `par:EWJ` | iShares MSCI 日本 ETF | USD／份 | 0.001 | 20 倍 |
| `par:BRENT` | 布伦特原油 | USD／桶 | 0.01 | 20 倍 |
| `par:GOLD` | 黄金 | USD／金衡盎司 | 0.0001 | 25 倍 |
| `par:USDEUR` | 欧元兑美元 EUR/USD | USD／EUR | 0.1 | 50 倍 |
| `par:USDJPY` | 美元兑日元 USD/JPY | JPY／USD | 0.01 | 50 倍 |

`USDEUR` 实际跟踪 **EUR/USD**，不要按名称猜方向。数量步长不等于最小订单金额；杠杆上限不等于推荐或必须使用的倍数。开发时读取 `meta` 的 `szDecimals` 与 `maxLeverage`。[官方市场清单](https://perpetual-alpha26.github.io/docs/#markets)

## 2. 交易信息与用途

| 信息 | 官方查询／订阅 | 用途 |
|---|---|---|
| 资产规格 | `meta` | 资产清单、数量精度、杠杆上限 |
| K 线 | `candleSnapshot`／`candle` | 趋势、收益、波动；收盘信号只用完整 K 线 |
| 最优买卖价、盘口 | `l2Book`／`bbo`、`l2Book` | 价差、深度和执行成本 |
| 近期成交 | `recentTrades`／`trades` | 市场活动；快照不等于完整成交历史 |
| 参考价、标记价、资金费、持仓量、24 小时成交量 | `metaAndAssetCtxs`／`activeAssetCtx` | 市场状态、价格偏离和持仓成本 |
| 资金费历史、实际支付 | `fundingHistory`、`userFunding` | 回测成本、实际收益核对 |
| 余额、真实仓位、强平价 | `spotClearinghouseState`、`clearinghouseState` | 全账户资金和剩余风险 |
| 杠杆、保证金模式、最大下单数量 | `activeAssetData` | 可用交易额度 |
| 挂单、订单状态、实际成交 | `openOrders`、`orderStatus`、`userFills` | 拒单、部分成交、剩余挂单核对 |
| 费用、账户费率 | 成交的 `fee`／`feeToken`、`userFees` | 成本计算，以实际成交费用核对 |
| 请求预算、市场持仓上限 | `userRateLimit`、`perpDexLimits` | 接口及市场限制 |

参考价 `oraclePx`、标记价 `markPx` 和实际成交价分别记录，不能互相替代。官方使用标记价计算未实现盈亏、保证金与强平；执行判断应观察实际报价。[价格说明](https://perpetual-alpha26.github.io/docs/#oracle-and-mark-prices)、[查询清单](https://perpetual-alpha26.github.io/docs/#info-queries)

## 3. 接入与交易约束

- Hyperliquid 测试网，市场 `par`。查询：`https://api.hyperliquid-testnet.xyz/info`；签名交易：`https://api.hyperliquid-testnet.xyz/exchange`；订阅：`wss://api.hyperliquid-testnet.xyz/ws`。适用查询显式选择 `dex=par`，代码带 `par:` 前缀。
- 每笔订单至少 10 美元；数量符合步长；价格最多 5 位有效数字，并符合 `6 − szDecimals` 位小数限制；不得偏离参考价超过 80%。这些是接口边界，不是项目的风险容忍度。
- 可用普通限价 `Gtc`、立即成交否则撤销 `Ioc`、只挂单 `Alo`、止损／止盈触发单。第一版不必实现全部类型。
- 请求成功不等于订单成功。核对单笔结果；部分成交、撤单或平仓后，继续确认实际订单和剩余仓位。
- SDK 市价开平仓辅助方法默认滑点限制为 5%，这是工具默认值，不是我们已经认可的参数。

依据：[参与指南](https://perpetual-alpha26.github.io/docs/)、[价格与数量规则](https://perpetual-alpha26.github.io/docs/#price-and-size-rules)、[订单说明](https://perpetual-alpha26.github.io/docs/#orders-and-leverage)。

## 4. 官方机制与自定方案

| 内容 | 官方机制 | 我们需要讨论的选择 |
|---|---|---|
| 账户 | 主办方提供资金；队伍钱包授权 API 钱包，设置统一账户 | 凭证管理和运行方式 |
| 保证金、杠杆、强平 | 确实存在；统一 `USDPR` 余额支撑仓位；明确设置市场杠杆 | 倍数、可用额度余量；不自建清算系统 |
| 手续费、资金费 | 成交收费，资金费每小时结算 | 持仓时间、订单类型、成本门槛 |
| 总体仓位 | 账户和市场有交易额度约束 | 单资产、全账户、挂单占用、亏损限制 |
| 资产与策略 | 在公布的市场范围内交易 | 交易哪些资产、使用什么信号，尚未确定 |

此前的 1 倍杠杆、10% 仓位、2% 当日亏损限制和 5% 回撤限制都是候选建议，不是比赛规定或已确认参数。[官方交易机制](https://perpetual-alpha26.github.io/docs/#venue-mechanics)

## 5. 如何使用这份清单

以下是项目建议：按数据覆盖、盘口深度、价差、资金费、波动、可量化信号频率及维护成本，比较候选资产，再共同选择少量市场。每个候选形成观察卡：代码与单位、历史范围、报价与深度、成本、候选信号、机会频率、风险及待确认事项。不能只凭宏观故事或杠杆上限决定交易对象。

参赛资格另行跟踪：至少 10 个不同日期交易，并达到最低累计名义成交额。公开规则未给出成交额具体阈值，需要确认；不得自成交或刷量。[比赛规则](https://perpetual-alpha26.github.io/about)

关联文档：[数据开发规划](data-development.zh-CN.md)、[决策与仓位框架](decision-framework.zh-CN.md)、[宏观研究想法](research-ideas.zh-CN.md)。本文仅整理事实与规划，没有实施配置或交易。
