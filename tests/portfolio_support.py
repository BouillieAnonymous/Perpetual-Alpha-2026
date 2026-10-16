"""Every valuation, equity and limit here is fabricated solely for isolated simulation."""
import json
from pathlib import Path

from perpetual_alpha.contracts import Forecast, ForecastBatch


def fixture():
    root = Path(__file__).resolve().parents[1]
    risk = json.loads((root / "docs/examples/market-risk-replay.synthetic.json").read_text(encoding="utf-8"))["result"]["risk_snapshot"]
    now = risk["asof_ms"]
    policy = {"version": "SYNTHETIC-ONLY-1", "mode": "synthetic-only", "asset_budgets_usd": {"par:BTC": "50"},
              "asset_caps_usd": {"par:BTC": "100", "par:ETH": "100"}, "gross_cap_usd": "100", "net_cap_usd": "100",
              "sigma_cap_usd": "10", "leverage_cap": "1", "margin_fraction": "1", "margin_buffer_usd": "10",
              "drawdown_cap": "0.2", "session_loss_cap_usd": "20", "horizon_ms": 60000, "decision_ttl_ms": 10000,
              "max_age_ms": 60000, "min_samples": 3, "risk_model_version": risk["model_version"],
              "allow_reversals": False, "allow_explicit_exits": False}
    state = {"schema_version": "portfolio-simulation/0.1", "synthetic": True, "context_id": "synthetic-portfolio",
             "asof_ms": now, "account": {"observed_ms": now, "status": "ready", "equity_usd": "100",
                                         "available_margin_usd": "100", "positions": [], "open_orders": []},
             "assertions": {"risk_ready": True, "global_account_complete": True, "other_exposure_known": True,
                            "inflight_orders_known": True}, "peak_equity_usd": "100", "session_start_equity_usd": "100",
             "market": {s: {"usd_per_unit": "10", "quantity_step": "0.1", "observed_ms": now, "event_ms": now,
                            "valuation_source": "synthetic-fixture"} for s in risk["symbols"]}, "risk": risk,
             "control": {"producer_id": "synthetic-agent", "producer_version": "1", "epoch": 1, "role": "control",
                         "symbols": ["par:BTC"], "accepted_asof_ms": now, "consumed_asof_ms": None},
             "health": {"market_data": True, "account_data": True, "execution": True}}
    batch = ForecastBatch("synthetic-batch", state["context_id"], "synthetic-agent", "1", now,
                          (Forecast("par:BTC", now, now+20000, 60000, "ready", "up"),))
    return {"policy": policy, "state": state, "forecast": batch.to_dict(), "now_ms": now, "decision_id": "synthetic-decision"}
