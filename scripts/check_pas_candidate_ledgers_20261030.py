"""Independently verify fills against frozen raw books and accounting."""
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path('docs/research/candidate-validation-2026-10-30')
paths = [ROOT / 'ledgers.jsonl.gz', ROOT / 'complete-window/ledgers.jsonl.gz']
ledgers = []
for path in paths:
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        ledgers.extend(json.loads(line) for line in f)
needed = {f['seq'] for r in ledgers for f in r['fills']}
raw = {}
with gzip.open('.cache/validation-2026-10-30/pas-prefix.jsonl.gz', 'rt', encoding='utf-8') as f:
    next(f)
    for line in f:
        r = json.loads(line)
        if r['seq'] in needed:
            raw[r['seq']] = r
assert set(raw) == needed
fills = 0
for ledger in ledgers:
    previous = 0.0
    for f in ledger['fills']:
        row = raw[f['seq']]
        book = json.loads(row['raw_json'])
        bid, ask = book['levels']
        assert ledger['symbol'] == row['symbol'] == book['coin']
        assert f['time_ms'] == max(row[k] for k in ['requested_ms', 'received_ms', 'stored_ms'])
        assert 0 <= f['time_ms'] - book['time'] <= 90000
        assert f['bid'] == float(bid[0]['px']) and f['ask'] == float(ask[0]['px'])
        assert abs(f['delta'] - (f['target'] - previous)) < 1e-10
        side = ask if f['delta'] > 0 else bid
        assert abs(f['delta']) <= float(side[0]['sz']) + 1e-12
        price = float(side[0]['px']) * (1 + (1 if f['delta'] > 0 else -1) * f['slip_bps'] / 10000)
        assert abs(price - f['price']) < 1e-10
        assert abs(abs(f['delta'] * price) * f['fee_bps'] / 10000 - f['fee']) < 1e-10
        previous = f['target']
        fills += 1
    assert abs(previous - ledger['summary']['unclosed_quantity']) < 1e-10
result = dict(ledgers=len(ledgers), fills=fills, distinct_raw_books=len(raw), passed=True,
              source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              final_runner_sha256=hashlib.sha256(Path('scripts/validate_pas_candidates_20261030.py').read_bytes()).hexdigest(),
              ledger_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
(ROOT / 'independent-validation.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result))
