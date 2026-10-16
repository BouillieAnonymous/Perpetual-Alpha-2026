"""Local synthetic supervision independent of Agent. Start paused; never emit exit/cancel orders."""
import copy

from ..interfaces import timestamp
from ..risk_policy import SimulationPolicy, binding, exposure_metrics, stable_decimal


class RiskSupervisor:
    def __init__(self, policy):
        self.policy = copy.deepcopy(policy.to_dict())
        self.paused = True
        self.reasons = ["STARTUP_REQUIRES_EXPLICIT_RESUME"]

    @stable_decimal
    def inspect(self, state, now):
        timestamp(now)
        policy = SimulationPolicy.from_dict(self.policy)
        metrics = None
        try:
            metrics = exposure_metrics(state, policy, now, {})
            failures = metrics["breaches"]
        except (ValueError, TypeError, KeyError, ArithmeticError):
            failures = ["STATE_UNAVAILABLE"]
        if failures:
            self.paused = True
            self.reasons = sorted(set(self.reasons+failures))
        return {"schema_version": "simulation-supervision/0.1", "checked_ms": now, "paused": self.paused,
                "reasons": list(self.reasons), "current_failures": list(failures), "metrics": metrics,
                **binding(state, policy), "automatic_actions": [], "execution_authorized": False}

    def resume(self, state, now):
        """Explicit local simulation command, not an automatic recovery or live permission."""
        report = self.inspect(state, now)
        if report["current_failures"]:
            raise ValueError("Cannot resume while unhealthy or above explicit synthetic limits")
        self.paused, self.reasons = False, []
        return self.inspect(state, now)
