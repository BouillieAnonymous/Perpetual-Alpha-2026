"""Synthetic bounded Paper config; no real account data or production risk settings."""
from pathlib import Path

from portfolio_support import fixture

from perpetual_alpha.contracts import Forecast, ForecastBatch
from perpetual_alpha.runtime.contracts import AnalysisJob, JobLimits
from perpetual_alpha.runtime.mock_config import load_config
from perpetual_alpha.runtime.mock_fixture import context_for, initial_account
from perpetual_alpha.transport.messages import ClaimRequest, ForecastSubmission


def configuration():
    f = fixture()
    policy, state = f["policy"], f["state"]
    policy.update(asset_budgets_usd={"par:BTC": "16", "par:ETH": "3"}, horizon_ms=3600000,
                  max_age_ms=90000, decision_ttl_ms=30000)
    state["control"].update(producer_id="dummy-agent", producer_version="offline-0.1", symbols=["par:BTC", "par:ETH"])
    state["risk"].update(return_interval_ms=3600000, estimation_window_ms=10800000)
    state["risk"].update(method="synthetic-paper-covariance", model_version="paper-risk-fixture/0.1")
    policy["risk_model_version"] = "paper-risk-fixture/0.1"
    state["account"].update(equity_usd="1000", available_margin_usd="1000")
    state.update(peak_equity_usd="1000", session_start_equity_usd="1000")
    state["market"]["par:BTC"].update(usd_per_unit="80000", quantity_step="0.0001")
    state["market"]["par:ETH"].update(usd_per_unit="3000", quantity_step="0.001")
    return {"configuration_id": "SYNTHETIC-PAPER-0.1", "model_version": "paper-fixed-liquidity/0.1",
            "scope": "mock-account", "policy": policy, "initial_state": state,
            "execution": {"fee_bps": "10", "slippage_bps": "10", "max_fill_quantities": {"par:BTC": "0.0001", "par:ETH": "0.001"},
                          "min_notional_usd": {"par:BTC": "0", "par:ETH": "0"}, "order_timeout_ms": 30000}}


def enqueue(queue, principal, now, *, name="one", symbols=("par:BTC",), direction="up", scope="mock-account"):
    job = AnalysisJob("paper-job-"+name, "paper-context-"+name, principal.producer_id, principal.producer_version,
                      scope, 1, "control", symbols, now, now, now+120000, "queued", 0, 0,
                      JobLimits(10, 100, 262144, 2, 90000, 20000))
    sim = load_config(Path(__file__).resolve().parents[1]/"config/mock-http.toml")["simulation"]
    ctx, reqs = context_for(sim, job.context_id, now, initial_account(sim, now))
    ctx["account"] = None
    queue.enqueue(job, ctx, reqs)
    claim = queue.claim(principal, ClaimRequest("claim-"+name), lease_ms=30000)
    batch = ForecastBatch("batch-"+name, job.context_id, principal.producer_id, principal.producer_version, now,
                          tuple(Forecast(s, now, now+90000, 3600000, "ready", direction, ("fixture-quotes",)) for s in symbols))
    queue.submit(principal, ForecastSubmission("submit-"+name, claim.lease.ref, batch))
    return job
