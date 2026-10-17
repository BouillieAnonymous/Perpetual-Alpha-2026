import unittest

from perpetual_alpha.basis_research import BasisForecastProducer, ReplayConfig, direction, replay
from perpetual_alpha.context import SOURCE
from perpetual_alpha.forecasting import checked_forecast
from perpetual_alpha.producers import ProducerRegistry


def quote(now, bid="98", ask="99", oracle="100", size="10"):
    return dict(symbol="par:BTC", event_ms=now - 10, received_ms=now,
                context_received_ms=now - 20, bid=bid, ask=ask, oracle=oracle, funding="0",
                book=dict(coin="par:BTC", time=now - 10,
                          levels=[[dict(px=bid, sz=size)], [dict(px=ask, sz=size)]]))


class BasisTests(unittest.TestCase):
    def test_executable_side(self):
        self.assertEqual(direction("98", "101", "100", "20"), "neutral")
        self.assertEqual(direction("98", "99", "100", "20"), "up")
        self.assertEqual(direction("101", "102", "100", "20"), "down")

    def test_invalid_prices_and_parameters(self):
        for value in ("NaN", "Infinity", "0", "-1"):
            with self.assertRaises(ValueError):
                direction(value, "100", "100", "20")
        with self.assertRaises(ValueError):
            ReplayConfig(fee_bps="-1")
        with self.assertRaises(ValueError):
            ReplayConfig(latency_ms=0)

    def test_next_book_fill_and_real_exit(self):
        rows = [quote(1000), quote(2000), quote(302000, "100", "101")]
        result = replay(rows)
        self.assertEqual(result["closed_trades"], 1)
        self.assertEqual(result["trades"][0]["opened_ms"], 2000)
        self.assertEqual(result["open_positions"], 0)

    def test_no_same_snapshot_or_terminal_oracle_fill(self):
        self.assertEqual(replay([quote(1000)])["closed_trades"], 0)
        result = replay([quote(1000), quote(2000)])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["realized_pnl"], "0")

    def test_future_reference_and_missing_receipt(self):
        rows = [quote(1000), quote(2000)]
        rows[0]["context_received_ms"] = None
        rows[1]["context_received_ms"] = 3000
        result = replay(rows)
        self.assertEqual(result["rejected_time_rows"], 2)
        self.assertEqual(result["signals"], 0)

    def test_depth_and_expired_signal(self):
        self.assertEqual(replay([quote(1000), quote(2000, size="0.00001")])["open_positions"], 0)
        self.assertEqual(replay([quote(1000), quote(100000)])["open_positions"], 0)

    def test_costs_turn_flat_trade_negative(self):
        result = replay([quote(1000), quote(2000), quote(302000)])
        self.assertTrue(result["trades"][0]["pnl"].startswith("-"))

    def test_unsorted_and_wrong_book_rejected(self):
        with self.assertRaises(ValueError):
            replay([quote(2000), quote(1000)])
        row = quote(1000)
        row["book"]["coin"] = "par:ETH"
        with self.assertRaises(ValueError):
            replay([row])

    def test_registry_and_abstention(self):
        producer = BasisForecastProducer(("par:BTC",), 300000, 90000)
        registry = ProducerRegistry()
        registry.register(producer)
        self.assertIs(registry.resolve("reference-basis"), producer)
        context = dict(asof_ms=1000, context_id="test", datasets={})
        self.assertEqual(producer.forecast(context).items[0].status, "abstain")
        context["datasets"]["par:BTC_quotes"] = dict(status="ready", records=[
            dict(event_ms=990, received_ms=1000, data=dict(bid="98", ask="99", oracle="100"))])
        self.assertEqual(producer.forecast(context).items[0].direction, "up")

    def test_checked_forecast_boundary(self):
        producer = BasisForecastProducer(("par:BTC",), 300000, 90000)
        context = dict(protocol_version="1.0", asof_ms=1000, context_id="checked",
                       account=None, execution_reports=[], datasets={"par:BTC_quotes": dict(
                           source=SOURCE, kind="quote", parameters={}, required=True, status="ready",
                           records=[dict(symbol="par:BTC", event_ms=990, received_ms=1000,
                                         data=dict(bid="98", ask="99", oracle="100", mark="100"))])})
        batch = checked_forecast(producer, context, clock=lambda: 1000)
        self.assertEqual(batch.items[0].direction, "up")
        self.assertEqual(batch.items[0].evidence_refs, ("par:BTC_quotes",))


if __name__ == "__main__":
    unittest.main()
