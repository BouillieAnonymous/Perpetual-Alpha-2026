"""Frozen cohort, matched direction ablations and descriptive block uncertainty."""
import importlib.util
import sys
import unittest
from pathlib import Path

from perpetual_alpha.offline_quant.signals import Order

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'


def module(name):
    # Scripts import each other by name; expose only this repository's scripts.
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS/(name+'.py'))
        result = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(result)
        return result
    finally:
        sys.path.pop(0)


class LongHistoryTests(unittest.TestCase):
    def test_matched_direction_preserves_timing_and_absolute_exposure(self):
        runner = module('run_long_history')
        signals = [Order(12, {'par:BTC': -.12}, 'entry'), Order(24, {'par:BTC': 0.}, 'exit')]
        for side in [-1, 1]:
            result = runner.matched(signals, side)
            self.assertEqual([r.time for r in result], [12, 24])
            self.assertEqual(result[0].targets['par:BTC'], side*.12)
            self.assertEqual(result[1].targets['par:BTC'], 0)
        self.assertEqual(signals[0].targets['par:BTC'], -.12)

    def test_block_interval_is_reproducible_and_requires_history(self):
        analysis = module('analyze_long_history')
        self.assertIsNone(analysis.interval([.01]*20)['lower95'])
        sample = [.001, -.001, .002, 0, -.003]*20
        self.assertEqual(analysis.interval(sample), analysis.interval(sample))
        self.assertLessEqual(analysis.interval(sample)['lower95'], analysis.interval(sample)['upper95'])

    def test_zero_and_constant_daily_excess(self):
        analysis = module('analyze_long_history')
        for value in [0., .001]:
            result = analysis.interval([value]*100)
            self.assertAlmostEqual(result['lower95'], value)
            self.assertAlmostEqual(result['upper95'], value)
