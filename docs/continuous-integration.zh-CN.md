# GitHub Actions CI｜自动检查

CI（Continuous Integration，持续集成）在提交后自动安装项目、运行检查，并把结果显示在 GitHub commit / PR 页面。它能发现测试回归、明显代码错误、未更新的契约和不能跑通的模拟链路；不能证明策略盈利、真实交易安全或架构设计正确，也不是 AI 自动批准代码。

## 触发与检查

配置位于 [ci.yml](../.github/workflows/ci.yml)。推送任意分支、创建或更新 PR 时自动运行；也可在 [GitHub Actions 页面](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/actions/workflows/ci.yml) 点击 Run workflow 手动启动。同一分支有更新时取消旧运行，避免重复等待。

检查矩阵：Ubuntu 的 Python 3.11 / 3.12 / 3.13，以及 Windows 的 Python 3.12，对应项目支持版本和本地/服务器环境。每组环境独立运行：

1. 安装 `deploy/requirements-check.txt` 中的运行依赖、`config/requirements-ci.txt` 中的检查工具，以及当前项目。
2. `python -m ruff check . --output-format=github`：按 pyproject.toml 中已有规则检查语法相关错误、未使用名称和导入顺序，在 GitHub 标注错误位置。
3. `python -m unittest discover -s tests -v`：运行全部离线回归测试。
4. `python scripts/validate_schemas.py`：用 Draft 2020-12 验证七份 JSON Schema、文档中的交易/研究契约样例，同时检查 Schema 与 Python 类型一致。
5. `python -m perpetual_alpha.cli dry-run --journal .cache/ci-dry-run.sqlite3`：实际跑通离线模拟入口；失败会使检查失败。

当前仅使用只读 `contents: read` 权限；官方 checkout / setup-python 固定到具体提交，checkout 不保留 Git 凭据。CI 不配置交易密钥、不调用真实 LLM、不访问真实账户、不启动采集服务或运行 `test_order.py --execute`，不自动部署或合并代码。依赖安装会访问包仓库；测试和模拟业务流程使用离线数据。

## 开发者如何使用

推送后打开 Actions 页面或 PR 的 Checks，绿色表示配置的检查通过，红色表示至少一项失败。点开失败 job 的失败 step 查看具体报错；修复并重新推送会自动重新检查。测试通过仍需要人检查业务逻辑、边界和变更意图。

若要强制“CI 不通过就不能合并”，需要另外在仓库的 Rulesets / Branch protection 将对应检查设为 required。本次只添加工作流，没有修改仓库保护规则；CI 失败本身不会撤销已推送的提交。

本地复现：

```powershell
.\.venv\Scripts\python.exe -m pip install -r config/requirements-ci.txt
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/validate_schemas.py
.\.venv\Scripts\python.exe -m perpetual_alpha.cli dry-run --journal .cache/local-ci.sqlite3
```

Windows 沙箱临时目录设置见[架构实现说明](architecture-implementation.zh-CN.md#7-验证)。当前本地 Ruff、Schema 和 35 项测试通过，首次远端矩阵运行以 GitHub Actions 的实际结果为准。

官方参考：[GitHub 的 Python 构建与测试指南](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)、[工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)。
