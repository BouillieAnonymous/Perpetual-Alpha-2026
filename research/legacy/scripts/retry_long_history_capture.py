"""Bounded slow retries; retain original failures and never broaden requested windows."""
import json
import time

from capture_candidate_validation import append, request
from long_history_protocol import OUT
from run_candidate_validation import records


def retry():
    path = OUT/'additional-public-history.jsonl.gz'
    rows = list(records(path))
    good = {json.dumps(r['request'], sort_keys=True) for r in rows if not r.get('error')}
    failed = {json.dumps(r['request'], sort_keys=True): r['request'] for r in rows if r.get('error')}
    for key, payload in failed.items():
        if key in good:
            continue
        for attempt in range(2):
            time.sleep(2)
            row = request('mainnet', payload)
            row['retry_attempt'] = attempt+1
            append(path, row)
            print(payload.get('coin', payload.get('req', {}).get('coin')), row.get('error', len(row.get('payload', []))), flush=True)
            if not row.get('error'):
                break
            if '429' in row['error'] and attempt == 0:
                time.sleep(45)
            else:
                break


if __name__ == '__main__':
    retry()
