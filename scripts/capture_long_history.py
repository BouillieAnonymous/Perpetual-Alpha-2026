"""Reuse frozen archives; download bounded free own-market funding and HIP3 prices."""
import gzip
import json
import time
import urllib.request

from capture_candidate_validation import append, request
from long_history_protocol import OUT, RESEARCH

HOUR = 3600000


def capture():
    protocol = json.loads((OUT/'protocol.json').read_text(encoding='utf-8'))
    end = protocol['history_end_ms']
    path = OUT/'additional-public-history.jsonl.gz'
    if path.exists():
        raise FileExistsError('Do not overwrite captures')
    for dex in ['', 'xyz']:
        append(path, request('mainnet', {'type': 'metaAndAssetCtxs', **({'dex': dex} if dex else {})}))
    append(path, request('par', {'type': 'metaAndAssetCtxs', 'dex': 'par'}))
    # BTC/ETH candles remain the original archived six-month source, with new OWN funding.
    with gzip.open(RESEARCH/'external-transfer/external-history.jsonl.gz', 'rt', encoding='utf-8') as stream:
        originals = [r for r in map(json.loads, stream) if r['request']['type'] == 'candleSnapshot']
    for asset, (_, coin) in protocol['mapping_candidates'].items():
        if asset in {'EWY', 'HYPE'}:
            continue  # existing candidate-validation archive includes complete own funding/price
        if asset in {'BTC', 'ETH'}:
            relevant = [r['request']['req'] for r in originals if r['request']['req']['coin'] == asset]
            start = min(r['startTime'] for r in relevant)
            stop = max(r['endTime'] for r in relevant)+1
        else:
            start, stop = end-4998*HOUR, end
            append(path, request('mainnet', {'type': 'candleSnapshot', 'req': {'coin': coin, 'interval': '1h',
                                                                         'startTime': start, 'endTime': stop-1}}))
        for left in range(start, stop, 480*HOUR):
            row = request('mainnet', {'type': 'fundingHistory', 'coin': coin,
                                     'startTime': left, 'endTime': min(stop, left+480*HOUR)-1})
            append(path, row)
            print(asset, 'funding', row.get('error', len(row.get('payload', []))), flush=True)
            time.sleep(.2)
    # Real-market alternatives are context/quality evidence, not substituted candles or funding.
    attempts = []
    for asset, ticker in {'EWJ': 'EWJ', 'BRENT': 'BZ=F', 'GOLD': 'GC=F', 'USDJPY': 'JPY=X',
                          'BTC': 'BTC-USD', 'ETH': 'ETH-USD', 'SP500': '^GSPC', 'USDEUR': 'EURUSD=X'}.items():
        for interval, start in [('1d', 946684800), ('1h', end//1000-729*86400)]:
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={start}&period2={end//1000}&interval={interval}&events=div%2Csplits'
            row = {'asset': asset, 'ticker': ticker, 'interval': interval, 'url': url, 'started_ms': time.time_ns()//1000000}
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as response:
                    raw = response.read(16*1024*1024+1)
                if len(raw) > 16*1024*1024:
                    raise ValueError('Byte budget')
                with gzip.open(OUT/f'context-{asset}-{interval}.json.gz', 'wb') as stream:
                    stream.write(raw)
                row.update(received_ms=time.time_ns()//1000000, bytes=len(raw))
            except Exception as error:
                row.update(error=type(error).__name__+': '+str(error), received_ms=time.time_ns()//1000000)
            attempts.append(row)
            print(asset, interval, row.get('error', row.get('bytes')), flush=True)
    (OUT/'context-fetch.json').write_text(json.dumps(attempts, indent=2)+'\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    capture()
