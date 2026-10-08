import unittest
from unittest.mock import patch

from perpetual_alpha.api_client import ReadOnlyClient, TESTNET_URL, account_ready


class ReadOnlyClientTests(unittest.TestCase):
    @patch("perpetual_alpha.api_client.Info")
    def test_fixed_testnet_and_reject_agent_as_account(self, sdk):
        sdk.return_value.post.return_value = {"role": "agent"}
        with ReadOnlyClient("0x" + "1" * 40) as client:
            with self.assertRaises(ValueError):
                client.account()
            with self.assertRaises(ValueError):
                client.market("BTC")
        sdk.assert_called_once_with(TESTNET_URL, skip_ws=True, perp_dexs=["par"], timeout=15)
        sdk.return_value.spot_user_state.assert_not_called()

    def test_unified_balance_readiness_not_derived_from_perp_margin_value(self):
        self.assertTrue(account_ready({"account_mode": "unifiedAccount", "usdpr_total": "1000000",
                                      "margin_state": {"marginSummary": {"accountValue": "0"}}}))
        self.assertFalse(account_ready({"account_mode": "unifiedAccount", "usdpr_total": None}))
