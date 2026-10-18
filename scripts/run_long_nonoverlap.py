"""Declared diagnostic excluding PAR selection era; not a new parameter search."""
import gzip
import json
import time

from long_history_protocol import MAPPING, OUT
from run_long_history import baseline, load_sources, matched

from perpetual_alpha.offline_quant.data import HOUR
from perpetual_alpha.offline_quant.replay import Costs, replay
from perpetual_alpha.offline_quant.run import orders_for, write_json


def freeze_amendment():
    path = OUT/'nonoverlap-amendment.json'
    if path.exists():
        raise FileExistsError('Do not overwrite diagnostic declaration')
    registry = json.loads((OUT/'registry-170.json').read_text(encoding='utf-8'))
    write_json(path, {'declared_ms': time.time_ns()//1000000,
                     'reason': 'full external history overlaps later PAR selection window; exclude it for a separately reported falsification diagnostic',
                     'outcomes_read_when_declared': 'no new experiment returns viewed; primary runner still running',
                     'cutoffs_ms': {r['asset']: int(r['original']['start_ms']) for r in registry},
                     'rules': 'all37 same parameters +all10 fixed long/short/flat +37 matched direction pairs; base only',
                     'does_not_replace_primary_counts_or_frozen_screen': True, 'status': 'RETROSPECTIVE_NOT_PROSPECTIVE'})


def run():
    if (OUT/'nonoverlap-summary.json').exists():
        raise FileExistsError('Preserve existing nonoverlap diagnostic')
    amendment = json.loads((OUT/'nonoverlap-amendment.json').read_text(encoding='utf-8'))
    registry = json.loads((OUT/'registry-170.json').read_text(encoding='utf-8'))
    bars, rates, specs, _, _ = load_sources()
    summaries = []
    with gzip.open(OUT/'nonoverlap-ledgers.jsonl.gz', 'wt', encoding='utf-8', newline='\n') as stream:
        for asset in MAPPING:
            symbol, series = 'par:'+asset, bars.get(asset, [])
            if not series:
                continue
            start, end = series[0].time, amendment['cutoffs_ms'][asset]
            own_rates = {symbol: rates[asset]}
            definitions = [(r['rule'], r['definition'], True) for r in registry if r['asset'] == asset and r['selected']]
            definitions += [(kind, None, False) for kind in ['FIXED-LONG', 'FIXED-SHORT', 'NO-TRADE']]
            for kind, definition, selected in definitions:
                signal = (orders_for(definition, {symbol: series}, own_rates, {}, start, end) if definition else
                          baseline(series, start, end, {'FIXED-LONG': 1, 'FIXED-SHORT': -1, 'NO-TRADE': 0}[kind])
                          if kind != 'NO-TRADE' else [])
                plans = [(kind, signal)]
                if selected:
                    plans += [(kind+'::MATCHED-LONG', matched(signal, 1)), (kind+'::MATCHED-SHORT', matched(signal, -1))]
                cost = Costs(fee_bps=4.5 if MAPPING[asset][0] == 'native' else 9)
                for label, signals in plans:
                    result = replay({symbol: series}, own_rates, {symbol: specs[asset]}, signals, start, end, cost)
                    summary = result['summary']
                    identity = {'asset': asset, 'rule': label, 'split': 'pre_PAR_selection_retrospective', 'scenario': 'base',
                                'external_coin': MAPPING[asset][1], 'venue': 'mainnet', 'selected_original': selected}
                    status = ('INSUFFICIENT_DATA' if not summary['scenario_complete'] or not summary['fill_count'] else
                              'LONG_HISTORY_REJECTED' if summary['net_scenario_return'] <= 0 else 'INCONCLUSIVE')
                    summaries.append({**identity, 'start_ms': start, 'end_ms': end, 'hours': (end-start)//HOUR,
                                      'window_status': 'INCONCLUSIVE' if kind == 'NO-TRADE' else status, **summary})
                    stream.write(json.dumps({'identity': identity, **result}, allow_nan=False)+'\n')
            print(asset, 'pre-selection diagnostic complete', flush=True)
    write_json(OUT/'nonoverlap-summary.json', summaries)


if __name__ == '__main__':
    if not (OUT/'nonoverlap-amendment.json').exists():
        freeze_amendment()
    run()
