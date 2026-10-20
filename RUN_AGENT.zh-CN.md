# 本地填写 Jev API Key

这个目录是从 V1 ZIP 解压后修复的完整源码。旧 ZIP 保留原样，后续编辑以本目录为准。

## 先填密钥

打开根目录 **`.env.jev` 的第二行**，把你自己的 **TypeSafe 直连 Key** 放在 `TYPESAFE_API_KEY=` 后。模型版本已设为 `jev-1.14.0`。不需要改 Python 请求代码；`src/perpetual_alpha/agent/jev_api.py` 已实现调用和响应校验。

`.env.jev` 被 `.gitignore` 排除，分享时只分享 `config/jev.env.example` 模板。真实密钥不需要发到聊天里。运行前若已设置同名环境变量，会优先使用环境变量。

## 填完后怎么运行

在 VS Code 终端进入本目录。Agent 自身使用标准库，Python 3.11–3.13 可运行；读取原 PAS 派生数据库及运行全仓测试时，需要原仓库依赖，环境准备见 `ENVIRONMENT.md`。

```powershell
$env:PYTHONPATH = "src"
python -m perpetual_alpha.agent fixture --out data/agent/snapshot.synthetic.json
python -m perpetual_alpha.agent decide --snapshot data/agent/snapshot.synthetic.json --router jev --journal data/agent/jev-v11.sqlite3 --out data/agent/jev-decision.json
```

第二条命令会请求 TypeSafe，使用你填写的 Key；只输出决策和 Paper 目标，不会给交易所发送订单。相同快照与 journal 会返回已保存结果；想做一次新的模型实验需使用新的 journal 文件名。默认 2000ms inference budget，网络慢时会拒绝迟到分布，若调整须同步核对 `config/agent-v1.toml` 和 `.env.jev` 的超时。

不需要 Key 的离线功能检查：

```powershell
python -m perpetual_alpha.agent demo --config config/agent-v1.toml --journal data/agent/rules-v11.sqlite3 --out data/agent/rules-v11.json
python scripts/validate_agent.py
python -m unittest discover -s tests -p "test_agent*.py" -v
```

完整说明见 `docs/agent-v1.zh-CN.md`；本次修复记录见 `docs/agent-v1.1-fixes.zh-CN.md`。

目前没有真实 PAS 数据库随包提供，因此没有做新的真实行情盈利验证。盘口深度、新闻生产模块和多策略 PAS 签名执行也没有伪装成已完成；Jev 的真实服务连通性需填 Key 后验证。
