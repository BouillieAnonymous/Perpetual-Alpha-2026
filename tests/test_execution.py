import unittest
from unittest.mock import patch

from perpetual_alpha.execution import ExecutionClient


class ExecutionSafetyTests(unittest.TestCase):
    def test_disabled_does_not_load_key_or_create_exchange(self):
        with patch("perpetual_alpha.execution.Path.read_text") as read, \
                patch("perpetual_alpha.execution.Exchange") as exchange:
            with self.assertRaises(ValueError):
                ExecutionClient()
            read.assert_not_called()
            exchange.assert_not_called()

    def test_other_markets_rejected_before_mutation(self):
        client = object.__new__(ExecutionClient)
        with self.assertRaises(ValueError):
            client.order("BTC", True, .001, 10000, "Ioc")

    def test_test_notional_limit(self):
        client = object.__new__(ExecutionClient)
        with self.assertRaises(ValueError):
            client.order("par:BTC", True, 1, 100000, "Ioc")
