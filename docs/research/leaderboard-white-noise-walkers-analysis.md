# White-Noise-Walkers 公开交易行为研究

研究日期：2026-10-20。证据抓取始于 **14:06:24 UTC（伦敦 15:06:24 BST）**；各请求时间、HTTP 状态与 SHA-256 见 [manifest.json](white-noise-walkers-2026-10-20/manifest.json)。研究对象为官方排行榜队名的精确匹配，不将同名网络身份认定为账户所有人。

## 结论

**未能从本次核查的官方公开资源可靠确定 White-Noise-Walkers 的 Hyperliquid Testnet 团队钱包地址，因此无法开展账户级交易行为归因。** 已取得官方公开收益序列，但没有该队的逐笔成交、仓位、订单或资金流水。不能判断其收益主要来自持续方向持仓还是频繁短周期交易，也不能将它认定为动量、均值回归、做市或资金费率策略。

“第一名”需要区分阶段和指标：练习赛最终正式 Score 排名确实为第一；本次实盘快照中，它的每日 PnL 与最新 PnL 图表均居第一，但所有队伍的正式 `rank` 均为 null，尚未形成 Score 排名。不能把图表 `position=1` 当成正式综合评分第一。

按任务要求，在核心账户数据不可归属后停止策略归因，仅提供数据可用性和收益序列的描述性事实。没有为了补齐报告而猜测钱包，也没有进行曲线相似度寻址。

## 一、来源与核查范围

| 官方来源 | 本次用途与结果 |
|---|---|
| [排行榜页面](https://perpetual-alpha26.github.io/leaderboard) | 读取完整 HTML 与内嵌 JavaScript，确定实际公开数据地址、图表时间轴和评分规则 |
| [参与者文档](https://perpetual-alpha26.github.io/docs/) | 确认 Testnet、PAS/USDP、账户查询方式、估值与资金费率口径 |
| [官方 FAQ](https://perpetual-alpha26.github.io/faq/) | 未给出团队钱包表或交易记录下载；提交代码供评委审阅，不等于公开策略源码 |
| [官方公开网站源码](https://github.com/perpetual-alpha26/perpetual-alpha26.github.io/tree/9a408fb0061427da6782000bd4c8dbb1bb6ee6a8) | 固定仓库 revision `9a408fb0061427da6782000bd4c8dbb1bb6ee6a8`，检查全部 21 个匹配文本扩展名的源文件；跳过生成目录 `_site` 的重复版本 |
| [排行榜配置](https://github.com/perpetual-alpha26/perpetual-alpha26.github.io/blob/9a408fb0061427da6782000bd4c8dbb1bb6ee6a8/_data/leaderboard.yml) | 公开四个 JSON URL；没有额外账户查询入口 |
| [团队元数据](https://github.com/perpetual-alpha26/perpetual-alpha26.github.io/blob/9a408fb0061427da6782000bd4c8dbb1bb6ee6a8/_data/teams.yml) | 只有 `name`、`email` 字段；存在目标队名，但没有钱包字段或地址。未把联系人用于身份关联；本地仅留字段检查与内容哈希，不保存该文件原文 |
| [Hyperliquid Info 文档](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint) | 核查账户成交、历史订单及 portfolio 等公开查询能力和历史保留限制 |

另读取官方 GitHub 用户公开仓库列表，返回一个网站仓库。未访问登录后台、参赛者提交系统、私人仓库、Discord 或私人凭据。网页搜索未找到可采信的官方钱包声明，未将无关搜索结果作为证据。这是对上述范围的可用性结论，不声称互联网上绝不存在公开声明。

四个实际访问成功、无需认证的公开数据端点：

| URL | 内容 | 数据生成时间 UTC |
|---|---|---|
| [leaderboard.json](https://d23m2l9bjnbgul.cloudfront.net/public/leaderboard.json) | 实盘每日指标 | 2026-10-20 14:04:00.00056236 |
| [chart.json](https://d23m2l9bjnbgul.cloudfront.net/public/chart.json) | 实盘最新 PnL 前十及十分钟序列 | 同上 |
| [practice.json](https://d23m2l9bjnbgul.cloudfront.net/public/practice.json) | 练习赛最终每日指标 | 2026-10-18 12:36:41.029620936 |
| [practice-chart.json](https://d23m2l9bjnbgul.cloudfront.net/public/practice-chart.json) | 练习赛冻结图表 | 同上 |

请求时间与数据生成时间不同；在线 URL 会更新，报告数值以保存的快照为准。

## 二、为什么不能可靠关联钱包

实盘及练习榜团队字段都是：

```text
name, ranked, return, volatility, volatility_horizon,
rank, q, score, eligible, reasons
```

图表团队字段都是 `name, position, pnl, last_at, values`。四个 JSON 均没有 42 字符 EVM 地址、钱包字段或账户链接。网站源文件扫描也未发现可关联目标队名的钱包地址，详见 [source-audit.json](white-noise-walkers-2026-10-20/source-audit.json)。这一结果来自实际字段检查和源文阅读，而非单凭页面没有显示地址。

官方文档要求账户查询传入**团队主钱包地址**，API agent 地址不代表其余额与持仓。队名和排名不是 Hyperliquid `user` 参数，公开市场交易也不自动提供“此地址属于该队”的证明。共同获资时间、相同收益、队名含义都不能替代官方明示关联。

因此本轮没有对任何猜测地址请求账户数据。这里的障碍是**身份映射缺失**，不是已经验证该队成交接口报错或禁止公开读取。没有尝试绕过鉴权、扫描隐藏端点或枚举钱包。

## 三、官方账户接口能提供什么

比赛文档规定查询使用 `POST https://api.hyperliquid-testnet.xyz/info`，无需私钥；实盘是 `pas`，练习赛使用 `par`。下面是文档支持的能力说明，**不代表已实测取得该队数据**。

| 需求 | 公开查询与参数 | 研究限制 |
|---|---|---|
| 成交、手续费、平仓盈亏 | `userFills` / `userFillsByTime`：`user`、时间范围 | 文档称前者最多最近 2000 条；后者每次最多 2000 条且只保留最近 10000 条。分批请求不能突破保留上限；部分成交不能直接当作独立交易 |
| 当前仓位、未实现收益 | `clearinghouseState`：`user, dex:"pas"` | 是当前状态，不是任意历史时点持仓。历史重建需完整 fills、可靠起始仓位、清算等事件并与末端对账 |
| 当前挂单、订单状态 | `openOrders`：`user, dex`；`orderStatus`：`user, oid` | 当前挂单不能反映过去挂单／撤单频率 |
| 历史订单 | `historicalOrders`：`user` | Hyperliquid 文档描述最多最近 2000 条；不是完整逐次修改事件流，需按资产前缀过滤，Testnet/PAS 实际覆盖未验证 |
| 账户资金费支付 | `userFunding`：`user, startTime, endTime` | 不能用市场资金费率直接代替账户实际收入 |
| 市场资金费、行情 | `fundingHistory`：`coin`、时间；`metaAndAssetCtxs`：`dex`；`candleSnapshot`：`req` | 资产须为 `pas:BTC` 等；原生 BTC 或主网行情不是 PAS mark 历史 |
| 权益、抵押品、权益历史 | `spotClearinghouseState`、`clearinghouseState`；Hyperliquid `portfolio`：`user` | Unified 账户需核对现货抵押与永续估值，避免重复计入或遗漏；portfolio 对 Testnet 自建 dex/USDP 的覆盖未验证，不能预先视为比赛完整权益账本 |

历史请求须依接口规定分页并检查边界、同毫秒多笔及重复记录。文档总体分页规则与各端点专属上限需一并遵守。没有完整覆盖证据时，不能把“返回一批记录”称为全历史。

## 四、实际可观察的收益事实

### 4.1 阶段与排名

| 指标 | 实盘快照 | 练习赛最终快照 |
|---|---:|---:|
| 阶段起点 UTC | 2026-10-19 12:00 | 2026-10-05 12:00 |
| 阶段终点 UTC | 2026-11-06 12:00（未来计划） | 2026-10-18 12:00 |
| 榜内团队数 | 47 | 64 |
| 有数值正式 rank 的团队数 | 0 | 46 |
| 目标正式 rank | null | 1 |
| 目标 eligible | false，`few_daily_returns` | true |
| 每日采样累计净收益率 | +1.014956672% | +0.2910154111% |
| Score | null | 0.1064149578359402 |
| 最新图表 position | 1 | 1 |
| 最新图表收益率 | +0.76494% | +0.29102% |
| 最新图表实际快照 UTC | 2026-10-20 14:01:01.828 | 2026-10-18 11:57:25.543 |

前端默认按 Score 排序；`ranked=true` 在此不能解读成已有正式名次，因为同一行仍然 `rank=null, eligible=false`。实盘两种 PnL 的差异主要是采样时点口径不同，不能解释为已经平仓获利或已支付某项费用。

### 4.2 口径与描述统计

排行榜 `return` 是扣除转入转出影响的时间加权净收益率，不是美元已实现利润。前端说明权益包含按 mark 估值的未实现盈亏。每日采样在 12:00 UTC，实盘配置 `sample_after_s=240`，选取从该时刻启动的轮次中的首个快照；漏采时使用此前最后快照。练习赛该配置为 0，使用截止前最后快照。

图表每十分钟一个网格点，前端每五分钟刷新，并在 `last_at` 晚于最后网格时追加 `pnl`。脚本按 `start + i × step_s` 还原网格，保留 null，既不插值也不跨缺口计算十分钟变化。保存数据支持以下描述：

| 序列统计 | 实盘 | 练习赛 |
|---|---:|---:|
| 网格点数 | 157 | 1873 |
| 缺失网格点 | 0 | 119 |
| 网格起止 UTC | 10-19 12:00 至 10-20 14:00 | 10-05 12:00 至 10-18 12:00 |
| 可用相邻十分钟变化对 | 156 | 1750 |
| 上升／下降／不变区间数 | 83／70／3 | 485／501／764 |
| 观测最小／最大累计收益率 | -0.11684%／+1.10776% | -0.44867%／+0.38848% |
| 归一化财富序列观测最大回撤 | 0.406803% | 0.570839% |

回撤计算为 `W=1+图表收益率`，取 `min(W_t / max(W_s, s≤t) - 1)` 的绝对值；它是该公开序列上的采样回撤，不是账户完整高频权益回撤。练习图表的最后网格时间晚于其 `last_at`，因此没有另行附加一个更早的点，也不能把终点网格标签当作实际账户采样时间。

**这些区间不是成交笔数。** 没有交易时 mark、基差和资金费也能改变权益；十分钟内大量交易也可能最终权益几乎不变。不能由 153 个非零变化区间推导实盘至少发生 153 次交易，也不能由练习赛平坦区间推出空仓。

本次没有将初始配资乘以收益率估算账户美元利润，因为未取得该队完整转账及权益账本。

## 五、交易行为问题的证据评估

| 问题 | 本轮结果 |
|---|---|
| BTC、ETH 或其他 PAS 资产分布 | 不可计算。官方列出 BTC、ETH、HYPE、SP500、EWY、EWJ、BRENT、GOLD、USDEUR、USDJPY；上架清单不是该队持仓清单 |
| 分钟／小时／更长持仓时间 | 不可识别，缺少带方向和仓位起点的成交序列 |
| 每小时交易频率、成交额、仓位变化 | 不可计算，收益曲线的更新频率不是交易频率 |
| 多空比例、净敞口、杠杆 | 不可计算。市场最大杠杆或账户设定杠杆也不等于实际名义敞口／权益 |
| 已实现、未实现、手续费、资金费贡献 | 不可拆分，只有包含未实现估值的聚合收益率 |
| 少数大盈利还是大量小盈利 | 不可判断。需定义交易配对／仓位周期并获得完整平仓收益，而非用权益上涨区间计数 |
| 权益与主要资产价格关系 | 未计算相关系数；没有取得同步 PAS 历史 mark／oracle 面板和该队现金流调整权益。遵循核心数据不可归属即停止归因的边界 |
| 主要靠持续方向仓位还是频繁短周期交易 | **目前无法区分，核心假设既未证实也未否定** |

特别检验的最低证据是完整窗口内的仓位路径、持仓周期、换手率，以及对净收益的方向敞口解释比例。单一方向仓位会随价格产生权益波动；不断开平仓的策略也能产生相近波动。因此“曲线持续变化／总体上涨”不是高频交易或持续持仓的充分证据。

若未来仅有权益序列与行情，可对同步**收益变化**而非累计曲线水平做相关／回归；PAS mark 与 oracle 应分别分析，排除缺失、陈旧快照、资金流与错位时间，进行分段和样本外检验。即使得到稳定 beta，它也只支持某段时间存在方向性价格暴露，不能证明持仓不变、交易频率低，或入场信号是动量。多资产共线性、基差、时变仓位和费用仍然造成不可识别性。本轮未报告未经计算的相关系数。

## 六、策略类型的结论分级

| 证据等级 | 结论 |
|---|---|
| 已验证事实 | 官方公开聚合收益与名次；练习赛正式第一；当前实盘 PnL 第一但无正式 Score 名次；核查的官方资源未给出钱包映射 |
| 有数据支持的推断 | 该队公开聚合收益路径在观察窗口内有涨有跌，不能把期末正收益视为每个区间都赚钱；除此之外没有足够证据支持具体交易机制 |
| 目前无法区分 | 持续方向仓位、趋势跟随、均值回归、做市、资金费率交易及其混合策略；这些只是待检验类别，并非已发现策略特征 |
| 缺乏证据的猜测 | “第一所以是动量”“队名暗示随机游走／均值回归”“十分钟波动表示十分钟交易”“上升主要来自已实现利润”；本报告均不采纳 |

做市需要双边挂单、maker 成交、库存回归与撤单证据；资金费率交易需要实际支付收入、跨时点敞口及可能的对冲腿；趋势与均值回归需要入场前信号和后续仓位响应。当前数据均未满足。

## 七、复现与产物

只读脚本：[study_leaderboard_white_noise_walkers.py](../../scripts/study_leaderboard_white_noise_walkers.py)。仅使用 Python 标准库和 GET 请求，无项目交易模块导入、环境文件读取、签名或交易接口调用；网络目标固定为官方页面、官方公开 GitHub 资源及前端明示的 CloudFront。不会查询本地账户。

在项目根目录 PowerShell 执行：

```powershell
# 完全离线：使用本报告的已保存快照重新生成 summary.json
.\.venv\Scripts\python.exe scripts/study_leaderboard_white_noise_walkers.py --offline docs/research/white-noise-walkers-2026-10-20

# 重新调查：须指定尚不存在的目录，避免覆盖本次证据；在线值可能变化
.\.venv\Scripts\python.exe scripts/study_leaderboard_white_noise_walkers.py --output docs/research/white-noise-walkers-new-snapshot
```

证据目录 [white-noise-walkers-2026-10-20](white-noise-walkers-2026-10-20/) 包含四个原始 JSON、网页与固定版本源码、仓库树、公开仓库列表、字段审计、请求清单和 [summary.json](white-noise-walkers-2026-10-20/summary.json)。未保留团队联系人文件原文；其请求哈希和字段审计保留。离线模式只重新生成摘要，不发网络请求。

后续开展账户分析的前提是获得可引用的官方队名—团队钱包声明，并确认历史记录覆盖范围。当前阶段不输出策略参数建议，也不据此调整本项目策略。全程未读取交易私钥或 `.env`，未提交订单、改动 Collector／策略／风控／交易配置，未访问或部署 VPS 服务，未自动合并或部署。
