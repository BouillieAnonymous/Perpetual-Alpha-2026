# Research workspaces

Research is evidence, not a production strategy recommendation. See the
[consolidated index](../docs/research/INDEX.md) for hypotheses, negative results,
data identities and reproducibility limits.

- `legacy/`: original source, scripts, tests and dependency pins from PRs
  #13 → #17 and #15 → #20, plus recovered offline study scripts.
- `experimental/websocket/`: PR #16 collector prototype, never imported by the
  production package or enabled in deployment.
- `tools/`: maintained consolidation and verification utilities.

The historical `perpetual_alpha` modules remain byte-identical snapshots. They
are **not** part of `src/perpetual_alpha`, the installed wheel, or the production
runtime image. Historical documents keep their original paths and statements;
their relative script links describe the original layout. `source-lock.json`
maps every relocated source file to its original commit, path and blob.

## Disposable test workspace

Run from a clean committed repository with Python 3.12.15 and Git installed:

```powershell
python research/tools/workspace.py ..\pa26-research-check
cd ..\pa26-research-check
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r deploy/requirements-check.txt -r config/requirements-ci.txt -r config/requirements-quant-research.txt -r config/requirements-research.txt
$env:PYTHONPATH="$PWD/src;$PWD/tests;$PWD/scripts"
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

On POSIX use `.venv/bin/python` and `PYTHONPATH=src:tests:scripts`. The exported
workspace overlays only isolated research source/scripts/tests and optional
dependency files onto a Git snapshot. It refuses to replace different existing
baseline files. Add `--experimental` to include the WebSocket prototype's tests.
Do not copy `.env`, wallet files or account databases into this workspace.

Some full-repository tests require Linux file locks/permissions. GitHub CI runs
those checks on Linux. Local Windows failures/skips are reported separately.
The independent research environment does not alter production requirements.

Original research runners can write beside their source, overwrite verifier
reports, or rely on historical `.cache` paths. Run them only in a disposable
copy or with an explicitly new output directory and the exact documented public
input hashes. A successful test suite is not a full historical-data rerun.

Frozen legacy formatting exceptions are narrow per-file Ruff exceptions for
style/import ordering only. Undefined names, syntax and other selected checks
remain enabled. New maintained tooling has no such exceptions.

## Historical collection helpers: NOT RUN

The archive also preserves original SSH/public-HTTP/WebSocket capture and public
leaderboard helpers, and some already-versioned infrastructure metadata. They
are historical network tools, not offline reproduction commands. They were not
executed during consolidation and must not be used against production services
or accounts. Review metadata before any future public release; current
repository visibility stays private. Original compaction/seal/report modules can
write or rename outputs even on import: only import them in disposable copies.
