"""Bounded local synthetic harness through existing MockExecution. No live backends or HTTP entry points."""
import argparse
import copy
import json
from pathlib import Path

from .contracts import ForecastBatch, RiskSnapshot, RiskVerdict
from .interfaces import Decision
from .journal import Journal, digest
from .portfolio_policies import BudgetPortfolio
from .risk_gate import SimulationRiskGate
from .risk_policy import SimulationPolicy
from .runtime.supervisor import RiskSupervisor
from .simulation import MockExecution


class MockGateBridge:
    """Recheck actual local Mock account and providers at apply, preserving Decision/Report 1.0."""
    def __init__(self, gate, proposal, state):
        self.gate, self.proposal, self.state = gate, copy.deepcopy(proposal), copy.deepcopy(state)
        self.max_age_ms = SimulationPolicy.from_dict(gate.policy).max_age_ms

    def market(self):
        return {s: {"mark": q["usd_per_unit"]} for s, q in self.state["market"].items()}

    def check(self, decision, account, market, risk, now):
        state, proposal = copy.deepcopy(self.state), copy.deepcopy(self.proposal)
        state["account"], state["risk"] = account, risk.to_dict()
        if market != self.market():
            state["health"]["market_data"] = False
        proposal["decision"] = decision.to_dict()
        return RiskVerdict.from_dict(self.gate.check(proposal, state, now)["verdict"])


def run(fixture, journal_path, *, resume_supervisor=False):
    if not isinstance(fixture, dict) or set(fixture) != {"policy", "state", "forecast", "now_ms", "decision_id"}:
        raise ValueError("Explicit synthetic harness fixture required")
    policy = SimulationPolicy.from_dict(fixture["policy"])
    state, now = copy.deepcopy(fixture["state"]), fixture["now_ms"]
    supervisor = RiskSupervisor(policy)
    before = supervisor.resume(state, now) if resume_supervisor else supervisor.inspect(state, now)
    proposal = BudgetPortfolio(policy).construct(ForecastBatch.from_dict(fixture["forecast"]), state, now, fixture["decision_id"])
    gate = SimulationRiskGate(policy, supervisor)
    approval = gate.check(proposal, state, now)
    path = Path(journal_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.open("x").close()  # Isolated new journal only; never attach to an existing control journal.
    journal = Journal(path)
    try:
        bridge = MockGateBridge(gate, proposal, state)
        execution = MockExecution(journal, bridge, bridge.market, lambda: RiskSnapshot.from_dict(state["risk"]),
                                  lambda: now, (state["control"]["producer_id"], state["control"]["producer_version"]))
        execution.initialize_account(state["account"])
        with journal.connection:
            journal.record("simulation-input", fixture, now)
            journal.record("simulation-proposal", proposal, now)
            journal.record("simulation-supervision", before, now)
        report = execution.apply(Decision.from_dict(proposal["decision"]))
        after = copy.deepcopy(state)
        after["account"] = execution.account()
        after_report = supervisor.inspect(after, now)
        return {"synthetic": True, "input_digest": digest(fixture), "proposal": proposal, "approval": approval,
                "execution_report": report.to_dict(), "supervision_after": after_report, "real_execution_authorized": False}
    finally:
        journal.connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--resume-supervisor", action="store_true", help="Explicit local simulation resume only")
    args = parser.parse_args(argv)
    with Path(args.fixture).open("rb") as stream:
        raw = stream.read(1024*1024+1)
    if len(raw) > 1024*1024:
        raise ValueError("Synthetic fixture budget exceeded")
    result = run(json.loads(raw), args.journal, resume_supervisor=args.resume_supervisor)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["execution_report"]["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
