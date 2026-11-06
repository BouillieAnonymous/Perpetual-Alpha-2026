# Repository Structure Overview

Baseline: `0c579d77282e7adffc8bd55b2b08e5f6cabc702a`.

| Location | Responsibility | Maturity / execution |
|---|---|---|
| `src/perpetual_alpha/funding_live` | Independent ETH Funding-LAST line | Existing competition runtime; unchanged |
| `src/perpetual_alpha/runtime`, `transport` | Core/Worker, jobs, frozen Gateway, Paper | Implemented Agent-ready infrastructure; synthetic execution |
| `historical`, `accounts`, risk/portfolio modules | Versioned public history, readonly evidence and independent risk | Existing implementation, unchanged |
| `research/legacy/src/perpetual_alpha` | Basis, offline_quant, market_transfer snapshots | Research-only; excluded from production wheel and image |
| `research/legacy/scripts`, `tests`, `config` | Original offline runners, regression tests and optional pins | Disposable historical layout only |
| `research/experimental/websocket` | PR16 prototype/source/tests | Experimental; no deployment wiring |
| `docs/research` | Frozen evidence and dashboards | Scientific history retained byte-for-byte |
| `research/tools` | Workspace export and integrity/navigation checks | Maintained consolidation utilities |
| `.github/workflows/research-validation.yml` | Separate optional-dependency research job | Supplements unchanged original CI |

Original scripts sometimes import `perpetual_alpha.offline_quant` or refer to
root `scripts/`. The [workspace builder](../../research/tools/workspace.py)
exports committed Git content into a new directory and overlays only the
isolated research files. Existing different baseline files are rejected rather
than replaced. This restores historical imports for tests without changing
production package paths, installation metadata, container files or policies.

[Source lock](source-lock.json) records original commit/path/blob and current
destination. Earlier relocated versions remain in the staged integration
commits. [Production baseline](production-baseline.json) locks the protected
runtime/config/deployment files and Phase1 evidence blobs.

Frozen historical relative script links refer to their original layout. Use
the provenance mapping and disposable workspace; do not rewrite frozen reports
to hide relocation. Maintained README/index links are checked separately.
