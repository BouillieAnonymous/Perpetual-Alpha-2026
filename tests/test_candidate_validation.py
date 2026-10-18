"""Frozen funding identity, cache causality, aligned labels and gap rejection."""
import importlib.util
import math
import unittest
from pathlib import Path

from perpetual_alpha.offline_quant.data import HOUR, Bar
from perpetual_alpha.offline_quant.relative import funding_orders

SPEC = importlib.util.spec_from_file_location(
    'candidate_runner', Path(__file__).resolve().parents[1]/'scripts/run_candidate_validation.py')
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def history():
    rows = []
    for i in range(360):
        close = 100*math.exp(.001*i+.02*math.sin(i/8))
        rows.append(Bar('HYPE', (i+1)*HOUR, close-.1, close+.2, close-.2, close, 10, 1000*HOUR))
    rates = {i*HOUR+17: .00002 if i % 60 < 30 else -.00001 for i in range(360)}
    return rows, rates


class CandidateValidationTests(unittest.TestCase):
    def test_no_native_or_pas_history_substituted_for_par(self):
        for venue, symbol in [('par', 'HYPE'), ('mainnet', 'par:HYPE'), ('par', 'pas:HYPE'),
                              ('pas', 'par:EWY'), ('mainnet', 'EWY')]:
            with self.assertRaises(ValueError):
                RUNNER.validate_market(venue, symbol)
        RUNNER.validate_market('mainnet', 'xyz:EWY')
        RUNNER.validate_market('par', 'par:HYPE')

    def test_primary_exactly_reuses_original_funding_rule(self):
        rows, rates = history()
        self.assertEqual(RUNNER.orders(rows, rates, 'funding-mean24', 0),
                         funding_orders({'HYPE': rows}, {'HYPE': rates}, ['HYPE'], mode='mean24'))

    def test_cached_funding_controls_equal_truncated_past_only_orders(self):
        rows, rates = history()
        for kind in ['funding-mean24', 'reverse-funding', 'momentum24', 'fixed-long', 'fixed-short']:
            full = RUNNER.orders(rows, rates, kind, 0)
            cut = 240*HOUR
            limited = RUNNER.orders([r for r in rows if r.time < cut], rates, kind, 0)
            self.assertEqual([o for o in full if o.time < cut], limited)

    def test_future_rates_never_change_earlier_orders(self):
        rows, rates = history()
        changed = {t: -r if t > 240*HOUR else r for t, r in rates.items()}
        first = RUNNER.orders(rows, rates, 'funding-mean24', 0)
        second = RUNNER.orders(rows, changed, 'funding-mean24', 0)
        self.assertEqual([o for o in first if o.time < 240*HOUR], [o for o in second if o.time < 240*HOUR])

    def test_next_open_label_has_no_same_close_execution(self):
        rows, rates = history()
        labels = RUNNER.predictive_rows(rows, rates)
        self.assertGreater(len(labels), 10)
        opens = {r.time-HOUR: r.open for r in rows}
        for label in labels:
            t = label['time_ms']
            self.assertAlmostEqual(label['future_return12'], math.log(opens[t+13*HOUR]/opens[t+HOUR]))

    def test_outcome_gap_is_not_bridged(self):
        rows, rates = history()
        labels = RUNNER.predictive_rows(rows, rates)
        t = labels[0]['time_ms']
        broken = [r for r in rows if r.time != t+5*HOUR]
        self.assertNotIn(t, [r['time_ms'] for r in RUNNER.predictive_rows(broken, rates)])
