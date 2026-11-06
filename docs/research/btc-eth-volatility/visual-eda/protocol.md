# Visual EDA protocol — fixed before event calculation

2026-11-04. Offline descriptive visualization only. Input: Stage 1 frozen PAS BTC/ETH export and causal minute panel, original SHA256 required. Reuse Stage 2A A/B return and complete-window RV functions; independently reconstruct arrivals and compare panel. No collection, models, statistical tests, parameter optimization or trading.

A retains valid fresh-event zero returns. B additionally requires changed bid/ask and 30–90 second event spacing. No interpolation, gap filling, partial-window RV or annualization. Prices normalized at first jointly valid grid point, gaps remain gaps; this is a level ratio, not a continuous investable return series.

Shock threshold: lower 5% and upper 95% quantiles (nearest rank by round(q*(n-1))) of signed A BTC minute returns whose support lies inside development [2026-10-20,2026-10-27) UTC. Apply once to all historical valid BTC A returns, split positive/negative; do not require ETH future availability for event inclusion. Outcomes: sum of ETH A signed log returns t+1 through t+h, h=5/15/30, all h required. Missing and tail-truncated outcomes counted, never zero-filled. Mark overlapping (t,t+30] outcome windows in either shock direction; report connected clusters, not independent sample counts. Show every event and median/IQR, no significance or profitability claim. This entire history is already observed.

Every figure includes source, sample, UTC as-of semantics and missing-data rules. Spread is quoted full spread bps; one spread is an illustrative unchanged-mid round-trip crossing cost, excludes fees, slippage, depth and latency. No executable profit estimate.
