"""Source inventory and ETF/calendar diagnostics; no synthetic historical quotes."""
import csv
import gzip
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from run_candidate_validation import OUT, ROOT, load, records, time_stats

from perpetual_alpha.offline_quant.data import HOUR, read_archive, source_hash
from perpetual_alpha.offline_quant.run import write_json


def inventory():
    source_rows, provenance = [], []
    base = ROOT.parents[1]/'.cache'
    sources = [
        (16, '5584806592149ac241dfb4ad404b5b7800798ded', base/'multi-asset-collector/docs/research/collector-2026-10-18/observations.jsonl.gz'),
        (17, '0e100aaeae1d12e01a96a553408f3b7aa1a88a1e', base/'multi-asset-basis-study/docs/research/multi-asset-2026-10-18/vps-extension.jsonl.gz'),
        (17, '0e100aaeae1d12e01a96a553408f3b7aa1a88a1e', base/'multi-asset-basis-study/docs/research/multi-asset-2026-10-18/ten-asset-probe.jsonl.gz'),
    ]
    with gzip.open(OUT/'prior-pr-inputs.jsonl.gz', 'wt', encoding='utf-8', newline='\n') as stream:
        for pr, sha, path in sources:
            data = [r for r in records(path) if r.get('symbol') in {'par:EWY', 'par:HYPE'}]
            identity = {'pr': pr, 'commit': sha, 'file': path.name, 'source_sha256': source_hash(path)}
            provenance.append(identity)
            for r in data:
                stream.write(json.dumps({'source': identity, 'observation': r})+'\n')
            for symbol in ['par:EWY', 'par:HYPE']:
                subset = [r for r in data if r['symbol'] == symbol]
                source_rows.append({'source': f'PR{pr}/'+path.name, 'symbol': symbol,
                                    'type': 'book+oracle+mark+indicative-funding+premium+OI',
                                    **time_stats([r['received_ms'] for r in subset], 60000),
                                    'book_event': time_stats([r['event_ms'] for r in subset]),
                                    'availability': 'actual local receive; oracle upstream time UNKNOWN',
                                    'supports': 'short quote checks only; no 12h strategy evidence'})
        path = base/'multi-asset-basis-study/docs/research/multi-asset-2026-10-18/official-history.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        identity = {'pr': 17, 'commit': '0e100aaeae1d12e01a96a553408f3b7aa1a88a1e', 'file': path.name,
                    'source_sha256': hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest(),
                    'hash_convention': 'UTF8 normalized LF text, matching Git blob'}
        provenance.append(identity)
        for observation in data['observations']:
            symbol = observation['request'].get('coin')
            if symbol not in {'par:EWY', 'par:HYPE'} or observation.get('error'):
                continue
            stream.write(json.dumps({'source': identity, 'observation': observation})+'\n')
            source_rows.append({'source': 'PR17/official-history.json', 'symbol': symbol,
                                'type': observation['request']['type'],
                                **time_stats([r['time'] for r in observation['payload']]),
                                'received_ms': observation['received_ms'],
                                'availability': 'retrospective exact batch receipt; participant users already omitted',
                                'supports': 'hourly realized rates/premium or ten recent trades only, not historical execution'})
    old_bars, old_funding, _, _ = read_archive(OUT.parent/'official-hourly.jsonl.gz')
    for symbol in ['par:EWY', 'par:HYPE']:
        source_rows += [
            {'source': 'PR15/official-hourly', 'symbol': symbol, 'type': 'OHLCV1h',
             **time_stats([r.time for r in old_bars[symbol]], HOUR), 'availability': 'retrospective download, actual receipt retained',
             'supports': 'price/cost scenario only'},
            {'source': 'PR15/official-hourly', 'symbol': symbol, 'type': 'realized-hourly-funding',
             **time_stats(list(old_funding[symbol]), HOUR), 'availability': 'retrospective download',
             'supports': 'rate history, settlement oracle unavailable'}]
    write_json(OUT/'prior-source-provenance.json', provenance)
    write_json(OUT/'prior-source-inventory.json', source_rows)


def etf():
    quality = {}
    zone = ZoneInfo('America/New_York')
    for name in ['yahoo-daily', 'yahoo-hourly']:
        raw = json.loads(gzip.open(OUT/(name+'.response.gz'), 'rt').read())
        if raw['chart'].get('error'):
            raise ValueError('ETF provider error')
        data = raw['chart']['result'][0]
        times, q = data['timestamp'], data['indicators']['quote'][0]
        if data['meta']['symbol'] != 'EWY' or data['meta']['exchangeTimezoneName'] != 'America/New_York':
            raise ValueError('Wrong ETF mapping')
        adj = data['indicators'].get('adjclose', [{}])[0].get('adjclose', [None]*len(times))
        usable, missing = [], 0
        for i, t in enumerate(times):
            values = {k: q[k][i] for k in ['open', 'high', 'low', 'close', 'volume']}
            if any(v is None for v in values.values()):
                missing += 1
                continue
            if not 0 < values['low'] <= min(values['open'], values['close']) <= max(values['open'], values['close']) <= values['high']:
                raise ValueError('Invalid ETF OHLC')
            usable.append({'timestamp_ms': t*1000, 'local_time': datetime.fromtimestamp(t, zone).isoformat(),
                           **values, 'adjclose': adj[i]})
        with (OUT/(name+'.csv')).open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(usable[0]), lineterminator='\n')
            writer.writeheader()
            writer.writerows(usable)
        groups = defaultdict(list)
        for r in usable:
            groups[r['local_time'][:4]].append(r)
        annual = {y: {'observations': len(g), 'close_change': g[-1]['close']/g[0]['close']-1,
                       'adjclose_change': g[-1]['adjclose']/g[0]['adjclose']-1
                       if g[-1]['adjclose'] and g[0]['adjclose'] else None}
                  for y, g in groups.items()}
        max_run, run, previous = 0, 0, None
        for row in usable:
            t = row['timestamp_ms']
            run = run+1 if previous is not None and t-previous == HOUR else 1
            max_run, previous = max(max_run, run), t
        quality[name] = {**time_stats([r['timestamp_ms'] for r in usable]), 'null_ohlcv_rows': missing,
                         'max_consecutive_exact_hours': max_run, 'dividend_events': len(data.get('events', {}).get('dividends', {})),
                         'split_events': len(data.get('events', {}).get('splits', {})), 'annual_context': annual,
                         'timezone': data['meta']['exchangeTimezoneName'], 'currency': data['meta']['currency'],
                         'timestamp_semantics': 'provider session/bar-start, NOT actual historical receive; daily not exact UTC24h grid',
                         'strategy_status': 'INSUFFICIENT_DATA: daily incompatible; regular-session hourly gaps break frozen continuous lookback',
                         'adjustment': 'raw OHLC and provider adjclose kept separate; adjclose retrospective, not point-in-time feature'}
    write_json(OUT/'etf-quality.json', quality)


def prospective():
    protocol = json.loads((OUT/'protocol.json').read_text(encoding='utf-8'))
    bars, rates, _, _ = load()
    summary, diagnostics = [], []
    probe = list(records(OUT/'quote-probe.jsonl.gz'))
    for coin in ['par:EWY', 'par:HYPE']:
        quotes = [r for r in probe if r['venue'] == 'par' and r['request']['coin'] == coin]
        good, old_age_pass, times = 0, 0, []
        for i, row in enumerate(quotes):
            if 'error' in row:
                diagnostics.append({'symbol': coin, 'round': row['round'], 'status': 'request-error'})
                continue
            book = row['payload']
            age = row['available_ms']-book['time']
            levels = book['levels']
            good += int(0 <= age <= 2000 and bool(levels[0]) and bool(levels[1]))
            old_age_pass += int(0 <= age <= 10000 and bool(levels[0]) and bool(levels[1]))
            times.append(row['available_ms'])
            # Diagnostic next received book only, never promoted to a strategy entry.
            nxt = quotes[i+1] if i+1 < len(quotes) else None
            diagnostics.append({'symbol': coin, 'round': row['round'], 'available_ms': row['available_ms'],
                                'book_event_ms': book['time'], 'age_ms': age,
                                'bid': levels[0][0] if levels[0] else None, 'ask': levels[1][0] if levels[1] else None,
                                'next_received_available_ms': nxt.get('available_ms') if nxt else None,
                                'status': 'INSUFFICIENT_DATA:no-eligible-frozen-decision-no-12h-exit'})
        series = bars['par', coin, '1h']
        # Only 12h decisions strictly after freeze; downloaded bars ending earlier are not prospective.
        decisions = [r.time for r in series if r.time > protocol['freeze_ms'] and r.time//HOUR % 12 == 0]
        summary.append({'symbol': coin, 'freeze_ms': protocol['freeze_ms'], 'quotes': len(quotes),
                        'quote_available': time_stats(times), 'fresh_nonempty_books_2s': good,
                        'old_transfer_10s_age_only_pass': old_age_pass,
                        'postfreeze_eligible_12h_decisions': len(decisions), 'complete_entry_exit_pairs': 0,
                        'status': 'INSUFFICIENT_DATA', 'executable_net_return': None,
                        'reason': 'two-minute session cannot mature frozen 12h position; received historical features not backdated'})
    write_json(OUT/'prospective.json', summary)
    write_json(OUT/'next-quote-diagnostics.json', diagnostics)


if __name__ == '__main__':
    inventory()
    etf()
    prospective()
