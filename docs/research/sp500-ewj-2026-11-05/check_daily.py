"""Fixed 24/48h exploratory checks on existing PAS data; no tuning or orders."""
import csv
import gzip
import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

from analyze import SOURCE, corr

HERE = Path(__file__).resolve().parent


def main():
    manifest = json.loads((SOURCE.parent / 'manifest.json').read_text())
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == manifest[SOURCE.name]
    data = {'pas:SP500': {}, 'pas:EWJ': {}}
    with gzip.open(SOURCE, 'rt', encoding='utf8') as f:
        for line in f:
            r = json.loads(line)
            if r['symbol'] in data:
                data[r['symbol']][r['decision_ms']//60000] = r
    x, y = data.values()
    times = sorted(x)
    assert times == sorted(y)
    assert len(times) == times[-1]-times[0]+1
    prefixes = []
    for p in [x, y]:
        pref = [0]
        for t in times:
            pref.append(pref[-1] + int(p[t]['log_return'] is not None))
        prefixes.append(pref)

    def ret(a, b):
        if a not in x or b not in x:
            return None
        if any(not p[t]['quote_valid'] for p in [x, y] for t in [a, b]):
            return None
        coverage = min((pref[b-times[0]+1]-pref[a-times[0]+1])/(b-a) for pref in prefixes)
        if coverage < .99:
            return None
        return tuple(10000*math.log(p[b]['mid']/p[a]['mid']) for p in [x, y])

    output = []
    events = []
    for h in [24, 48]:
        w = h*60
        for step in [60, w]:
            same = []
            forward = []
            reverse = []
            own = []
            gap = []
            for t in times:
                if t % step:
                    continue
                past = ret(t-w, t)
                future = ret(t, t+w)
                if past:
                    same.append(past)
                if past and future:
                    forward.append((past[0], future[1]))
                    reverse.append((past[1], future[0]))
                    own.append((past[1], future[1]))
                    gap.append((past[1]-past[0], future[1]-future[0]))
                    if step == w:
                        events.append({'horizon_hours': h, 'decision_utc': datetime.fromtimestamp(t*60, timezone.utc).isoformat(), 'past_SP500_bps': past[0], 'past_EWJ_bps': past[1], 'future_SP500_bps': future[0], 'future_EWJ_bps': future[1], 'gap_bps': past[1]-past[0], 'future_relative_bps': future[1]-future[0]})
            output.append({'horizon_hours': h, 'sample_every_hours': step//60, 'same_n': len(same), 'same_corr': corr(same), 'predictive_n': len(forward), 'SP500_past_to_EWJ_future': corr(forward), 'EWJ_past_to_SP500_future': corr(reverse), 'EWJ_own_past_to_future': corr(own), 'gap_to_future_relative': corr(gap), 'gap_reversal_fraction': statistics.mean(a*b<0 for a,b in gap) if gap else None})
    for name, rows in [('daily-horizons.csv', output), ('daily-events.csv', events)]:
        with (HERE/name).open('w', newline='', encoding='utf8') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps(output, indent=2))
    print(json.dumps(events, indent=2))


if __name__ == '__main__':
    main()
