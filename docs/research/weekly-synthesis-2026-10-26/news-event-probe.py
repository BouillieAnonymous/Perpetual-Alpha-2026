"""Read-only, retrospective event-window diagnostic; run on the VPS via SSH stdin.

No news was captured live. Event times are publisher/scheduled timestamps,
not measured availability. This script does not establish prediction or causality.
"""
import bisect
import datetime as dt
import hashlib
import json
import sqlite3

DB = '/opt/perpetual-alpha-pas-data/pas/public.sqlite3'
EVENTS = [
    ('japan-cpi', '2026-10-22T23:30:00+00:00', 'Scheduled 08:30 JST national CPI release'),
    ('eurozone-pmi', '2026-10-23T08:00:00+00:00', '08:00 GMT contemporary release report'),
    ('purr-share-buyback', '2026-10-23T20:15:00+00:00', 'Issuer PRNewswire timestamp 16:15 EDT'),
]

def stamp(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat()

c = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
c.execute('PRAGMA query_only=ON')
c.execute('BEGIN')
seq = c.execute('SELECT max(seq) FROM observations').fetchone()[0]
streams = {}
digest = hashlib.sha256()
for row in c.execute("SELECT seq,symbol,requested_ms,received_ms,stored_ms,event_ms,raw_json,sha256 FROM observations WHERE kind='book' AND quality='ready' AND seq<=? ORDER BY seq", (seq,)):
    ident, symbol, requested, received, stored, event, raw, sha = row
    digest.update(f'{ident}:{sha}\n'.encode())
    available = max(requested, received, stored)
    if event is None or event > available or available - event > 90000:
        continue
    book = json.loads(raw)
    try:
        bid = float(book['levels'][0][0]['px'])
        ask = float(book['levels'][1][0]['px'])
        if not 0 < bid <= ask:
            continue
        streams.setdefault(symbol, []).append((available, event, bid, ask, ident))
    except (KeyError, IndexError, TypeError, ValueError):
        continue
c.rollback()
c.close()
for stream in streams.values():
    stream.sort()

def asof(stream, times, target):
    i = bisect.bisect_right(times, target) - 1
    if i < 0:
        return None
    x = stream[i]
    if target - x[0] > 90000 or target - x[1] > 90000:
        return None
    return {'available_utc': stamp(x[0]), 'event_utc': stamp(x[1]),
            'seq': x[4], 'bid': x[2], 'ask': x[3], 'mid': (x[2] + x[3]) / 2,
            'spread_bps': (x[3] - x[2]) / ((x[2] + x[3]) / 2) * 10000}

results = []
for name, when, provenance in EVENTS:
    t = int(dt.datetime.fromisoformat(when).timestamp() * 1000)
    for symbol, stream in sorted(streams.items()):
        times = [x[0] for x in stream]
        points = {str(m): asof(stream, times, t + m * 60000)
                  for m in [-60, 0, 2, 15, 60, 240, 1440]}
        anchor = points['0']
        returns = {m: ((p['mid'] / anchor['mid'] - 1) * 10000
                       if p and anchor else None) for m, p in points.items()}
        # Only price moves, no execution/PnL model or assumed direction.
        delayed = {m: ((points[m]['mid'] / points['2']['mid'] - 1) * 10000
                       if points[m] and points['2'] else None)
                   for m in ['15', '60', '240', '1440']}
        results.append({'event': name, 'event_utc': when, 'time_basis': provenance,
                        'symbol': symbol, 'points': points,
                        'mid_return_from_event_bps': returns,
                        'mid_return_from_hypothetical_2m_delay_bps': delayed})
print(json.dumps({'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'database': DB, 'snapshot_max_seq': seq,
                  'scanned_book_seq_sha256': digest.hexdigest(),
                  'limitations': ['Retrospective hand-selected three events; no statistical inference',
                                  'No archived pre-release expectations or historical news receipt times',
                                  'Publisher time may differ from first dissemination',
                                  'Mid returns exclude spread, fees, slippage, funding and API cost',
                                  'Overlapping windows and common factors are not independent samples',
                                  'Data prefix fingerprint is not a saved complete raw input snapshot'],
                  'results': results}, ensure_ascii=False))
