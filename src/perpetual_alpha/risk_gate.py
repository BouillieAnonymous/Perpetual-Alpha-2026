"""Independent synthetic gate: recompute whole-account bounds, never resize a Decision."""
import copy
from decimal import Decimal

from .contracts import ForecastBatch, RiskVerdict
from .interfaces import Decision, timestamp
from .journal import digest
from .risk_policy import SimulationPolicy, amount, binding, exposure_metrics, stable_decimal, validate_state


class SimulationRiskGate:
    def __init__(self, policy, supervisor):
        self.policy = copy.deepcopy(policy.to_dict())
        self.supervisor = supervisor

    @stable_decimal
    def check(self, proposal, state, now):
        timestamp(now)
        proposal, state = copy.deepcopy(proposal), copy.deepcopy(state)
        policy = SimulationPolicy.from_dict(self.policy)
        decision = Decision.from_dict(proposal["decision"])
        metrics = None

        def verdict(status, code, message):
            record = RiskVerdict(decision.decision_id+":gate:"+str(now), decision.decision_id,
                                 digest(decision.to_dict()), policy.version, now, status, code,
                                 "account:"+digest(state.get("account")), "market:"+digest(state.get("market")),
                                 state.get("risk", {}).get("snapshot_id", "risk-unavailable"), (message,), simulated=True)
            return {"verdict": record.to_dict(), "metrics": metrics, **binding(state, policy), "execution_authorized": False}

        if decision.created_ms > now:
            return verdict("rejected", "FUTURE_SIGNAL", "Future decision")
        if decision.expires_ms <= now:
            return verdict("rejected", "EXPIRED", "Expired decision")
        try:
            fields = {"schema_version", "decision", "forecast", "forecast_asof_ms", "forecast_digest", "control_epoch",
                      "state_digest", "policy_digest", "sizing"}
            if set(proposal) != fields or proposal["schema_version"] != "simulation-proposal/0.1":
                raise ValueError("Invalid proposal envelope")
            positions, _ = validate_state(state, policy, now)
            control = state["control"]
            batch = ForecastBatch.from_dict(proposal["forecast"])
            if (digest(batch.to_dict()) != proposal["forecast_digest"] or batch.context_id != state["context_id"]
                    or (batch.producer_id, batch.producer_version) != (control["producer_id"], control["producer_version"])
                    or not state["asof_ms"] <= batch.created_ms <= decision.created_ms):
                raise ValueError("Forecast identity/digest/time invalid")
            usable = {}
            for f in batch.items:
                if (f.symbol not in control["symbols"] or f.asof_ms != state["asof_ms"]
                        or f.horizon_ms != policy.horizon_ms or f.status == "error"
                        or f.expires_ms <= now or decision.expires_ms > f.expires_ms):
                    raise ValueError("Forecast expired, unauthorized or unavailable")
                if f.status == "ready" and f.direction in {"up", "down"}:
                    usable[f.symbol] = f.direction
            asof = proposal["forecast_asof_ms"]
            timestamp(asof)
            timestamp(proposal["control_epoch"])
            if (asof != state["asof_ms"] or asof < control["accepted_asof_ms"]
                    or (control["consumed_asof_ms"] is not None and asof <= control["consumed_asof_ms"])):
                raise ValueError("Forecast behind accepted/consumed watermark")
            if (proposal["control_epoch"] != control["epoch"] or decision.context_id != state["context_id"]
                    or (decision.strategy_id, decision.strategy_version) != (control["producer_id"], control["producer_version"])):
                raise ValueError("Control identity/epoch changed")
            if any(proposal[k] != v for k, v in binding(state, policy).items()):
                raise ValueError("State or policy changed; replan required")
            if decision.expires_ms > decision.created_ms+policy.decision_ttl_ms or decision.created_ms < asof:
                raise ValueError("Invalid decision lifetime")
            targets = {t.symbol: t.quantity for t in decision.targets}
            if not targets.keys() <= usable.keys():
                raise ValueError("Targets require directional Forecasts; no implicit exits")
            for s, q in targets.items():
                old = positions.get(s, Decimal(0))
                reducing = old*q >= 0 and abs(q) <= abs(old)
                if q != 0 and ((q > 0) != (usable[s] == "up")) and not reducing:
                    raise ValueError("Target direction unsupported by Forecast")
                if not reducing and abs(q)*amount(state["market"][s]["usd_per_unit"]) > amount(policy.asset_budgets_usd[s]):
                    return verdict("rejected", "RISK_LIMIT", "Target exceeds explicit synthetic capital budget")
                if q == 0 and old != 0 and not policy.allow_explicit_exits:
                    raise ValueError("Explicit exit policy disabled")
                if old*q < 0 and not policy.allow_reversals:
                    raise ValueError("Reversal policy disabled")
                if q % amount(state["market"][s]["quantity_step"], positive=True):
                    raise ValueError("Target does not conform to latest step")
            # Deliberately ignore Portfolio sizing metrics and recompute from immutable targets.
            metrics = exposure_metrics(state, policy, now, targets)
            supervision = self.supervisor.inspect(state, now)
            if supervision["policy_digest"] != digest(policy.to_dict()) or supervision["paused"]:
                return verdict("deferred", "BUSY", "Supervisor paused or policy mismatch")
            if metrics["breaches"]:
                return verdict("rejected", "RISK_LIMIT", "Synthetic bounds exceed policy: "+",".join(metrics["breaches"]))
        except (ValueError, KeyError, TypeError, ArithmeticError):
            return verdict("deferred", "DATA_UNAVAILABLE", "Unverified state, identity, authority, step or binding; replan required")
        return verdict("approved", "OK", "SYNTHETIC ONLY: independent full-exposure limits passed")
