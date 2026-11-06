# PR Disposition Report

The final Integration PR is the only proposed merge to main. Source research
PRs remain open until final human review, so no work is marked merged before it
actually reaches main. No remote branches are deleted.

| PRs | Consolidation action | PR state decision |
|---|---|---|
| #36 → #39 → #41 → #43 | Actual dependency-ordered merges at audited heads, frozen blobs preserved | Retain until final Integration PR review/merge; then close as superseded with final commit reference |
| #38, #27 | Canonical research archives integrated; cross-PR inputs documented | Retain until final review; do not claim every full rerun is portable |
| #13 → #17 | Basis source/tests/pins relocated under research/legacy, docs/results preserved | Retain until selective integration accepted; original collector configuration excluded |
| #15 → #20 | Quant/regime snapshots and complete results relocated/preserved | Retain until selective integration accepted; original CI/deployment changes excluded |
| #16 | Prototype source/tests preserved under research/experimental/websocket | Keep Draft as unadopted collector; no proof it replaces current collector |
| #19 | Canonical proposal preserved, maturity explicitly proposal-only | Retain pending final archive acceptance; no deployed Agent claim |
| #29 | Public historical research preserved; scraper not run | Retain pending final archive acceptance; no ongoing scraping task |
| #31 | External fork ZIP/runtime/configuration not imported or executed | Keep open for owner/manual source review; insufficient evidence to merge or declare obsolete |
| #21 | Unchanged head is an ancestor of baseline main | CLOSED with ancestry explanation; branch/discussion preserved |

#21 was rechecked and closed on 2026-11-06. Its head is
`99736bf1a9685b7d5846c64d0decba583cdf7caf`; baseline main is
`0c579d77282e7adffc8bd55b2b08e5f6cabc702a`. No old Funding code was copied.

All reviewed source heads remain unchanged from the prior audit. Exact merge
commits and relocated files are in [source-lock.json](source-lock.json).
The GitHub API initially returned15 open PRs including #31; closing #21 leaves14
source PRs open before creating the new final Integration PR.
