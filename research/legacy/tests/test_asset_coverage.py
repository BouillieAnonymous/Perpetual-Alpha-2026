"""Coverage expansion must not change original rules or promote missing evidence."""
import importlib.util
import unittest
from pathlib import Path

from perpetual_alpha.offline_quant.run import strategy_definitions

SPEC = importlib.util.spec_from_file_location(
    'coverage_runner', Path(__file__).resolve().parents[1]/'scripts/run_asset_coverage.py')
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class AssetCoverageTests(unittest.TestCase):
    def test_only_asset_list_changes(self):
        original = {d['id']: d for d in strategy_definitions(['par:BTC', 'par:ETH'])}
        for symbol in ['par:BTC', 'par:EWY', 'par:USDJPY']:
            definitions = RUNNER.definitions(symbol)
            self.assertEqual(len(definitions), 18)
            self.assertEqual(len({d['id'] for d in definitions}), 18)
            for definition in definitions:
                self.assertEqual(definition, {**original[definition['id']], 'symbols': [symbol]})
                self.assertNotIn(definition['kind'], ['xsm', 'pair_ratio', 'pair_coint'])
                self.assertNotEqual(definition.get('mode'), 'hedged')

    def test_missing_funding_cannot_be_promoted_on_positive_known_cash(self):
        summary = {'fill_count': 2, 'scenario_complete': False, 'net_scenario_return': None,
                   'known_components_return': .1}
        self.assertEqual(RUNNER.status(summary)[0], 'INSUFFICIENT_DATA')

    def test_positive_seen_scenario_is_inconclusive(self):
        summary = {'fill_count': 100, 'scenario_complete': True, 'net_scenario_return': .2}
        self.assertEqual(RUNNER.status(summary)[0], 'INCONCLUSIVE')
        summary['net_scenario_return'] = -.1
        self.assertEqual(RUNNER.status(summary)[0], 'REJECTED')

    def test_no_fill_is_not_evidence_of_failure(self):
        summary = {'fill_count': 0, 'scenario_complete': True, 'net_scenario_return': 0}
        self.assertEqual(RUNNER.status(summary)[0], 'INSUFFICIENT_DATA')
        self.assertEqual(RUNNER.status(summary, control=True)[0], 'INCONCLUSIVE')
