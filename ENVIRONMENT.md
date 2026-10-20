# Project runtime environment

Local project Python and the deployed collector container use Python 3.12.15.
The 31 runtime dependency versions are pinned in `deploy/requirements-check.txt`.
Windows and Linux use different platform builds of the same Python and library versions.

Recreate the local environment with uv from the project root:

```powershell
uv venv --python 3.12.15 .venv
uv pip install --python .venv/Scripts/python.exe -r deploy/requirements-check.txt -e .
uv pip check --python .venv/Scripts/python.exe
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

If an older uv cannot find Python 3.12.15, use a newer uv or provide the path to an installed Python 3.12.15 interpreter to `uv venv --python`.
Use `.venv\Scripts\python.exe` explicitly rather than the Windows Python application alias.
The virtual environment is managed with uv and does not require pip to be installed inside it.

Verified on 2026-10-08: Python 3.12.15, all 31 pinned runtime versions matched,
dependency compatibility checks passed, and all seven unit tests passed.
The deployed collector remained unchanged. Local Docker/Compose versions and the operating system are not aligned by this Python setup.

`.venv/`, `.cache/`, and `.env` are local files excluded from Git.

## CI environment

GitHub Actions builds the existing `deploy/Dockerfile` as a Linux amd64 image, then adds the tools from `config/requirements-ci.txt` using `deploy/Dockerfile.ci`. Checks run inside that image as UID 1000 with networking disabled and a read-only root filesystem. `scripts/validate_runtime.py` verifies `.python-version` and all 31 pinned runtime packages; `pip check` validates dependency compatibility.

The documented server host is Ubuntu 26.04 x86_64. GitHub's Ubuntu 24.04 runner only hosts Docker; CI matches the application's container image, not the server kernel, resources or networking. It does not connect to or deploy onto the server. See [CI instructions](docs/continuous-integration.zh-CN.md).
