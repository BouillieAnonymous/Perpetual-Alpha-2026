# PAR 与 PAS：独立环境与公共采集

PAR 是当前已有数据与研究流程的排练环境；PAS 是正式比赛环境。PAS 正式比赛已开始，已通过独立 `venue_collection` 入口接入十资产公共只读采集，详见 [PAS 公共行情采集](pas-public-collection.zh-CN.md)。PAR 继续用于已有研究与回测。

两者共享代码库，配置与状态独立；不复制两套会逐渐分叉的代码。`systems` 模块只负责本地环境命名空间与配置准备；PAS 公共采集使用独立入口和数据库，并不代表 PAS 的 History/Account/Portfolio/Risk/签名执行全链已实现或启用。

| 范围 | PAR | PAS |
|---|---|---|
| 配置 | `config/systems/par.toml` | `config/systems/pas.toml` |
| 来源 | `hyperliquid-testnet/par` | `hyperliquid-testnet/pas` |
| 抵押品 | USDPR | USDP |
| 符号 | `par:*` | `pas:*` |
| 新环境根目录 | `<root>/par/` | `<root>/pas/` |
| 当前状态 | 保留已有采集/研究 | 独立公共采集；交易未启用 |

每个根目录分别预留 quotes、history、account、runtime、paper、journal、logs、research 路径。初始化写入身份标记，拒绝已有无标记目录、来源/抵押品错配或跨环境路径别名；不会移动、改名或复用旧数据库。账户资金、持仓、订单、决策、授权和研究结果均不得跨环境继承。

旧 `config/collector.toml`、VPS 服务、`market.sqlite3`、PAR 历史/账户 Schema 及模拟控制链保持原样。原来代码中的 PAR 限制目前仍是排练链的限制，不能仅替换前缀就宣称支持 PAS。PR #15 的原始实验和账本继续保留，其收益属于 PAR 历史情景。

本地操作（不启动采集或下单）：

```powershell
$env:PYTHONPATH = "$PWD/src"
python -m perpetual_alpha.systems inspect --system par --root .cache/systems
python -m perpetual_alpha.systems inspect --system pas --root .cache/systems
python -m perpetual_alpha.systems init --system par --root .cache/systems
python -m perpetual_alpha.systems init --system pas --root .cache/systems
```

`inspect`不创建目录；`init`只准备本地目录/身份标记。PAR另外生成兼容当前采集器的独立`collector.toml`，但不会运行；PAS不生成可运行采集配置。`collector_enabled=true`仅表示PAR环境用途，初始化不会启动进程，也不是服务器部署状态。

PAS 公共采集已经接入独立源、元数据/抵押品身份与原始数据时间质量记录。`config/systems/pas.toml` 继续保持预留且 disabled，仅约束 `systems` 初始化，不控制独立采集服务。后续带 PAS 身份的 History/Account 读取与 Portfolio/Risk/协议测试仍须分别验证。真实估值与交易准入须单独验证，服务器启动与订单操作仍需用户明确授权；不从环境准备推导交易许可。

此次暂停新闻方向，不调用新闻源或LLM服务，不增加策略、调参或启动Agent。
