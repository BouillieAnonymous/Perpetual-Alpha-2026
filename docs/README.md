# Technical Documentation

Maintained navigation · 7 November 2026.

[Project overview](../README.md) · [System design](../README.md#system-design) ·
[Research findings](research/FINDINGS.md) · [Competition report](competition/README.md)

This index points to the canonical component explanations already in the
repository. It is not a new unified architecture specification. PR0–PR9
implementation records describe their own stages; statements such as “HTTP
pending” or “PR9 awaiting review” retain their original dates and do not describe
the current integrated mainline. Use the later component record for its scope.

**Reading labels:** *implementation record* explains implemented code and its
dated tests; *historical deployment record* describes a past acceptance, not
current server status; *synthetic* means local simulation without live trading
authority; *experimental/proposal* is not adopted or deployed; *historical design*
and *snapshot* retain an earlier plan or observation.

## System Architecture & Design

| Document | Purpose and status |
|---|---|
| [System design overview](../README.md#system-design) | Current reader-facing boundaries, separate execution paths and code/test links. |
| [Forecast integration v0.2.1](forecast-integration-v0.2.1.zh-CN.md) | Implementation/interface record: ForecastProducer/AgentAdapter, time semantics, registry and selected/shadow roles. |
| [Repository structure and isolation](consolidation/repository-structure.md) | Maintained module map; installable runtime versus isolated legacy research and prototypes. |
| [Architecture v0.2](architecture-v0.2.zh-CN.md) | **Historical design baseline** for the first simulation architecture, not a complete current implementation specification. |

## Core, Worker, Scheduler and Outbox

| Document | Purpose and status |
|---|---|
| [PR0 — Agent Transport and Ingress](agent-transport-v0.1.zh-CN.md) | Implementation record: wire contracts, status axes, ingress validation, identity and idempotency boundaries. |
| [PR1 — SQLite jobs, scheduler and Outbox](runtime-jobs-pr1.zh-CN.md) | Implementation record: job admission/leases, trigger coalescing, atomic receipts, delivery fencing and crash/concurrency tests. |
| [PR2 — Core ↔ Worker HTTP](http-loop-pr2.zh-CN.md) | Implementation record: authenticated processes, Dummy Worker spool, frozen inputs and Mock execution. Synthetic only. |
| [Schema definitions](schemas/) and [contract examples](examples/) | Machine-readable contract references and fixtures; validation is described in the testing section. |

## Data Collection and Data Gateway

| Document | Purpose and status |
|---|---|
| [PR3 — Historical market data](historical-market-data-pr3.zh-CN.md) | Implementation record: public collection, metadata, closed-candle versions and as-of availability. |
| [PR4 — Dynamic Data Gateway](dynamic-data-gateway-pr4.zh-CN.md) | Implementation record: capability-bounded multi-round reads, pinned snapshots, query budgets and sealed evidence. |
| [PAS public collector](pas-public-collection.zh-CN.md) | Implementation and historical deployment record: separate ten-market quotes/funding collection and quality labels. |
| [PAS research data pipeline](pas-research-pipeline.zh-CN.md) | Implementation record: incremental extraction, causal minute panels, quality/volatility/funding reports and exports. |
| [Read-only SSH CSV export](pas-ssh-csv-export.zh-CN.md) | Implementation/operations record: restricted export capability, installation, bounds and rollback. |
| [CSV download guide and delivery](pas-ssh-csv-delivery.zh-CN.md) | Historical delivery record and usage guide for full/ranged exports and datasets. Remote access is not part of local setup. |
| [PAR/PAS environment separation](rehearsal-competition-separation.zh-CN.md) | Environment identity, collateral/source separation and independent public collection; dated activation statements remain historical. |
| [Ten-asset WebSocket collector](multi-asset-collection.zh-CN.md) | **Experimental historical prototype** from PR #16, isolated under research; not a replacement for the maintained PAS collector. |
| [Data sources and trading rules](data-development.zh-CN.md) | **Historical reference** to markets, data providers and competition assumptions; verify dated venue rules before reuse. |

## Portfolio and Risk Management

| Document | Purpose and status |
|---|---|
| [PR5 — Read-only account Adapter](read-only-account-pr5.zh-CN.md) | Implementation record: balances, positions, order evidence and unknown/unverified state; observation does not grant execution authority. |
| [PR6 — Market risk and frozen replay](market-risk-replay-pr6.zh-CN.md) | Implementation record: returns/covariance, explicit windows/units, PSD/singular cases and reproducible inputs. |
| [PR7 — Portfolio, Risk Gate and Supervisor](portfolio-risk-pr7.zh-CN.md) | **Synthetic implementation**: sizing, independent exposure checks, state-bound approval and pause/resume behavior. |

## Live Trading and Execution

The competition runner is deterministic and independent of generic Agent/Core.
The following are dated implementation and acceptance records, not permission
to operate an account or assertions about a currently running service.

| Document | Purpose and status |
|---|---|
| [Funding-LAST dual-environment release](funding-release.zh-CN.md) | Dedicated runner, fixed signal, durable intents, reconciliation, risk policy, PAR acceptance and environment gates at that stage. |
| [PAS launch preparation](pas-launch-preparation.zh-CN.md) | Later competition admission, separate signing/leverage approval, monitoring and launch limitations. |
| [19 October independent launch acceptance](operations/pas-launch-2026-10-19.zh-CN.md) | **Historical deployment record**: checks and unresolved conditions on that date; preserve the original evidence. |

## Synthetic Paper and Operations

| Document | Purpose and status |
|---|---|
| [PR8 — Recoverable Paper execution](paper-execution-pr8.zh-CN.md) | **Synthetic implementation**: persistent intents/partial fills, ledger recovery, atomic local feedback and Core integration. |
| [PR9 — Isolated Paper operations](paper-operations-pr9.zh-CN.md) | **Synthetic implementation/acceptance record**: readiness versus liveness, WAL backup, isolated restore and fault tests. |

Local transaction guarantees apply to the synthetic ledger, not exactly-once
exchange execution. Live reconciliation is documented in the separate runner records above.

## Testing, Deployment and CI

| Document | Purpose and status |
|---|---|
| [Environment and pinned runtime](../ENVIRONMENT.md) | Local Python/dependency setup and platform requirements. |
| [Quick start and offline checks](../README.md#getting-started) | Credential-free Demo, regression-suite and Schema commands. |
| [Continuous integration](continuous-integration.zh-CN.md) | CI usage and environment record; [current workflows](../.github/workflows/) define the executable checks. |
| [Consolidation tests and security](consolidation/test-security-report.md) | Dated validation evidence with PASS/FAIL/BLOCKED/NOT RUN distinctions; not an ongoing security certification. |
| [Reproducibility report](consolidation/reproducibility-report.md) | Dated numerical/byte checks, raw-input requirements and known reproduction limits. |
| [Remaining integration issues](consolidation/remaining-issues.md) | Preserved browser/manual acceptance gaps, historical inputs and manifest/line-ending issues. |
| [Final integration report](consolidation/final-integration-report.md) | **Pre-merge snapshot** of integration scope, checks and limits; later merge state is in PR disposition. |
| [PR disposition](consolidation/pr-disposition.md) and [source lock](consolidation/source-lock.json) | Final source-PR decisions and exact provenance/file mappings; original dated evidence remains intact. |

## Research, Competition Report and Historical Records

### Research and competition evidence

| Document | Purpose and status |
|---|---|
| [Research Findings](research/FINDINGS.md) | Maintained synthesis of hypotheses, exploratory positives, failed validation, execution unknowns and limits. |
| [Complete Research Index](research/INDEX.md) | Canonical entry points for **all archived study groups**: reports, protocols, scripts, frozen results, dashboards and source PRs. |
| [Disposable research workspace](../research/README.md) | Isolated legacy dependencies, reconstruction/check commands and missing-input policy. |
| [Competition report and display source](competition/README.md) | Four-page report, matching email-free display TeX, hashes and historical-snapshot context. |
| [Original report evidence checklist](competition/report-evidence-checklist.md) | **Historical submission evidence** and support-package provenance; original ZIP-relative paths are not current repository paths. |
| [Reference-basis study](reference-basis-research.zh-CN.md) | Original basis/cost study; follow the Research Index for subsequent direction and multi-asset evidence. |
| [PAS research records, 19–20 October](research/pas-research-records-2026-10-20.md) | Historical research-round record linking frozen rules and reproduction evidence. |
| [News/event Agent proposal](research/news-event-agent-2026-10-18.zh-CN.md) | **Proposal only**; no implemented/deployed news trader established by this document. |

### Historical design and planning records

These remain discoverable without presenting their old “pending” statements as
the current state of main. They supplement, rather than replace, the later
component implementation records above.

| Document | Purpose and status |
|---|---|
| [Initial architecture implementation](architecture-implementation.zh-CN.md) | **Historical first-stage snapshot**: Dry Run, demonstration policies, contracts and gaps at that time. |
| [Architecture v0.3 review](architecture-v0.3-review.zh-CN.md) | **Historical review** against its fixed baseline; findings are not automatically current unresolved defects. |
| [Development baseline v0.3](development-baseline-v0.3.zh-CN.md) | **Historical plan** and accepted sequencing; later PR records describe what was subsequently implemented. |
| [System interfaces and deployment plan](technical-plan.zh-CN.md) | **Historical plan, last updated 14 October**: early combined architecture/interface/deployment context, not current unified architecture. |
| [Project plan and meeting decisions](project-plan.zh-CN.md) | **Historical project record** of responsibilities, milestones and decisions; not individual contribution verification. |
| [Strategy development and brainstorming](strategy-development.zh-CN.md) | **Historical hypotheses/proposals**; measured research conclusions are indexed separately above. |
| [Research-ideas redirect](research-ideas.zh-CN.md) | Legacy navigation pointer to the consolidated strategy discussion, not another canonical research report. |
| [Agent learning resources](agent-learning-resources.zh-CN.md) | Background reading and an earlier learning plan; reference material, not implemented functionality. |
| [14 October discussion analysis](meeting-2026-10-14-analysis.zh-CN.md) | **Historical discussion/commentary**, separate from implementation evidence. |
| [3 November sprint audit](final-sprint-2026-11-03.md) | **Dated readiness snapshot**, not the current acceptance state. |
| [Earlier documentation navigation](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/blob/efc9889cdc4d0c82454bc51f584fbf06be3cde04/docs/README.md#historical-module-index) | Immutable copy of the original October module index and document consolidation notes. |

### Maintenance and review boundaries

Consolidation [PR #44](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/44)
and presentation [PR #45](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/45)
are merged. This navigation update does not rerun research or resolve the manual
acceptance/researcher-review issues #37, #40 and #42.

Maintain one detailed explanation per component and link to it from this index.
Preserve dated protocols, deployment evidence and negative/unknown results.
Historical deployment documents contain host/IP and other operational metadata;
the report provenance also records email-bearing Git history. Review those in
a separate **Privacy & Public Release Audit** before changing visibility.
This update does not alter those records or publish the repository.
