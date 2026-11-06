"""Descriptive PAS pair check; no parameter selection, orders or profit backtest."""
import csv
import gzip
import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'pas-visual-discovery-v3/audit-verified/causal-panel.jsonl.gz'


def corr(pairs):
    if len(pairs) < 3:
        return None
    x, y = zip(*pairs)
    mx, my = statistics.mean(x), statistics.mean(y)
    xx = math.fsum((a-mx)**2 for a in x)
    yy = math.fsum((b-my)**2 for b in y)
    return math.fsum((a-mx)*(b-my) for a, b in pairs)/math.sqrt(xx*yy) if xx*yy else None


def main():
    panel = {'pas:SP500': {}, 'pas:EWJ': {}}
    with gzip.open(SOURCE, 'rt', encoding='utf8') as f:
        for line in f:
            row = json.loads(line)
            if row['symbol'] in panel:
                panel[row['symbol']][row['decision_ms']//60000] = row
    x, y = panel.values()
    assert x.keys() == y.keys()
    times = sorted(x)
    assert all(b-a == 1 for a, b in zip(times, times[1:]))
    results = []
    for w in [1, 5, 15, 60, 240]:
        bars = {}
        for t in times:
            if t % w or t-w+1 < times[0]:
                continue
            rows = [[p.get(k) for k in range(t-w+1, t+1)] for p in [x, y]]
            if any(r is None or r['log_return'] is None for rs in rows for r in rs):
                continue
            bars[t] = tuple(10000*math.fsum(r['log_return'] for r in rs) for rs in rows)
        for subset in ['all', 'weekday', 'weekend']:
            keys = [t for t in bars if subset == 'all' or (datetime.fromtimestamp(t*60, timezone.utc).weekday()<5) == (subset=='weekday')]
            pairs = [bars[t] for t in keys]
            forward = [(bars[t][0], bars[t+w][1]) for t in keys if t+w in bars and t+w in set(keys)]
            reverse = [(bars[t][1], bars[t+w][0]) for t in keys if t+w in bars and t+w in set(keys)]
            results.append({'minutes': w, 'subset': subset, 'n': len(pairs), 'contemporaneous_corr': corr(pairs), 'lead_n': len(forward), 'SP500_to_next_EWJ_corr': corr(forward), 'EWJ_to_next_SP500_corr': corr(reverse)})
        if w == 60:
            for day in sorted({datetime.fromtimestamp(t*60, timezone.utc).date().isoformat() for t in bars}):
                pairs = [v for t, v in bars.items() if datetime.fromtimestamp(t*60, timezone.utc).date().isoformat() == day]
                results.append({'minutes': w, 'subset': day, 'n': len(pairs), 'contemporaneous_corr': corr(pairs)})
    costs = {}
    for name, p in panel.items():
        costs[name] = {'median_spread_bps': statistics.median(r['spread_bps'] for r in p.values() if r['quote_valid']), 'zero_fraction_A': sum(r['log_return']==0 for r in p.values())/sum(r['log_return'] is not None for r in p.values()), 'valid_returns_A': sum(r['log_return'] is not None for r in p.values())}
    meta = {'start_utc': datetime.fromtimestamp(times[0]*60, timezone.utc).isoformat(), 'end_utc': datetime.fromtimestamp(times[-1]*60, timezone.utc).isoformat(), 'input_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(), 'method': 'UTC-aligned non-overlapping complete log-return bars; zeros retained; no fill; lead uses next non-overlapping bar; exploratory previously observed sample, no OOS or executable PnL claim', 'costs': costs}
    (HERE/'metadata.json').write_text(json.dumps(meta, indent=2), encoding='utf8')
    with (HERE/'correlations.csv').open('w', newline='', encoding='utf8') as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    print(json.dumps(meta, indent=2))
    for r in results:
        print(r)


if __name__ == '__main__':
    main()
