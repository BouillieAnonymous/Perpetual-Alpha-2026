"""Frozen plans + existing MockExecution; no signing client import or live backend selection."""
import json
from dataclasses import replace
from decimal import Decimal

from ..forecasting import MockRiskEstimator
from ..interfaces import Decision
from ..journal import canonical, digest, report_from_dict
from ..portfolio import PortfolioUnavailable
from ..simulation import MockExecution
from .contracts import ProcessingRecord, transition_processing
from .ingress import IngressRejected
from .jobs import JobQueue, reject
from .mock_config import gate, policy
from .outbox import Outbox


class MockConsumer:
    def __init__(self, store, journal, config):
        self.store, self.journal, self.config = store, journal, config
        self.outbox = Outbox(store)
        with store.transaction() as (c, now):
            if c.execute("SELECT 1 FROM paper_configuration LIMIT 1").fetchone():
                raise ValueError("Paper and legacy Mock modes require separate runtime databases")
            saved = c.execute("SELECT payload_digest FROM mock_configurations WHERE configuration_id=?",
                              (config["configuration_id"],)).fetchone()
            if saved and saved[0] != digest(config):
                raise ValueError("Configuration ID is immutable; use an explicit new configuration version")
            c.execute("INSERT OR IGNORE INTO mock_configurations VALUES(?,?,?)",
                      (config["configuration_id"], digest(config), canonical(config)))

    def _recorded(self, decision):
        row = self.journal.connection.execute("SELECT payload,report FROM decisions WHERE decision_id=?",
                                               (decision.decision_id,)).fetchone()
        if row is None:
            return None
        if row[0] != canonical(decision.to_dict()):
            raise ValueError("Stored Mock Decision identity conflict; stop for inspection")
        return report_from_dict(json.loads(row[1]))

    def reconcile(self):
        """Read authoritative LOCAL mock reports before expiry filtering; never reapply a recorded plan."""
        with self.store.connection() as c:
            rows = c.execute("SELECT m.processing_id,m.decision FROM mock_plans m JOIN processing p "
                             "USING(processing_id) WHERE p.status IN ('pending','running')").fetchall()
        for row in rows:
            decision = Decision.from_dict(json.loads(row["decision"]))
            report = self._recorded(decision)
            if report is None:
                continue
            verdicts = [e["payload"] for e in self.journal.events() if e["kind"] == "risk-verdict"
                        and e["payload"]["decision_id"] == decision.decision_id]
            with self.store.transaction() as (c, now):
                record = ProcessingRecord.from_dict(json.loads(c.execute(
                    "SELECT record FROM processing WHERE processing_id=?", (row["processing_id"],)).fetchone()[0]))
                if record.status not in {"pending", "running"}:
                    continue
                if record.status == "pending":
                    record = transition_processing(record, "start", now)
                # Even an unknown report is a recorded result. Do not release it for new automatic work.
                event = "complete" if report.status in {"completed", "rejected", "failed"} else "block"
                record = transition_processing(record, event, now, reason_code="RECOVERED_MOCK" if event == "complete"
                                                else "NETWORK_UNKNOWN")
                record = replace(record, decision_id=decision.decision_id, report_id=report.report_id,
                                 verdict_id=verdicts[-1]["verdict_id"] if verdicts else None)
                epoch = c.execute("SELECT delivery_epoch FROM outbox WHERE processing_id=?",
                                  (record.processing_id,)).fetchone()[0]
                self.outbox._write(c, record, "done", epoch + 1)
                if event == "complete":
                    job = JobQueue._job(c, record.job_id)[1]
                    c.execute("UPDATE controls SET consumed_asof_ms=max(coalesce(consumed_asof_ms,0),?) WHERE scope=?",
                              (job.asof_ms, job.control_scope))
                self.store.event(c, now, "mock-reconciled", {"processing_id": record.processing_id,
                                                            "report_id": report.report_id})

    def _plan(self, delivery):
        with self.store.transaction() as (c, now):
            stored = c.execute("SELECT m.*,f.payload FROM mock_plans m JOIN mock_configurations f "
                               "USING(configuration_id) WHERE processing_id=?",
                               (delivery.processing.processing_id,)).fetchone()
            if stored:
                return Decision.from_dict(json.loads(stored["decision"])), json.loads(stored["inputs"]), json.loads(stored["payload"])
            job = JobQueue._job(c, delivery.processing.job_id)[1]
            invalid = self.outbox._invalid(c, job, delivery.batch, now)
            if invalid:
                reject("STALE_RESULT", "Delivery became invalid before plan creation")
            config = self.config
            context = delivery.manifest["context"]
            risk = MockRiskEstimator(tuple(config["quantities"]), config["horizon_ms"]).estimate(context)
            identity = "decision:" + digest({"processing_id": delivery.processing.processing_id})
            decision = policy(config).construct(delivery.batch, risk, context, now, identity)
            inputs = {"context": context, "risk": risk.to_dict()}
            c.execute("INSERT INTO mock_plans VALUES(?,?,?,?,?)", (delivery.processing.processing_id,
                      config["configuration_id"], decision.decision_id, canonical(decision.to_dict()), canonical(inputs)))
            self.store.event(c, now, "mock-plan-frozen", {"processing_id": delivery.processing.processing_id,
                                                        "decision_id": identity})
            return decision, inputs, config

    def step(self):
        self.reconcile()
        if self.unresolved():
            return False
        delivery = self.outbox.begin()
        if delivery is None:
            return False
        try:
            decision, inputs, config = self._plan(delivery)
        except (IngressRejected, PortfolioUnavailable) as error:
            self.outbox.finish(delivery.processing.processing_id, delivery.delivery_epoch, "block",
                               reason_code=getattr(error, "code", "DATA_UNAVAILABLE"))
            return True
        from ..contracts import RiskSnapshot
        risk = RiskSnapshot.from_dict(inputs["risk"])
        # Hold the runtime write guard only over LOCAL Mock I/O. No HTTP/SDK calls in this section.
        with self.store.transaction() as (c, now):
            row = c.execute("SELECT status,delivery_epoch FROM outbox WHERE processing_id=?",
                            (delivery.processing.processing_id,)).fetchone()
            if row["status"] != "running" or row["delivery_epoch"] != delivery.delivery_epoch:
                reject("LEASE_LOST", "Consumer reservation changed")
            job = JobQueue._job(c, delivery.processing.job_id)[1]
            invalid = self.outbox._invalid(c, job, delivery.batch, now)
            if invalid or decision.expires_ms <= now:
                # End the guard without applying; finish below persists the blocked state.
                blocked = invalid or "EXPIRED"
            else:
                blocked = None
                market = {s: {"mark": price, "bid": str(Decimal(price) - 1),
                              "ask": str(Decimal(price) + 1), "observed_ms": now, "event_ms": now}
                          for s, price in config["fixture_prices"].items()}
                # Only the synthetic state has no outside actor. Refresh its observation, not its holdings.
                account = self.journal.connection.execute("SELECT account FROM simulation_state WHERE id=1").fetchone()
                if account:
                    fresh = json.loads(account[0])
                    fresh["observed_ms"] = now
                    with self.journal.connection:
                        self.journal.connection.execute("UPDATE simulation_state SET account=? WHERE id=1", (canonical(fresh),))
                executor = MockExecution(self.journal, gate(config), lambda: market, lambda: risk,
                                         self.store.clock, (job.producer_id, job.producer_version))
                executor.apply(decision)
        if blocked:
            self.outbox.finish(delivery.processing.processing_id, delivery.delivery_epoch, "block",
                               reason_code=blocked, decision_id=decision.decision_id)
        else:
            self.reconcile()
        return True

    def unresolved(self):
        return any(json.loads(row[0])["status"] not in {"completed", "rejected", "failed"}
                   for row in self.journal.connection.execute("SELECT report FROM decisions"))
