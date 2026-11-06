"""Bounded public EWY/HYPE information requests; never account/exchange routes."""
import argparse
import gzip
import hashlib
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from perpetual_alpha.historical.client import NoRedirect

HOUR = 3600000
ORIGINS = {'mainnet': 'https://api.hyperliquid.xyz/info',
           'par': 'https://api.hyperliquid-testnet.xyz/info'}
ALLOWED = {'meta', 'metaAndAssetCtxs', 'candleSnapshot', 'fundingHistory', 'l2Book', 'recentTrades',
           'perpDexs', 'spotMeta'}


def request(venue, payload):
    if venue not in ORIGINS or payload.get('type') not in ALLOWED:
        raise ValueError('Fixed public information routes only')
    record = {'venue': venue, 'request': payload, 'started_ms': time.time_ns()//1000000}
    tick = time.monotonic_ns()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        req = urllib.request.Request(ORIGINS[venue], json.dumps(payload).encode(),
                                     {'Content-Type': 'application/json'})
        with opener.open(req, timeout=15) as response:
            raw = response.read(8*1024*1024+1)
        record['received_ms'] = time.time_ns()//1000000
        if len(raw) > 8*1024*1024:
            raise ValueError('Response byte budget')
        data = json.loads(raw)
        # Public market trades contain counterparty addresses; they add no research value.
        if payload['type'] == 'recentTrades':
            data = [{k: v for k, v in x.items() if k != 'users'} for x in data]
            record['redaction'] = 'counterparty users removed before archival'
        record.update(payload=data, available_ms=time.time_ns()//1000000,
                      payload_sha256=hashlib.sha256(json.dumps(data, sort_keys=True,
                                                  separators=(',', ':')).encode()).hexdigest())
    except Exception as error:
        record.update(error=type(error).__name__+': '+str(error), received_ms=time.time_ns()//1000000)
    record['round_trip_ms'] = (time.monotonic_ns()-tick)/1e6
    return record


def append(path, row):
    with gzip.open(path, 'at', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(row, allow_nan=False)+'\n')


def capture(output):
    output.mkdir(parents=True, exist_ok=True)
    if (output/'protocol.json').exists():
        raise FileExistsError('Fresh session required')
    freeze = time.time_ns()//1000000
    end = freeze//HOUR*HOUR
    protocol = {'freeze_ms': freeze, 'parent_commit': 'c69f76afbdc7bb82ccb0bc878848c1ab2bc265b6',
                'rules': 'unchanged ATR/Donchian lookback24 ATR1.5 hold12; FUNDING-MEAN24 rebalance12 target -0.2sign(mean24)',
                'primary_interval': '1h', 'history_start_ms': end-4998*HOUR, 'history_end_ms': end,
                'splits': '60/20/20 chronological +12h gap; downloaded past is retrospective, not prospective',
                'funding_controls': ['same-sign-funding', 'momentum24-exposure-matched', 'fixed-long', 'fixed-short', 'no-trade'],
                'conditional_model': 'OLS 12h forward executable-open return ~ intercept + past24h log momentum + funding direction; HAC lag2; chronological fit only',
                'no_parameter_search': True, 'prospective_eligible': 'decision availability AFTER freeze_ms only; historical response availability is current receipt, never backdated',
                'etf_daily': 'long-run context only; cannot substitute daily bars for frozen hourly rule',
                'quote_probe_seconds': 120, 'quote_probe_interval_seconds': 5}
    (output/'protocol.json').write_text(json.dumps(protocol, indent=2)+'\n', encoding='utf-8', newline='\n')
    tasks = []
    for venue, coin, dex in [('mainnet', 'HYPE', ''), ('mainnet', 'xyz:EWY', 'xyz'),
                              ('par', 'par:HYPE', 'par'), ('par', 'par:EWY', 'par')]:
        tasks += [(venue, {'type': 'metaAndAssetCtxs', **({'dex': dex} if dex else {})}),
                  (venue, {'type': 'recentTrades', 'coin': coin})]
        for interval, start in [('1h', end-4998*HOUR), ('1d', end-1000*24*HOUR)]:
            tasks.append((venue, {'type': 'candleSnapshot', 'req': {'coin': coin, 'interval': interval,
                          'startTime': start, 'endTime': end-1}}))
        for left in range(end-4998*HOUR, end, 480*HOUR):
            tasks.append((venue, {'type': 'fundingHistory', 'coin': coin, 'startTime': left,
                                  'endTime': min(end, left+480*HOUR)-1}))
    path = output/'public-history.jsonl.gz'
    for venue, payload in tasks:
        row = request(venue, payload)
        append(path, row)
        print(venue, payload['type'], payload.get('coin', payload.get('req', {}).get('coin', 'meta')),
              row.get('error', len(row.get('payload', []))), flush=True)
        time.sleep(.25)
    session = 'ewy-hype-'+str(freeze)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for index in range(24):
            started = time.monotonic()
            for future in [pool.submit(request, venue, {'type': 'l2Book', 'coin': coin})
                           for venue, coin in [('mainnet', 'HYPE'), ('mainnet', 'xyz:EWY'),
                                               ('par', 'par:HYPE'), ('par', 'par:EWY')]]:
                row = future.result()
                row.update(session=session, round=index)
                append(output/'quote-probe.jsonl.gz', row)
            time.sleep(max(0, 5-(time.monotonic()-started)))


def audit_venues(output):
    """New official venue is audited separately, never injected into old PAR study."""
    output.mkdir(parents=True, exist_ok=True)
    if (output/'venue-identity-audit.jsonl.gz').exists():
        raise FileExistsError('Preserve original source-identity vintage')
    end = time.time_ns()//1000000//HOUR*HOUR
    for payload in [{'type': 'metaAndAssetCtxs', 'dex': 'pas'}, {'type': 'perpDexs'}, {'type': 'spotMeta'}]:
        row = request('par', payload)
        row['venue'] = 'testnet-source-audit'
        append(output/'venue-identity-audit.jsonl.gz', row)
    for coin in ['pas:EWY', 'pas:HYPE']:
        for payload in [
            {'type': 'candleSnapshot', 'req': {'coin': coin, 'interval': '1h',
             'startTime': end-4998*HOUR, 'endTime': end-1}},
            {'type': 'fundingHistory', 'coin': coin, 'startTime': end-48*HOUR, 'endTime': end-1},
            {'type': 'l2Book', 'coin': coin}
        ]:
            row = request('par', payload)
            row['venue'] = 'pas-discovery-only'
            append(output/'pas-discovery.jsonl.gz', row)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--venue-audit', action='store_true')
    args = parser.parse_args()
    audit_venues(args.output) if args.venue_audit else capture(args.output)
