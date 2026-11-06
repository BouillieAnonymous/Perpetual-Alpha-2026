"""Post-selection sensitivity diagnostics. All cases reported; no new winner chosen."""
import gzip
import hashlib
import json
from pathlib import Path

from study_pas_weekly_alpha import CUT1, CUT2, MINUTE, load, panel, simulate, stats

root = Path('docs/research/weekly-synthesis-2026-10-26/alpha-screen')
original = json.loads((root/'results.json').read_text())
books, contexts, funds, settlements, manifest = load(Path('.cache/alpha-week-2026-10-26/pas-prefix.jsonl.gz'))
grids, _, start, end = panel(books, contexts, funds)
segments = [('development', start, CUT1), ('validation', CUT1, CUT2), ('audit', CUT2, end)]
symbol = 'pas:HYPE'
cases = []
for offset, delay in [(0,0), (15,0), (60,0), (0,2), (0,5), (15,2), (60,2)]:
    trades, sections = [], {}
    for name, a, b in segments:
        ts, rs = simulate(books[symbol], grids[symbol], settlements[symbol], contexts[symbol],
                          a+offset*MINUTE, b, 'imbalance', 240, delay*MINUTE)
        sections[name] = stats(ts, rs)
        trades.extend(ts)
    total = stats(trades, {})
    cases.append(dict(start_offset_minutes=offset, decision_delay_minutes=delay,
                      total=total, splits=sections))
base = cases[0]
key = next(x for x in original['results'] if x['symbol']==symbol and x['rule']=='imbalance' and x['hold_minutes']==240)
assert abs(key['total']['net']-base['total']['net']) < 1e-10
trades = []
with gzip.open(root/'trades.jsonl.gz','rt',encoding='utf-8') as f:
    for line in f:
        t=json.loads(line)
        if t['symbol']==symbol and t['rule']=='imbalance' and t['hold_minutes']==240:
            trades.append(t)
concentration = dict(leave_one_day_out={d:key['total']['net']-v for d,v in key['total']['daily'].items()},
                     without_best_trade=key['total']['net']-max(t['net'] for t in trades),
                     without_best_three=key['total']['net']-sum(sorted((t['net'] for t in trades),reverse=True)[:3]),
                     by_side={str(s):dict(n=sum(t['side']==s for t in trades),net=sum(t['net'] for t in trades if t['side']==s)) for s in [1,-1]})
families=[]
for name in sorted({x['rule'] for x in original['results']}):
    xs=[x for x in original['results'] if x['rule']==name]
    active=[x for x in xs if x['total']['n']]
    families.append(dict(rule=name, combinations=len(xs), active=len(active),
                         positive=sum(x['total']['net']>0 for x in active),
                         positive_mid_gross=sum(x['total']['mid_gross']>0 for x in active),
                         positive_touch=sum(x['total']['touch_net']>0 for x in active)))
out=dict(schema='pas-alpha-post-selection-diagnostics/1',
         designation='Retrospective diagnostics after observing the main screen; no parameter selection',
         original_source_sha256=original['source_sha256'],
         diagnostic_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         symbol=symbol, rule='imbalance', hold_minutes=240, cases=cases,
         concentration=concentration, families=families)
(root/'diagnostics.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n',encoding='utf-8')
for x in cases:
    print('offset',x['start_offset_minutes'],'delay',x['decision_delay_minutes'],
          'n',x['total']['n'],'net',round(x['total']['net'],6),'stress',round(x['total']['stress_net'],6),
          'sections', [round(x['splits'][s]['net'],6) for s in ['development','validation','audit']])
print('concentration',json.dumps(concentration))
print('families',json.dumps(families))
