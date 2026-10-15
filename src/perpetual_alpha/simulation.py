"""Single simulation executor with durable idempotency; never imports a signing client."""
import json
from decimal import Decimal

from .context import validate_account
from .interfaces import Decision, ExecutionItem, ExecutionReport, decimal_string, timestamp
from .journal import canonical, report_from_dict


class MockExecution:
    def __init__(self, journal, risk_gate, market_provider, risk_provider, clock, selected_producer):
        self.journal, self.gate = journal, risk_gate
        self.market_provider, self.risk_provider, self.clock = market_provider, risk_provider, clock
        if not isinstance(selected_producer, tuple) or len(selected_producer) != 2:
            raise ValueError("An explicit controlling producer id/version is required")
        from .interfaces import identity
        identity(*selected_producer)
        self.selected_producer = selected_producer

    def initialize_account(self, account):
        validate_account(account, self.clock(), self.gate.max_age_ms)
        with self.journal.connection:
            self.journal.connection.execute("INSERT OR IGNORE INTO simulation_state VALUES(1,?)",
                                            (canonical(account),))

    def account(self):
        row = self.journal.connection.execute("SELECT account FROM simulation_state WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def _report(self, decision, status, code, now, account, initial=None):
        connection = self.journal.connection
        # Conflicting retries get distinct reports without replacing the original execution state.
        row = connection.execute("SELECT sequence FROM report_sequences WHERE decision_id=?",
                                 (decision.decision_id,)).fetchone()
        if row is None:
            original = connection.execute("SELECT report FROM decisions WHERE decision_id=?",
                                          (decision.decision_id,)).fetchone()
            sequence = json.loads(original[0])["sequence"] + 1 if original else 1
        else:
            sequence = row[0] + 1
        connection.execute("INSERT INTO report_sequences VALUES(?,?) ON CONFLICT(decision_id) "
                           "DO UPDATE SET sequence=excluded.sequence", (decision.decision_id, sequence))
        positions = {p["symbol"]: decimal_string(p["quantity"]) for p in (account or {}).get("positions", [])}
        initial = positions if initial is None else initial
        items = tuple(ExecutionItem(t.symbol, t.quantity,
                                    None if status == "unknown" or account is None else positions.get(t.symbol, Decimal(0)),
                                    positions.get(t.symbol, Decimal(0)) - initial.get(t.symbol, Decimal(0)), (),
                                    "SIMULATED unknown" if status == "unknown" else None) for t in decision.targets)
        return ExecutionReport(decision.decision_id + ":" + str(sequence), decision.decision_id, sequence,
                               now, status, code, items, "SIMULATED ONLY: no orders or exchange fills")

    def apply(self, decision, *, simulate_unknown=False):
        decision = Decision.from_dict(decision.to_dict())
        now = self.clock()
        timestamp(now)
        connection = self.journal.connection
        # One immediate transaction serializes simulator writers and makes positions/reports atomic.
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute("SELECT payload,report FROM decisions WHERE decision_id=?",
                                     (decision.decision_id,)).fetchone()
            if row:
                if row[0] == canonical(decision.to_dict()):
                    connection.commit()
                    return report_from_dict(json.loads(row[1]))
                report = self._report(decision, "rejected", "ID_CONFLICT", now, self.account())
                self.journal.record("id-conflict", report.to_dict(), now)
                connection.commit()
                return report
            pending = [Decision.from_dict(json.loads(payload)) for payload, report in connection.execute(
                "SELECT payload,report FROM decisions")
                if json.loads(report)["status"] in {"accepted", "running", "partial", "unknown"}]
            account = self.account()
            if (decision.strategy_id, decision.strategy_version) != self.selected_producer:
                report = self._report(decision, "rejected", "INVALID_INPUT", now, account)
            elif any({t.symbol for t in d.targets} & {t.symbol for t in decision.targets} for d in pending):
                report = self._report(decision, "rejected", "BUSY", now, account)
            else:
                market, risk = self.market_provider(), self.risk_provider()
                verdict = self.gate.check(decision, account, market, risk, now)
                self.journal.record("risk-verdict", verdict.to_dict(), now)
                if verdict.status != "approved":
                    report = self._report(decision, "rejected", verdict.reason_code, now, account)
                else:
                    accepted = self._report(decision, "accepted", "OK", now, account)
                    self.journal.record("execution-report", accepted.to_dict(), now)
                    if simulate_unknown and decision.targets:
                        report = self._report(decision, "unknown", "NETWORK_UNKNOWN", now, account)
                    else:
                        initial = {p["symbol"]: decimal_string(p["quantity"]) for p in account["positions"]}
                        positions = {p["symbol"]: p for p in account["positions"]}
                        for target in decision.targets:
                            positions[target.symbol] = {"symbol": target.symbol, "quantity": str(target.quantity),
                                                        "mark_price": market[target.symbol]["mark"]}
                        account = {**account, "observed_ms": now, "positions": list(positions.values())}
                        connection.execute("UPDATE simulation_state SET account=? WHERE id=1", (canonical(account),))
                        report = self._report(decision, "completed", "OK", now, account, initial)
            connection.execute("INSERT INTO decisions VALUES(?,?,?)",
                               (decision.decision_id, canonical(decision.to_dict()), canonical(report.to_dict())))
            self.journal.record("decision", decision.to_dict(), now)
            self.journal.record("execution-report", report.to_dict(), now)
            connection.commit()
            return report
        except Exception:
            connection.rollback()
            raise

    def reconcile(self, decision_id):
        row = self.journal.connection.execute("SELECT report FROM decisions WHERE decision_id=?",
                                             (decision_id,)).fetchone()
        if row is None:
            raise KeyError(decision_id)
        # Unknown remains unknown: this mock does not invent external confirmation or resubmit.
        return report_from_dict(json.loads(row[0]))
