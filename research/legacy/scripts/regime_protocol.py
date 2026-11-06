"""Frozen, small, retrospective regime study; no runtime integration."""
import time
from pathlib import Path

from run_long_history import load_sources

from perpetual_alpha.offline_quant.data import HOUR, source_hash
from perpetual_alpha.offline_quant.run import source_digest, write_json

ROOT = Path(__file__).resolve().parents[1]
PRIOR = ROOT/'docs/research/new-alpha-discovery-2026-10-18/long-history-37'
OUT = ROOT/'docs/research/regime-conditional-2026-10-18'
DAY = 24*HOUR
STATES = ['TREND_HIGH', 'TREND_LOW', 'RANGE_HIGH', 'RANGE_LOW']
FAMILIES = ['TSM', 'ATR', 'ROLLING_Z']
FILTER = {'TSM': 'TREND_LOW', 'ATR': 'TREND_HIGH', 'ROLLING_Z': 'RANGE_LOW'}


def freeze():
    if OUT.exists():
        raise FileExistsError('Never overwrite the declaration')
    OUT.mkdir()
    bars, _, _, _, _ = load_sources()
    start = max(v[0].time for v in bars.values())+193*HOUR
    start = ((start+DAY-1)//DAY)*DAY
    end = min(v[-1].time for v in bars.values())
    holdout = end-28*DAY
    folds, test = [], start+74*DAY+24*HOUR
    while test+14*DAY <= holdout-12*HOUR:
        val_end = test-12*HOUR
        val_start = val_end-14*DAY
        train_end = val_start-12*HOUR
        folds.append({'name': f'walk-{len(folds)+1}', 'train': [train_end-60*DAY, train_end],
                      'validation': [val_start, val_end], 'test': [test, test+14*DAY]})
        test += 14*DAY
    val_end, val_start = holdout-12*HOUR, holdout-12*HOUR-14*DAY
    train_end = val_start-12*HOUR
    folds.append({'name': 'final_locked28d', 'train': [train_end-60*DAY, train_end],
                  'validation': [val_start, val_end], 'test': [holdout, end]})
    write_json(OUT/'protocol.json', {
        'frozen_ms': time.time_ns()//1000000, 'parent_commit': '6832145603b2bbd305fbbd91377d16538ecb5951',
        'frozen_engine_sha256': source_digest(), 'prior_hashes_sha256': source_hash(PRIOR/'hashes.json'),
        'scope': 'all170 fixed rules attribution; causal overlays for three predeclared representatives; all past already seen in PR15',
        'feature': '25 closes efficiency>.35; RV24 stdev vs median strictly previous168 RV values; HIGH if >median; UNKNOWN until193h continuous history',
        'states': STATES, 'families': FAMILIES, 'simple_filter': FILTER,
        'selector': 'asset-specific state->TSM/ATR/ROLLING_Z/FLAT, evaluated at UTC12h; held family until next12h, follow causal original shadow target',
        'training_gate': '>=30 completed independent entry episodes in state,>=10 entry days; positive state-hour net; daily5day block bootstrap lower95>0; rank by mean daily conditional PnL, frozen family order ties',
        'validation_gate': 'choose best TRAIN-qualified family only, require >=5 completed entry episodes and>=3entrydays in same state and positive state-hour net in14d validation; failure->FLAT, no runner-up search',
        'costs': 'reuse native4.5bps/HIP3 9bps, full spread6+slip2/side delay1h; stress doublefee/spread18/slip4 delay2h; own funding only',
        'capital': '10000 each of10 sleeves, total100000; fixed equal10% sleeve weights; original max.2 sleeve initial capital; no optimized risk weights',
        'holdout': 'last28d frozen before any new regime outcomes, no within-holdout retraining; algorithmic retrospective holdout, NOT genuinely unseen history',
        'folds': folds, 'purge_hours': 12, 'common_start_ms': start, 'common_end_ms': end,
        'conditional_status': '>=30 completed episodes,>=10entrydays,>=3months with>=5episodes: else INSUFFICIENT_DATA; adequate and nonpositive=>REJECTED; positive=>INCONCLUSIVE; never PROMISING here',
        'attribution': 'PnL hour label from classifier at previous hour; entry episode counts separately; NOT entry-state PnL or executable filter',
        'all_attempts_recorded': True, 'threshold_search': False, 'prospective_status': 'INSUFFICIENT_DATA',
        'stop': 'no positive stable selector excess over FLAT and each fixed family in both rolling and locked28d, or inadequate active support => no runtime/ML development justified'})
    print('Frozen regime declaration', len(folds), 'time blocks', flush=True)


if __name__ == '__main__':
    freeze()
