"""Time-boundary and overlay tests, not copies of research PnL values."""
import importlib.util
import math
import sys
import unittest
from pathlib import Path

from perpetual_alpha.offline_quant.data import HOUR, Bar

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    spec = importlib.util.spec_from_file_location('regime_core', SCRIPTS/'regime_core.py')
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
finally:
    sys.path.pop(0)


def sample(n=400):
    return [Bar('par:BTC', (i+1)*HOUR, 100+i*.1, 101+i*.1, 99+i*.1,
                100*math.exp(.001*i+.01*math.sin(i/3)), 1, 0) for i in range(n)]


class RegimeStudyTests(unittest.TestCase):
    def test_classifier_future_append_cannot_change_past(self):
        rows = sample()
        whole, prefix = core.classify(rows), core.classify(rows[:260])
        self.assertEqual(prefix, {k:v for k,v in whole.items() if k<=rows[259].time})
        self.assertTrue(all(v['state']=='UNKNOWN' for k,v in prefix.items() if k<rows[192].time))
        self.assertEqual(core.lookup(whole, rows[259].time-1),whole[rows[258].time])

    def test_gap_makes_feature_unknown(self):
        rows = sample()
        del rows[250]
        result = core.classify(rows)
        self.assertEqual(result[rows[280].time]['state'],'UNKNOWN')

    def test_incomplete_or_preexisting_episodes_are_purged(self):
        rows = [{'entry_decision_ms':10,'exit_ms':30,'entry_state':'RANGE_LOW'},
                {'entry_decision_ms':20,'exit_ms':50,'entry_state':'RANGE_LOW'},
                {'entry_decision_ms':25,'exit_ms':35,'entry_state':'RANGE_LOW'}]
        self.assertEqual(core.mature(rows,'RANGE_LOW',20,40),[rows[2]])

    def test_overlay_future_state_or_signal_does_not_change_past(self):
        shadows = {r:[{'time':12*HOUR,'targets':{'par:BTC':.1}},
                      {'time':24*HOUR,'targets':{'par:BTC':0}}] for r in ['TSM','ATR','ROLLING_Z']}
        states = {t*HOUR:{'state':'TREND_LOW'} for t in range(40)}
        mapping = {'TREND_LOW':'TSM'}
        before, _ = core.overlay(shadows,states,'par:BTC',0,20*HOUR,'SELECTOR',mapping)
        states[24*HOUR]={'state':'RANGE_HIGH'}
        after, _ = core.overlay(shadows,states,'par:BTC',0,40*HOUR,'SELECTOR',mapping)
        self.assertEqual(before,[r for r in after if r.time<20*HOUR])
        flat,_ = core.overlay(shadows,states,'par:BTC',0,40*HOUR,'SELECTOR',{})
        self.assertTrue(all(not any(r.targets.values()) for r in flat))

    def test_selector_training_labels_ignore_future_outcome(self):
        fold = {'train':[0,60*24*HOUR],'validation':[61*24*HOUR,75*24*HOUR]}
        future = {'end_ms':100*24*HOUR,'state':'TREND_LOW','net':1e10,'gross':1e10,'fee':0,
                  'spread':0,'slippage':0,'funding':0,'turnover':0,'gross_fraction':.2}
        data = {r:([],[],[]) for r in ['TSM','ATR','ROLLING_Z']}
        base,_ = core.choose(data,fold)
        data['TSM'] = ([future],[],[])
        changed,_ = core.choose(data,fold)
        self.assertEqual(base,changed)
        self.assertTrue(all(v=='FLAT' for v in changed.values()))

    def test_validation_rejection_never_searches_runner_up(self):
        day = 24*HOUR
        fold = {'train':[0,60*day],'validation':[61*day,75*day]}
        episodes = [{'entry_decision_ms':d*day,'exit_ms':d*day+HOUR,'entry_state':'TREND_LOW'}
                    for d in list(range(60))+list(range(61,75))]
        def labels(train,validation):
            return [{'end_ms':(d+1)*day,'state':'TREND_LOW','net':train if d<60 else validation,
                     'gross':0,'fee':0,'spread':0,'slippage':0,'funding':0,'turnover':0,'gross_fraction':.1}
                    for d in list(range(60))+list(range(61,75))]
        data = {'TSM':(labels(2,-1),episodes,[]),'ATR':(labels(1,1),episodes,[]),'ROLLING_Z':([],[],[])}
        mapping,audit = core.choose(data,fold)
        self.assertEqual(mapping['TREND_LOW'],'FLAT')
        self.assertTrue(next(r for r in audit if r.get('rule')=='ATR' and r['state']=='TREND_LOW')['train_eligible'])
        data['TSM'] = labels(2,1),episodes,[]
        mapping,_ = core.choose(data,fold)
        self.assertEqual(mapping['TREND_LOW'],'TSM')
