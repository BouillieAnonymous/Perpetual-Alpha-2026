# Evidence and review notes — 6 November 2026

The report is the requested historical 5 November 03:10 UTC case, **not latest or final results**. Report date is not backdated. The named original archive was not available; supplied historical TeX and saved private snapshot were used to reconstruct the support package. Only one report sentence changed, after the pinned runner source had been exported.

Paths in the following table are relative to the root of `PA26-Anonymous-code.zip`, unless marked support. No repository-access assumption is needed to inspect these bundled sources. `source-provenance.json` supplies full immutable revisions, source paths and SHA256; teammate files additionally identify their original ZIP member and archive hash. They are reviewable source evidence, not a certification of the running binary.

| Key | Bundled evidence | Scope and limitation |
|---|---|---|
| E1 | `production-historical/src/perpetual_alpha/funding_live/` (`policy.py`, `engine.py`, `store.py`, `venue.py`, `preparation.py`, activation/renewal/ops); `tests/test_funding_live.py` in same tree | Exact historical revision `1b9bc798ac8d6109d124a9e5ee4022b579148f1b`: deterministic signal, sizing, risk admission, durable intent, IOC/reconciliation and recovery. Code properties do not certify every historical poll. Pause does not flatten; verified reducing exits can exceed original entry cap. |
| E2 | `production-historical/deploy/compose.pas-trading.yaml`, `compose.pas-renewal.yaml`; `evidence/E2/report.zh-CN.md` | Archived 26 October deployment observation records runner revision `1b9bc79`. Initial activation and 35 control-event details remain private evidence; see the sanitized attribution below. Complete external actions, standing-renewal activation and final deployed binary remain unverified. |
| E3 | `evidence/E3/report.zh-CN.md` | PR15 head `6832145603b2bbd305fbbd91377d16538ecb5951`: 37 selected positive short-window scenarios, 36 rejected and one inconclusive on longer histories; retrospective research, not live return. |
| E4 | `evidence/E4/report.md`, `metrics.json`, `run_h09.py` | PR43 head `1cd52af25b6246d74d4abd84740035b154677448`; archived metrics/script at `324526afe7396c44da91dd52897b675373f3e906`. Validation skill +0.0418559462%, late approximately −0.01581%; error improvement, not profit. Predictive gate failed; executable-profit test not run. Data not bundled; full experiment not rerun. |
| E5 | **support:** `pas-observed-equity.csv`, `performance-verification.json`, `verify_equity.py`, `figures/` | 21,251 derived observations; +1.622088 USDP historical change; sampled drawdown 2.266909 USDP. Funding is a saved 227-record aggregate; original cash-flow rows not independently re-summable. Hashes identify the private original and delivered CSV separately. Later worse observation is retained. Private ledger/source is not publicly auditable from this package. |
| E6 | `framework-research/src/perpetual_alpha/`: contracts, forecasting, producers, runtime, transport, paper execution; matching tests, docs/examples and docs/schemas | Exact `324526afe7396c44da91dd52897b675373f3e906`; EXPERIMENTAL common framework, separate from live runner. Synthetic paper execution is not live wallet authority. |
| E7 | `teammate-agent/src/perpetual_alpha/agent/`, `tests/test_agent*.py`, `docs/agent-v1.zh-CN.md`, `docs/agent-tuning-results.zh-CN.md`, `docs/research/agent-tuning/summary.json` | Exact source members from PR31 archive at `ff82523f33dc8acb8237975635f7160b885fe948`; 54 fake-I/O tests. EXPERIMENTAL / RESEARCH_ONLY. Final saved paper net −0.895443 USDP, two completed holdings, selection bias; no PAS signing/reconciliation bridge. No new model call. |
| E8 | `evidence/E8/report.zh-CN.md`, `direction-statistics.csv` | Pinned `dee8b1bce1b649b4dc181d2fb78b69b6173efd98`, CSV at archived HEAD. 122/179 direction hits differ from the 17 executable paired events; baseline modeled net approximately −22.10 bps. No actual-fill claim. |
| E9 | `evidence/E9/report.zh-CN.md`, `protocol.json`, `selection-audit.json` | PR20 head `31668b900e9142e716e59ee828c61ece8ce524ec`: frozen regime selector stayed flat; insufficient data, not proof of no information. |
| E10 | `evidence/E10/report.zh-CN.md`, `summary.md`, `frozen-protocol.md` | PR27 `ccaa3ecf2d4d9d5160722caa0b14028cc42244f3` and PR38 `25730c16019cef4b255758e25c29c01a3635273d`: negative short-cycle cost scenarios and frozen-hypothesis no-evidence/insufficient/execution-unknown outcomes. Not every hypothesis was cost-tested. |

## Private operational evidence attribution

Existing review records attribute the 19 October activation to explicit human signer/leverage approval and resume; the record as-of is 19 October 23:13 UTC despite its 20 October filename. It records baseline 100,000 USDP and no orders yet, not a first trade. Its SHA256 is `f2e25dbb6ff0150ae202189147568e53936f3f126a1f6c2c25c8919d141584d1`. Initial unavailable email delivery was explicitly waived. Private ledger exports preserve 35 boot/pause/resume/operator-request events; these are not 35 human interventions. Raw account logs and approval files are excluded. This attribution does not replace independent access to the private evidence and does not certify complete intervention history or delivery confirmation.

## Later observation — deliberately retained

Historical private snapshot SHA256: `b9272730c96183a16543ff8c961af374d8b3aef75c8533b990d25151fe4b0444`.

Later private snapshot SHA256: `d30fd57d11e4b47e43ae0fec219d1770bbaa32fc130dea1e40bd26014add1fc8`.

At 6 November 10:59:52.982 UTC, observed equity was **99,999.762629 USDP**, net **−0.237371 USDP**, account return **−0.000237371%**, and fixed-budget normalized PnL **−0.949484%**. The earlier short was closed and a long opened; the historical +6.488352% budget ratio is not the later long's return. Report preparation and historical observation times are distinct. Final official rank/result is unavailable.

## Validation and audit boundaries

See code `validation-results.json` and support `delivery-checks.json` for actual checks. No new research, production changes, remote PR merges, email or publication. Existing all-reachable-Git-history credential/privacy review and compressed-object follow-up were inspected; its bounded conclusion was no confirmed credential leak, not proof that all history is safe. This final delivery uses a restricted source allowlist and scans every delivered source/support member. No Git history, runtime database, raw account logs, private snapshot, local .env, signer or approval is shipped. Necessary report contact emails remain intentionally included. Pattern checks cannot establish absence of every possible secret format or certify application security.
