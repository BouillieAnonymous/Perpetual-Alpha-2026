"""Official cost formula and frozen ledger reconciliation, without network/account access."""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_official_quant_costs import account, fee_rates, funding_payment  # noqa: E402


class OfficialCostTests(unittest.TestCase):
    def test_par_undiscounted_fees_are_doubled_once_not_twice(self):
        maker, taker = fee_rates()
        self.assertEqual(maker, Decimal(".0003"))
        self.assertEqual(taker, Decimal(".0009"))

    def test_discounts_and_scale_follow_published_formula(self):
        maker, taker = fee_rates(deployer_scale=".5", referral_discount=".1")
        self.assertEqual(maker, Decimal(".0002025"))
        self.assertEqual(taker, Decimal(".0006075"))
        self.assertEqual(fee_rates(aligned_quote=True)[1], Decimal(".00081"))
        self.assertEqual(fee_rates(growth_mode=True)[1], Decimal(".00009"))

    def test_negative_maker_rebate_is_not_scaled_as_positive_fee(self):
        maker, _ = fee_rates(maker_base="-.00001", aligned_quote=True)
        self.assertEqual(maker, Decimal("-.0000125"))

    def test_funding_sign_and_hourly_rate_not_divided_by_eight_again(self):
        self.assertEqual(funding_payment(2, 100, ".001"), Decimal("-.2"))
        self.assertEqual(funding_payment(-2, 100, ".001"), Decimal(".2"))
        self.assertEqual(funding_payment(2, 100, "-.001"), Decimal(".2"))

    def test_invalid_input_rejected(self):
        for bad in ["NaN", "Infinity"]:
            with self.assertRaises(ValueError):
                fee_rates(taker_base=bad)
        with self.assertRaises(ValueError):
            funding_payment(1, 0, ".01")

    def test_frozen_fills_reconcile_and_zero_friction_is_only_a_sensitivity(self):
        detail = {"strategy_id": "flat", "split": "full_seen", "scenario": "base",
            "summary": {"funding_scenario": 0, "costs": {"fee_bps": 9}, "fees": 1.8,
                        "initial_capital": 10000, "gross_price_pnl": 0,
                        "scenario_complete": True, "net_scenario_return": -.00028, "closed_segments": 1},
            "fills": [{"delta": 10, "scenario_price": 100.05, "reference": 100, "friction": .5},
                      {"delta": -10, "scenario_price": 99.95, "reference": 100, "friction": .5}],
            "funding": []}
        row = account(detail)
        self.assertAlmostEqual(row["fee_only_zero_friction_return"], -.00018)
        self.assertAlmostEqual(row["official_taker_with_frozen_friction_return"], -.00028)
        self.assertIsNone(row["executable_net_return"])
        self.assertIsNone(row["official_exact_funding"])


if __name__ == "__main__":
    unittest.main()
