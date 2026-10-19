"""Create a deterministic SYNTHETIC collector-schema database, never a market-data download."""
import argparse
import hashlib
from pathlib import Path

from perpetual_alpha.venue_collection import Capture, canonical
from perpetual_alpha.venues import venue

START = 1_792_368_000_000  # 2026-10-19 00:00 UTC


def append(store, kind, symbol, raw, received, quality='observed', event=None, latency=100, stored=None):
    request = {'type': {'book': 'l2Book', 'context': 'metaAndAssetCtxs',
                        'settled-funding': 'fundingHistory'}.get(kind, 'l2Book')}
    request.update({'dex': store.selected.dex} if kind == 'context' else {'coin': symbol})
    payload = canonical(raw)
    store.db.execute('INSERT INTO observations VALUES(NULL,?,?,?,?,?,?,?,?,?,?)',
                     (kind, symbol, received-latency, received, stored if stored is not None else received+1,
                      event, quality, canonical(request), payload, hashlib.sha256(payload.encode()).hexdigest()))


def book(symbol, event, mid=100, empty=False):
    return {'coin': symbol, 'time': event, 'levels': [[], []] if empty else
            [[{'px': str(mid-0.1), 'sz': '2'}], [{'px': str(mid+0.1), 'sz': '3'}]]}


def create(path):
    path = Path(path)
    if path.exists():
        raise ValueError('Synthetic example requires a new path; never overwrite a database')
    store = Capture(path, 'pas')
    try:
        for minute in range(12):
            for index, symbol in enumerate(venue('pas').symbols):
                if symbol == 'pas:USDJPY':  # completely missing market
                    continue
                if symbol == 'pas:EWJ' and minute in (4, 5, 6):
                    continue
                received = START+minute*60_000+index*100
                append(store, 'context', symbol,
                       {'context': {'markPx': str(100+index), 'oraclePx': '100', 'funding': '0.0001'},
                        'parent_received_ms': received}, received)
                event = received-500
                mid = 100+index+minute*0.02
                empty = symbol == 'pas:BTC' and minute == 4
                quote = book(symbol, event, mid, empty)
                if symbol == 'pas:GOLD' and minute == 5:
                    quote['levels'][0][0]['px'] = '999'
                append(store, 'book', symbol, quote, received+100,
                       'empty-or-one-sided' if empty else 'ready', event,
                       latency=6000 if symbol == 'pas:BRENT' and minute == 6 else 100)
                if minute in (0, 6):
                    append(store, 'settled-funding', symbol,
                           [{'coin': symbol, 'time': START-3_600_000, 'fundingRate': '0.0001'},
                            {'coin': symbol, 'time': START, 'fundingRate': '0.0002'}], received+200)
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    create(args.output)


if __name__ == '__main__':
    main()
