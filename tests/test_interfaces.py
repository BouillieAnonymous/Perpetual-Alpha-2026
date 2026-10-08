import unittest
from decimal import Decimal

from perpetual_alpha.interfaces import Decision, ExecutionItem, ExecutionReport, TargetPosition


class ContractTests(unittest.TestCase):
    def decision(self, targets=()):
        return Decision("d1", "c1", "baseline", "v1", 1000, 2000, targets, "example")

    def test_signed_absolute_targets_and_empty_noop_round_trip(self):
        targets = (TargetPosition("par:BTC", Decimal("-0.001")),
                   TargetPosition("par:ETH", Decimal("0")))
        original = self.decision(targets)
        self.assertEqual(Decision.from_dict(original.to_dict()), original)
        self.assertEqual(Decision.from_dict(self.decision().to_dict()).targets, ())

    def test_invalid_numbers_duplicate_symbols_and_versions_rejected(self):
        payload = self.decision((TargetPosition("par:BTC", Decimal("1")),)).to_dict()
        for invalid in (1.0, "NaN", "Infinity"):
            payload["targets"][0]["quantity"] = invalid
            with self.assertRaises((ValueError, ArithmeticError)):
                Decision.from_dict(payload)
        target = TargetPosition("par:BTC", Decimal("1"))
        with self.assertRaises(ValueError):
            self.decision((target, target))
        payload = self.decision().to_dict()
        payload["protocol_version"] = "2.0"
        with self.assertRaises(ValueError):
            Decision.from_dict(payload)

    def test_report_cannot_claim_completion_without_actual_target(self):
        item = ExecutionItem("par:BTC", Decimal("1"), None, Decimal("0"), ())
        with self.assertRaises(ValueError):
            ExecutionReport("r1", "d1", 1, 1000, "completed", "OK", (item,))
        report = ExecutionReport("r2", "d1", 2, 1000, "unknown", "NETWORK_UNKNOWN", (item,))
        self.assertIsNone(report.to_dict()["items"][0]["actual_quantity"])
        payload = self.decision().to_dict()
        payload["expires_ms"] = payload["created_ms"]
        with self.assertRaises(ValueError):
            Decision.from_dict(payload)
