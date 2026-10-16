"""Synthetic fixed-capital sizing; retain omitted holdings, reduce proposals before independent review."""
import copy
from decimal import ROUND_DOWN, Decimal

from .contracts import ForecastBatch
from .interfaces import Decision, TargetPosition, identity
from .journal import digest
from .portfolio import PortfolioUnavailable
from .risk_policy import SimulationPolicy, amount, binding, exposure_metrics, stable_decimal, validate_state


class BudgetPortfolio:
    def __init__(self, policy):
        self.policy = copy.deepcopy(policy.to_dict())

    @stable_decimal
    def construct(self, batch, state, now, decision_id, *, analysis_asof_ms=None):
        policy = SimulationPolicy.from_dict(self.policy)
        state = copy.deepcopy(state)
        positions, _ = validate_state(state, policy, now)
        batch = ForecastBatch.from_dict(batch.to_dict())
        control = state["control"]
        if (batch.context_id != state["context_id"] or (batch.producer_id, batch.producer_version) !=
                (control["producer_id"], control["producer_version"])):
            raise PortfolioUnavailable("Forecast/control identity mismatch")
        asof = state["asof_ms"] if analysis_asof_ms is None else analysis_asof_ms
        from .interfaces import timestamp
        timestamp(asof)
        if not 0 <= state["asof_ms"]-asof <= policy.max_age_ms or not asof <= batch.created_ms <= now:
            raise PortfolioUnavailable("Forecast batch time invalid")
        if asof < control["accepted_asof_ms"] or (control["consumed_asof_ms"] is not None and asof <= control["consumed_asof_ms"]):
            raise PortfolioUnavailable("Forecast behind control watermark")
        desired, expiries = {}, [now+policy.decision_ttl_ms]
        for f in batch.items:
            if (f.symbol not in control["symbols"] or f.asof_ms != asof or f.expires_ms <= now
                    or f.horizon_ms != policy.horizon_ms or f.status == "error"):
                raise PortfolioUnavailable("Unavailable, expired, unauthorized or mixed-horizon Forecast")
            expiries.append(f.expires_ms)
            if f.status != "ready" or f.direction == "neutral":
                continue
            q = positions.get(f.symbol, Decimal(0))
            step = amount(state["market"][f.symbol]["quantity_step"], positive=True)
            if q % step:
                raise PortfolioUnavailable("Existing dust requires an explicit policy")
            units = amount(policy.asset_budgets_usd[f.symbol]) / amount(state["market"][f.symbol]["usd_per_unit"], positive=True)
            target = (units/step).to_integral_value(rounding=ROUND_DOWN)*step*(1 if f.direction == "up" else -1)
            if q*target < 0 and not policy.allow_reversals:
                raise PortfolioUnavailable("Reversal policy is disabled")
            # Sub-step budget does not implicitly flatten an existing position.
            if target != 0:
                desired[f.symbol] = target
        baseline = exposure_metrics(state, policy, now, {})
        if baseline["breaches"]:
            raise PortfolioUnavailable("Existing risk requires supervision; no automatic exit policy")

        def candidate(scale):
            result = {}
            for s, target in desired.items():
                q = positions.get(s, Decimal(0))
                step = amount(state["market"][s]["quantity_step"], positive=True)
                sized = ((q+(target-q)*scale)/step).to_integral_value(rounding=ROUND_DOWN)*step
                if sized == 0 and q != 0 and not policy.allow_explicit_exits:
                    sized = q
                if sized != q:
                    result[s] = sized
            return result

        selected, scale = candidate(Decimal(1)), Decimal(1)
        if exposure_metrics(state, policy, now, selected)["breaches"]:
            low, high = Decimal(0), Decimal(1)
            selected = {}
            for _ in range(32):
                middle = (low+high)/2
                current = candidate(middle)
                if exposure_metrics(state, policy, now, current)["breaches"]:
                    high = middle
                else:
                    low, selected = middle, current
            scale = low
        identity(decision_id)
        decision = Decision(decision_id, state["context_id"], batch.producer_id, batch.producer_version,
                            now, min(expiries), tuple(TargetPosition(s, q) for s, q in sorted(selected.items())),
                            "SYNTHETIC fixed-capital sizing; policy="+policy.version)
        return {"schema_version": "simulation-proposal/0.1", "decision": decision.to_dict(),
                "forecast_asof_ms": asof, "forecast": batch.to_dict(), "forecast_digest": digest(batch.to_dict()),
                "control_epoch": control["epoch"],
                **binding(state, policy), "sizing": {"scale": str(scale), "desired_targets": {s: str(q) for s, q in desired.items()},
                                                     "metrics": exposure_metrics(state, policy, now, selected)}}
