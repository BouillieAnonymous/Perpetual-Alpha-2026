import copy
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from paper_support import configuration, enqueue
from runtime_support import Clock

from perpetual_alpha.journal import canonical
from perpetual_alpha.runtime.ingress import Principal
from perpetual_alpha.runtime.jobs import JobQueue
from perpetual_alpha.runtime.paper_engine import PaperEngine
from perpetual_alpha.runtime.store import APPLICATION_ID, SCHEMA_VERSION, RuntimeStore


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/"runtime.sqlite3"
        self.clock = Clock(1000000)
        self.store = RuntimeStore(self.path, clock=self.clock)
        self.config = configuration()
        self.queue = JobQueue(self.store)
        self.principal = Principal("paper-worker", "dummy-agent", "offline-0.1", "control")
        self.queue.configure_control("mock-account", "dummy-agent", "offline-0.1", 1)
        self.queue.configure_worker(self.principal)
        self.engine = PaperEngine(self.store, self.config)
        self.engine.boot()
        self.resume()

    def resume(self, request="resume-one"):
        s = self.engine.status()
        self.engine.resume(request, s["generation"], s["pause_epoch"], "synthetic-test-operator")

    def submit(self, **kwargs):
        return enqueue(self.queue, self.principal, self.clock.now, **kwargs)

    def count(self, table):
        with self.store.connection() as c:
            return c.execute("SELECT count(*) FROM "+table).fetchone()[0]

    def plan(self):
        with self.store.connection() as c:
            return dict(c.execute("SELECT * FROM paper_plans ORDER BY created_ms DESC LIMIT 1").fetchone())

    def tick(self):
        self.clock.now += 1
        self.engine.publish_fixture()
        return self.engine.step()

    def complete(self):
        for _ in range(20):
            self.tick()
            if self.plan()["status"] in {"completed", "failed", "partial-cancelled"}:
                return self.engine.report(self.plan()["decision_id"])
        self.fail("Paper execution did not settle")

    def test_intent_acceptance_is_not_fill(self):
        job = self.submit()
        self.tick()
        self.assertEqual(self.count("paper_intents"), 1)
        self.assertEqual(self.count("paper_fills"), 0)
        self.assertEqual(self.engine.report(self.plan()["decision_id"]).status, "accepted")
        self.assertEqual(self.queue.feedback(self.principal, job.job_id).processing.status, "running")

    def test_partial_then_exact_completed_and_atomic_feedback(self):
        job = self.submit()
        self.tick()
        self.tick()
        report = self.engine.report(self.plan()["decision_id"])
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.items[0].actual_quantity, Decimal("0.0001"))
        self.assertEqual(self.queue.feedback(self.principal, job.job_id).processing.status, "running")
        self.tick()
        report = self.engine.report(self.plan()["decision_id"])
        self.assertEqual(report.status, "completed")
        self.assertEqual(report.items[0].actual_quantity, report.items[0].target_quantity)
        feedback = self.queue.feedback(self.principal, job.job_id)
        self.assertEqual(feedback.processing.report_id, report.report_id)
        self.assertTrue(feedback.processing.verdict_id.startswith("paper-decision:"))
        self.assertEqual(feedback.processing.status, "completed")
        self.assertEqual(self.count("paper_fills"), 2)
        self.engine.verify_ledger()

    def test_fee_slippage_equity_hand_calculation(self):
        self.submit()
        self.complete()
        status = self.engine.status()
        self.assertEqual(Decimal(status["cash"]), Decimal("999.983984"))
        self.assertEqual(Decimal(status["supervision"]["metrics"]["gross_usd"]), Decimal(16))
        with self.store.transaction() as (c, now):
            state = self.engine._state(c, now)
        self.assertEqual(Decimal(state["account"]["equity_usd"]), Decimal("999.967984"))
        self.engine.verify_ledger()

    def test_repeated_steps_and_duplicate_stage_never_repeat_fills(self):
        self.submit()
        d = self.engine.outbox.begin()
        bad = replace(d, batch=replace(d.batch, items=tuple(replace(f, direction="down") for f in d.batch.items)))
        with self.assertRaises(ValueError):
            self.engine.stage(bad)
        first = self.engine.stage(d)
        self.assertEqual(self.engine.stage(d).to_dict(), first.to_dict())
        self.complete()
        for _ in range(3):
            self.tick()
        self.assertEqual(self.count("paper_plans"), 1)
        self.assertEqual(self.count("paper_intents"), 1)
        self.assertEqual(self.count("paper_fills"), 2)

    def test_restart_preserves_partial_intent_and_requires_new_resume(self):
        self.submit()
        self.tick()
        self.tick()
        old = self.engine.status()
        self.engine = PaperEngine(RuntimeStore(self.path, clock=self.clock), self.config)
        self.engine.boot()
        self.assertTrue(self.engine.status()["paused"])
        self.assertFalse(self.tick())
        self.assertEqual(self.count("paper_fills"), 1)
        with self.assertRaises(ValueError):
            self.engine.resume("stale-boot", old["generation"], old["pause_epoch"], "test")
        self.resume("restart-resume")
        self.complete()
        self.assertEqual(self.count("paper_fills"), 2)

    def test_unknown_never_resends_or_assumes_flat(self):
        self.submit()
        self.tick()
        self.tick()
        decision = self.plan()["decision_id"]
        self.engine.mark_unknown(decision)
        self.assertEqual(self.engine.report(decision).status, "unknown")
        self.assertIsNone(self.engine.report(decision).items[0].actual_quantity)
        self.assertFalse(self.tick())
        self.assertEqual(self.count("paper_fills"), 1)
        with self.assertRaises(ValueError):
            self.resume("unsafe")
        self.engine.reconcile("reconcile-one", "test")
        self.assertTrue(self.engine.status()["paused"])
        self.resume("after-reconcile")
        self.complete()
        self.assertEqual(self.count("paper_fills"), 2)

    def test_cancel_partial_keeps_actual_quantity_and_never_completes(self):
        job = self.submit()
        self.tick()
        self.tick()
        result = self.engine.cancel(self.plan()["decision_id"], "cancel-one", "test")
        self.assertEqual(result["report"]["status"], "partial")
        self.assertEqual(result["report"]["items"][0]["actual_quantity"], "0.0001")
        self.assertEqual(self.queue.feedback(self.principal, job.job_id).processing.status, "blocked")
        self.assertEqual(self.engine.cancel(self.plan()["decision_id"], "cancel-one", "test"), result)
        self.assertEqual(self.count("paper_fills"), 1)

    def test_cancel_unknown_requires_reconcile(self):
        self.submit()
        self.tick()
        decision = self.plan()["decision_id"]
        self.engine.mark_unknown(decision)
        result = self.engine.cancel(decision, "unknown-cancel", "test")
        self.assertEqual(result["report"]["status"], "unknown")
        self.assertEqual(self.plan()["status"], "unknown")

    def test_expiry_after_partial_cancels_remainder_only(self):
        job = self.submit()
        self.tick()
        self.tick()
        self.clock.now += 30000
        self.tick()
        self.assertEqual(self.count("paper_fills"), 1)
        self.assertEqual(self.engine.report(self.plan()["decision_id"]).status, "partial")
        self.assertEqual(self.queue.feedback(self.principal, job.job_id).processing.status, "blocked")

    def test_control_epoch_change_stops_remaining_fills(self):
        self.submit()
        self.tick()
        self.tick()
        self.queue.configure_control("mock-account", "dummy-agent", "offline-0.1", 2)
        self.tick()
        self.assertEqual(self.count("paper_fills"), 1)
        self.assertEqual(self.plan()["reason"], "STALE_RESULT")
        self.assertTrue(self.engine.status()["paused"])

    def test_stale_quote_is_not_restamped_by_execution(self):
        self.submit()
        self.tick()
        self.clock.now += 90001
        self.engine.advance()
        self.assertEqual(self.count("paper_fills"), 0)
        self.assertTrue(self.engine.status()["paused"])

    def test_price_move_after_acceptance_invalidates_risk(self):
        self.submit()
        self.tick()
        with self.store.transaction() as (c, now):
            q = json.loads(c.execute("SELECT payload FROM paper_quotes WHERE symbol='par:BTC'").fetchone()[0])
            q["usd_per_unit"] = "8000000"
            c.execute("UPDATE paper_quotes SET payload=? WHERE symbol='par:BTC'", (canonical(q),))
        self.engine.advance()
        self.assertEqual(self.count("paper_fills"), 0)
        self.assertTrue(self.engine.status()["paused"])

    def test_multileg_reports_only_complete_when_all_targets_match(self):
        self.submit(symbols=("par:BTC", "par:ETH"))
        self.tick()
        self.tick()
        self.assertEqual(self.engine.report(self.plan()["decision_id"]).status, "partial")
        r = self.complete()
        self.assertEqual(r.status, "completed")
        self.assertEqual(len(r.items), 2)
        self.assertEqual(self.count("paper_fills"), 3)

    def test_resume_idempotency_is_receipt_not_automatic_reauthorization(self):
        old = self.engine.status()
        self.engine.boot()
        response = self.engine.resume("resume-one", old["generation"], old["pause_epoch"], "synthetic-test-operator")
        self.assertTrue(response["resumed"])
        self.assertTrue(self.engine.status()["paused"])
        with self.assertRaises(ValueError):
            self.resume("resume-one")

    def test_immutable_config_and_shared_db_guard(self):
        changed = copy.deepcopy(self.config)
        changed["execution"]["fee_bps"] = "11"
        with self.assertRaises(ValueError):
            PaperEngine(self.store, changed)

    def test_corrupt_fills_positions_or_intent_counter_fail_closed(self):
        self.submit()
        self.complete()
        with self.store.transaction() as (c, now):
            c.execute("UPDATE paper_positions SET quantity='99'")
        with self.assertRaisesRegex(ValueError, "reconcile"):
            self.engine.verify_ledger()

    def test_recover_reservation_before_plan(self):
        self.submit()
        self.assertIsNotNone(self.engine.outbox.begin())
        self.engine.boot()
        self.resume("after-orphan")
        self.complete()
        self.assertEqual(self.count("paper_plans"), 1)
        self.assertEqual(self.count("paper_fills"), 2)

    def test_process_hard_exit_before_and_after_fill_commit(self):
        self.submit()
        self.tick()
        source = Path(self.temp.name)/"config.json"
        source.write_text(canonical(self.config), encoding="utf-8")
        code = (
            "import json,sys,os; from perpetual_alpha.runtime.store import RuntimeStore; "
            "from perpetual_alpha.runtime.paper_engine import PaperEngine; "
            "cfg=json.load(open(sys.argv[2],encoding='utf-8')); "
            "e=PaperEngine(RuntimeStore(sys.argv[1],clock=lambda:int(sys.argv[3])),cfg,"
            "fault=lambda p:os._exit(77) if p==sys.argv[4] else None); e.advance()")
        root = Path(__file__).resolve().parents[1]
        env = {**os.environ, "PYTHONPATH": str(root/"src")}
        for point, expected in (("before_fill_commit", 0), ("after_fill_commit", 1)):
            done = subprocess.run([sys._base_executable, "-c", code, str(self.path), str(source), str(self.clock.now), point],
                                  capture_output=True, env=env, timeout=20)
            self.assertEqual(done.returncode, 77, done.stderr.decode(errors="replace"))
            self.assertEqual(self.count("paper_fills"), expected)
            self.engine.verify_ledger()
        self.engine.boot()
        self.resume("after-hard-exit")
        self.complete()
        self.assertEqual(self.count("paper_fills"), 2)

    def test_final_commit_already_contains_terminal_feedback(self):
        job = self.submit()
        self.tick()
        self.tick()

        def fail(point):
            if point == "after_fill_commit":
                raise RuntimeError("simulated response loss")

        self.engine.fault = fail
        with self.assertRaises(RuntimeError):
            self.engine.advance()
        self.assertEqual(self.queue.feedback(self.principal, job.job_id).processing.status, "completed")
        self.engine.fault = lambda _: None
        self.engine.boot()
        self.assertEqual(self.count("paper_fills"), 2)
        self.assertFalse(self.engine.step())

    def test_concurrent_local_fill_advances_are_serial_and_unique(self):
        self.submit()
        self.tick()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.engine.advance(), range(2)))
        self.assertEqual(self.count("paper_fills"), 2)
        self.assertEqual(self.engine.report(self.plan()["decision_id"]).status, "completed")
        self.engine.verify_ledger()
        with self.store.connection() as c:
            verdicts = [json.loads(r[0])["approval"]["verdict"]["verdict_id"] for r in c.execute("SELECT payload FROM paper_checks")]
            self.assertGreaterEqual(len(set(verdicts)), 2)  # Same clock, different account inputs: distinct approval identity.

    def test_separate_close_and_open_legs_for_reversal(self):
        # Create an isolated config before any jobs so the explicit synthetic reversal permission is immutable.
        path = Path(self.temp.name)/"reversal.sqlite3"
        cfg = copy.deepcopy(self.config)
        cfg["policy"]["allow_reversals"] = True
        cfg["initial_state"]["account"]["positions"] = [{"symbol": "par:BTC", "quantity": "0.0002", "mark_price": "80000"}]
        store = RuntimeStore(path, clock=self.clock)
        queue = JobQueue(store)
        queue.configure_control("mock-account", "dummy-agent", "offline-0.1", 1)
        queue.configure_worker(self.principal)
        engine = PaperEngine(store, cfg)
        engine.boot()
        st = engine.status()
        engine.resume("reversal-resume", st["generation"], st["pause_epoch"], "test")
        enqueue(queue, self.principal, self.clock.now, direction="down")
        engine.step()
        engine.advance()
        engine.advance()
        with store.connection() as c:
            rows = c.execute("SELECT leg,quantity,filled,status FROM paper_intents ORDER BY leg").fetchall()
            self.assertEqual([r["leg"] for r in rows], [0, 1])
            self.assertEqual(rows[0]["status"], "filled")
            self.assertEqual(rows[1]["filled"], "0")
        engine.advance()
        engine.advance()
        engine.verify_ledger()
        with store.connection() as c:
            self.assertEqual(Decimal(c.execute("SELECT quantity FROM paper_positions WHERE symbol='par:BTC'").fetchone()[0]), Decimal("-0.0002"))

    def test_min_notional_rejects_without_making_dust_fill(self):
        path = Path(self.temp.name)/"min-notional.sqlite3"
        cfg = copy.deepcopy(self.config)
        cfg["execution"]["min_notional_usd"]["par:BTC"] = "20"
        store = RuntimeStore(path, clock=self.clock)
        queue = JobQueue(store)
        queue.configure_control("mock-account", "dummy-agent", "offline-0.1", 1)
        queue.configure_worker(self.principal)
        e = PaperEngine(store, cfg)
        e.boot()
        status = e.status()
        e.resume("min-resume", status["generation"], status["pause_epoch"], "test")
        enqueue(queue, self.principal, self.clock.now)
        e.step()
        e.advance()
        with store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM paper_fills").fetchone()[0], 0)
            r = json.loads(c.execute("SELECT report FROM paper_plans").fetchone()[0])
            self.assertEqual((r["status"], r["reason_code"]), ("failed", "ORDER_RULE"))

    def test_projected_fees_cannot_silently_violate_margin_buffer(self):
        path = Path(self.temp.name)/"cost-risk.sqlite3"
        cfg = copy.deepcopy(self.config)
        cfg["policy"]["margin_buffer_usd"] = "984"
        store = RuntimeStore(path, clock=self.clock)
        queue = JobQueue(store)
        queue.configure_control("mock-account", "dummy-agent", "offline-0.1", 1)
        queue.configure_worker(self.principal)
        e = PaperEngine(store, cfg)
        e.boot()
        status = e.status()
        e.resume("cost-resume", status["generation"], status["pause_epoch"], "test")
        enqueue(queue, self.principal, self.clock.now)
        e.step()
        e.advance()
        with store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM paper_fills").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT reason FROM paper_plans").fetchone()[0], "PROJECTED_COST_RISK")
        e.verify_ledger()

    def test_pause_peak_and_session_baseline_survive_restart(self):
        self.submit()
        self.complete()
        old = self.engine.status()
        self.engine.boot()
        current = self.engine.status()
        for k in ("cash", "peak", "session_start"):
            self.assertEqual(old[k], current[k])
        self.assertNotEqual(old["generation"], current["generation"])
        self.assertTrue(current["paused"])

    def test_missing_quote_latches_pause_and_requires_explicit_recovery(self):
        with self.store.transaction() as (c, now):
            c.execute("DELETE FROM paper_quotes WHERE symbol='par:BTC'")
        self.assertTrue(self.engine.status()["paused"])
        self.engine.publish_fixture()
        self.assertTrue(self.engine.status()["paused"])
        self.resume("after-quote")
        self.assertFalse(self.engine.status()["paused"])

    def test_changed_step_preserves_partial_dust_and_refuses_rounding(self):
        self.submit()
        self.tick()
        self.tick()
        with self.store.transaction() as (c, now):
            quote = json.loads(c.execute("SELECT payload FROM paper_quotes WHERE symbol='par:BTC'").fetchone()[0])
            quote["quantity_step"] = "0.0002"
            c.execute("UPDATE paper_quotes SET payload=? WHERE symbol='par:BTC'", (canonical(quote),))
        self.engine.advance()
        self.assertEqual(self.count("paper_fills"), 1)
        report = self.engine.report(self.plan()["decision_id"])
        self.assertEqual((report.status, report.reason_code), ("partial", "ORDER_RULE"))

    def test_abort_before_intent_commit_requeues_only_unplanned_delivery(self):
        self.submit()

        def abort(point):
            if point == "before_stage_commit":
                raise RuntimeError("injected failure")

        self.engine.fault = abort
        with self.assertRaises(RuntimeError):
            self.tick()
        self.assertEqual(self.count("paper_plans"), 0)
        self.assertEqual(self.count("paper_intents"), 0)
        self.engine.fault = lambda _: None
        self.engine.boot()
        self.resume("after-stage-abort")
        self.complete()
        self.assertEqual(self.count("paper_plans"), 1)

    def test_fill_evidence_and_cumulative_counter_corruption_are_rejected(self):
        self.submit()
        self.tick()
        self.tick()
        with self.store.transaction() as (c, now):
            c.execute("UPDATE paper_intents SET filled='0'")
        with self.assertRaises(ValueError):
            self.engine.verify_ledger()
        with self.store.transaction() as (c, now):
            c.execute("UPDATE paper_intents SET filled='0.0001'")
            c.execute("UPDATE paper_fills SET digest=?", ("0"*64,))
        with self.assertRaises(ValueError):
            self.engine.verify_ledger()

    def test_delivery_fencing_cannot_be_bypassed_by_existing_intent(self):
        self.queue.configure_control("other-scope", "dummy-agent", "offline-0.1", 1)
        enqueue(self.queue, self.principal, self.clock.now, name="foreign", scope="other-scope")
        foreign = self.engine.outbox.begin()
        with self.assertRaisesRegex(ValueError, "scope"):
            self.engine.stage(foreign)
        self.engine.outbox.finish(foreign.processing.processing_id, foreign.delivery_epoch, "block", reason_code="INVALID_INPUT")
        self.submit()
        self.tick()
        self.engine.outbox.recover()  # Not the Paper boot path; a competing operator invalidates the reservation.
        with self.assertRaises(ValueError):
            self.engine.advance()
        self.assertEqual(self.count("paper_fills"), 0)

    def test_new_execution_state_does_not_change_forecast_cutoff(self):
        self.submit()
        self.tick()
        self.clock.now += 5000
        self.tick()
        with self.store.connection() as c:
            evidence = json.loads(c.execute("SELECT payload FROM paper_checks ORDER BY sequence DESC LIMIT 1").fetchone()[0])
            original = json.loads(self.plan()["proposal"])
            self.assertGreater(evidence["state"]["asof_ms"], original["forecast_asof_ms"])
            self.assertEqual(original["forecast"]["items"][0]["asof_ms"], original["forecast_asof_ms"])
            self.assertEqual(evidence["approval"]["verdict"]["status"], "approved")
            from perpetual_alpha.risk_gate import SimulationRiskGate
            gate = SimulationRiskGate(self.engine.policy, self.engine._supervisor(c))
            gate.policy["gross_cap_usd"] = "99"
            self.assertNotEqual(gate.recheck_execution(self.engine._proposal(self.plan()), evidence["state"], self.clock.now)["verdict"]["status"], "approved")

    def test_cancel_before_fill_is_failed_not_completed(self):
        job = self.submit()
        self.tick()
        result = self.engine.cancel(self.plan()["decision_id"], "cancel-before-fill", "test")
        self.assertEqual(result["report"]["status"], "failed")
        self.assertEqual(self.queue.feedback(self.principal, job.job_id).processing.status, "blocked")
        self.assertEqual(self.count("paper_fills"), 0)

    def test_cost_policy_model_and_unknown_fields_rejected(self):
        for field, value in (("fee_bps", "NaN"), ("slippage_bps", "10000"), ("order_timeout_ms", True)):
            cfg = copy.deepcopy(self.config)
            cfg["execution"][field] = value
            with self.assertRaises(ValueError):
                PaperEngine(self.store, cfg)
        cfg = copy.deepcopy(self.config)
        cfg["model_version"] = "other"
        with self.assertRaises(ValueError):
            PaperEngine(self.store, cfg)

    def test_operator_id_conflicts_do_not_repeat_side_effects(self):
        self.submit()
        self.tick()
        decision = self.plan()["decision_id"]
        first = self.engine.cancel(decision, "one-operator-id", "test")
        self.assertEqual(self.engine.cancel(decision, "one-operator-id", "test"), first)
        with self.assertRaises(ValueError):
            self.engine.reconcile("one-operator-id", "test")

    def test_stale_authorization_after_new_pause_reason_is_rejected(self):
        before = self.engine.status()
        with self.store.transaction() as (c, now):
            self.engine._pause(c, "NEW_FAILURE")
        with self.assertRaises(ValueError):
            self.engine.resume("old-pause", before["generation"], before["pause_epoch"], "test")

    def test_unknown_and_partial_survive_boot_without_ledger_rewrite(self):
        self.submit()
        self.tick()
        self.tick()
        self.engine.mark_unknown(self.plan()["decision_id"])
        self.engine.boot()
        self.assertEqual(self.plan()["status"], "unknown")
        self.assertEqual(self.count("paper_fills"), 1)
        self.assertFalse(self.engine.advance())

    def test_real_observation_cannot_initialize_paper_account(self):
        root = Path(__file__).resolve().parents[1]
        observation = json.loads((root/"docs/examples/account-observation.synthetic.json").read_text(encoding="utf-8"))["snapshot"]
        cfg = copy.deepcopy(self.config)
        cfg["initial_state"]["account"] = observation
        with self.assertRaises(ValueError):
            PaperEngine(self.store, cfg)

    def test_empty_forecast_does_not_generate_flatten_orders(self):
        # Replace accepted input only before submission using an explicit empty Batch.
        from perpetual_alpha.contracts import ForecastBatch
        from perpetual_alpha.runtime.contracts import AnalysisJob, JobLimits
        from perpetual_alpha.runtime.mock_config import load_config
        from perpetual_alpha.runtime.mock_fixture import context_for, initial_account
        from perpetual_alpha.transport.messages import ClaimRequest, ForecastSubmission
        job = AnalysisJob("empty", "empty-context", "dummy-agent", "offline-0.1", "mock-account", 1, "control", ("par:BTC",),
                          self.clock.now, self.clock.now, self.clock.now+120000, "queued", 0, 0, JobLimits(10,100,262144,2,90000,20000))
        sim = load_config(Path(__file__).resolve().parents[1]/"config/mock-http.toml")["simulation"]
        ctx, req = context_for(sim, job.context_id, job.asof_ms, initial_account(sim, job.asof_ms))
        self.queue.enqueue(job, ctx, req)
        claim = self.queue.claim(self.principal, ClaimRequest("empty-claim"), lease_ms=30000)
        batch = ForecastBatch("empty-batch", job.context_id, job.producer_id, job.producer_version, job.asof_ms, ())
        self.queue.submit(self.principal, ForecastSubmission("empty-submit", claim.lease.ref, batch))
        self.tick()
        self.tick()
        self.assertEqual(self.count("paper_intents"), 0)
        self.assertEqual(self.engine.report(self.plan()["decision_id"]).status, "completed")

    def test_v3_additive_migration_preserves_existing_runtime_evidence(self):
        path = Path(self.temp.name)/"v3.sqlite3"
        migrations = Path(__file__).resolve().parents[1]/"src/perpetual_alpha/runtime/migrations"
        c = sqlite3.connect(path)
        try:
            for filename in ("001_runtime.sql", "002_mock_http.sql", "003_gateway.sql"):
                c.executescript((migrations/filename).read_text(encoding="utf-8"))
            c.execute(f"PRAGMA application_id={APPLICATION_ID}")
            c.execute("PRAGMA user_version=3")
            c.execute("INSERT INTO audit VALUES(1,1,'kept','{}')")
            c.commit()
        finally:
            c.close()
        migrated = RuntimeStore(path, clock=self.clock)
        self.assertEqual(migrated.audit()[0]["kind"], "kept")
        with migrated.connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(conn.execute("SELECT count(*) FROM paper_fills").fetchone()[0], 0)

    def test_modes_cannot_share_runtime_even_before_first_job(self):
        from perpetual_alpha.journal import Journal
        from perpetual_alpha.runtime.mock_config import load_config
        from perpetual_alpha.runtime.mock_consumer import MockConsumer
        journal = Journal(Path(self.temp.name)/"legacy-journal.sqlite3")
        self.addCleanup(journal.close)
        config = load_config(Path(__file__).resolve().parents[1]/"config/mock-http.toml")["simulation"]
        with self.assertRaises(ValueError):
            MockConsumer(self.store, journal, config)
        other = RuntimeStore(Path(self.temp.name)/"legacy-runtime.sqlite3", clock=self.clock)
        MockConsumer(other, journal, config)
        with self.assertRaises(ValueError):
            PaperEngine(other, self.config)


if __name__ == "__main__":
    unittest.main()
