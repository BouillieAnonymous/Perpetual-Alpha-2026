"""Static research figure; optional local matplotlib, never part of runtime."""
import gzip
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path('.cache/plot-libs').resolve()))
import matplotlib  # noqa: E402

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402

p = Path('docs/research/weekly-synthesis-2026-10-26/alpha-screen')
r = json.loads((p/'results.json').read_text())
d = json.loads((p/'diagnostics.json').read_text())
plt.rcParams.update({'font.family':'DejaVu Sans', 'font.size':10,
                     'axes.spines.top':False, 'axes.spines.right':False})
fig, ax = plt.subplots(3, 1, figsize=(10, 10), constrained_layout=True)
active = [x['total']['net'] for x in r['results'] if x['total']['n']]
ax[0].hist(active, bins=32, color='#64748b', edgecolor='white')
ax[0].axvline(0, color='#dc2626', lw=1.5)
ax[0].set(title='267 active scenarios: only 2 net-positive; 0 passed the priority gate',
          xlabel='Cumulative scenario PnL (USDP), 100 USDP per entry', ylabel='Scenarios')
daily = defaultdict(lambda: [0.,0.])
with gzip.open(p/'trades.jsonl.gz', 'rt', encoding='utf-8') as f:
    for line in f:
        t = json.loads(line)
        if t['symbol']=='pas:HYPE' and t['rule']=='imbalance' and t['hold_minutes']==240:
            date = datetime.fromtimestamp(t['exit_ms']/1000, timezone.utc).date()
            daily[date][0] += t['net']
            daily[date][1] += t['stress_net']
days, base, stress = sorted(daily), [], []
for day in days:
    base.append((base[-1] if base else 0)+daily[day][0])
    stress.append((stress[-1] if stress else 0)+daily[day][1])
ax[1].plot(days, base, marker='o', label='Base: 9 bp fee + 2 bp slip / side', color='#0f766e')
ax[1].plot(days, stress, marker='o', label='Stress: 18 bp fee + 4 bp slip / side', color='#dc2626')
ax[1].axhline(0, color='#94a3b8', lw=1)
ax[1].set(title='HYPE book imbalance / 4h: thin profit, concentrated in a few days',
          ylabel='Cumulative realized PnL (USDP)')
ax[1].legend(fontsize=9)
labels = [f"start +{x['start_offset_minutes']}m\ndelay {x['decision_delay_minutes']}m" for x in d['cases']]
values = [x['total']['net'] for x in d['cases']]
ax[2].bar(labels, values, color=['#0f766e' if x>0 else '#dc2626' for x in values])
ax[2].axhline(0, color='#94a3b8', lw=1)
ax[2].set_ylim(min(values)-2.0, max(values)+1.5)
ax[2].set(title='Post-selection diagnostics: all 7 cases shown; no best case selected', ylabel='Net PnL (USDP)')
for i, v in enumerate(values):
    ax[2].annotate(f'{v:+.2f}', (i,v), xytext=(0,4 if v>=0 else -13), textcoords='offset points', ha='center', fontsize=9)
fig.suptitle('PAS data-driven screen | 19-26 Oct 2026 | Retrospective, not validated Alpha', fontsize=14, weight='bold')
fig.savefig(p/'screen-summary.png', dpi=170, facecolor='white')
fig.savefig(p/'screen-summary.pdf', facecolor='white')
print(p/'screen-summary.png')
