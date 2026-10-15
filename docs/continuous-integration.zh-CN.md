# GitHub Actions CI｜自动检查

CI（Continuous Integration，持续集成）在提交后自动安装项目、运行检查，并把结果显示在 GitHub commit / PR 页面。它能发现测试回归、明显代码错误、未更新的契约和不能跑通的模拟链路；不能证明策略盈利、真实交易安全或架构设计正确，也不是 AI 自动批准代码。

## 触发与检查

配置位于 [ci.yml](../.github/workflows/ci.yml)。推送任意分支、创建或更新 PR 时自动运行；也可在 [GitHub Actions 页面](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/actions/workflows/ci.yml) 点击 Run workflow 手动启动。同一分支有更新时取消旧运行，避免重复等待。

测试环境以现有服务器的**应用容器**为准：宿主机记录为 Ubuntu 26.04 x86_64，程序使用 `deploy/Dockerfile` 中固定摘要的 Python slim 镜像，Python 为 3.12.15，31 个运行依赖固定在 `deploy/requirements-check.txt`。不再运行 Windows / Python 3.11、3.13 矩阵。

GitHub 的 ubuntu-24.04 runner 仅负责构建/启动 Linux amd64 容器，不代表服务器宿主机版本已改成 24.04。CI 复用现有部署 Dockerfile 构建 `perpetual-alpha-runtime:ci`，然后由 `deploy/Dockerfile.ci` 增加检查工具和完整源码。这保留应用镜像/解释器/运行依赖的一致性；不模拟服务器内核、资源限额、网络、磁盘或持续运行。

每次运行执行：

1. 构建现有服务器运行镜像，再构建检查层；检查工具版本在 `config/requirements-ci.txt` 中固定。
2. `scripts/validate_runtime.py` 检查解释器与 `.python-version` 完全一致，并逐项验证 31 个运行依赖；再执行 `pip check` 检查依赖兼容性。
3. `python -m ruff check . --no-cache --output-format=github`：按 pyproject.toml 中已有规则检查语法相关错误、未使用名称和导入顺序，在 GitHub 标注错误位置。
4. `python -m unittest discover -s tests -v`：运行全部离线回归测试。
5. `python scripts/validate_schemas.py`：用 Draft 2020-12 验证七份 JSON Schema、文档中的交易/研究契约样例，同时检查 Schema 与 Python 类型一致。
6. `python -m perpetual_alpha.cli dry-run --journal /tmp/ci-dry-run.sqlite3`：实际跑通离线模拟入口；失败会使检查失败。

检查容器以 UID 1000 运行，根文件系统只读，仅 /tmp 为可写临时目录，`--network none` 禁止测试与模拟访问网络。镜像构建时下载基础镜像与依赖。checkout 使用固定官方 Action 提交和只读 `contents: read` 权限，不保留 Git 凭据。CI 不配置交易密钥、不访问真实账户或远端服务器、不启动采集服务或运行 `test_order.py --execute`，不自动部署或合并代码。

## 开发者如何使用

推送后打开 Actions 页面或 PR 的 Checks，绿色表示配置的检查通过，红色表示至少一项失败。点开失败 job 的失败 step 查看具体报错；修复并重新推送会自动重新检查。测试通过仍需要人检查业务逻辑、边界和变更意图。

若要强制“CI 不通过就不能合并”，需要另外在仓库的 Rulesets / Branch protection 将对应检查设为 required。本次只添加工作流，没有修改仓库保护规则；CI 失败本身不会撤销已推送的提交。

本地复现相同容器（需要已运行的 Docker Linux Engine）：

```powershell
docker build --platform linux/amd64 -f deploy/Dockerfile -t perpetual-alpha-runtime:ci .
docker build --platform linux/amd64 -f deploy/Dockerfile.ci -t perpetual-alpha-checks:ci .
docker run --rm --network none --read-only --tmpfs /tmp perpetual-alpha-checks:ci python scripts/validate_runtime.py
docker run --rm --network none --read-only --tmpfs /tmp perpetual-alpha-checks:ci python -m pip check
docker run --rm --network none --read-only --tmpfs /tmp perpetual-alpha-checks:ci python -m ruff check . --no-cache
docker run --rm --network none --read-only --tmpfs /tmp perpetual-alpha-checks:ci python -m unittest discover -s tests -v
docker run --rm --network none --read-only --tmpfs /tmp perpetual-alpha-checks:ci python scripts/validate_schemas.py
docker run --rm --network none --read-only --tmpfs /tmp perpetual-alpha-checks:ci python -m perpetual_alpha.cli dry-run --journal /tmp/local-ci.sqlite3
```

不启用 Docker 时，也可在现有项目虚拟环境安装 `config/requirements-ci.txt` 后运行上述 Python 检查命令；Windows 沙箱临时目录设置见[架构实现说明](architecture-implementation.zh-CN.md#7-验证)。本地解释器/31 项依赖、Ruff、Schema 和 35 项测试已通过；容器环境的检查结果以 GitHub Actions 实际运行记录为准。

官方参考：[GitHub 的 Python 构建与测试指南](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)、[工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)。
