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
