"""Immutable inputs, causality, accounting and deterministic choices/replays."""
import gzip
import hashlib
import json
from decimal import Decimal

from regime_core import choose, classify, extract, overlay
from regime_protocol import FAMILIES, OUT, PRIOR, ROOT, STATES
from run_long_history import load_sources
from run_regime_study import read_full

from perpetual_alpha.offline_quant.data import HOUR, source_hash
from perpetual_alpha.offline_quant.replay import Costs, replay
from perpetual_alpha.offline_quant.run import orders_for, source_digest


def validate():
    manifest = json.loads((OUT/'manifest.json').read_text())
    protocol = manifest['protocol']
    assert source_digest()==protocol['frozen_engine_sha256']
    assert source_hash(PRIOR/'hashes.json')==protocol['prior_hashes_sha256']
    for name, digest in json.loads((OUT/'hashes.json').read_text()).items():
        assert source_hash(OUT/name)==digest,name
    for name, digest in manifest['prior_inputs'].items():
        assert source_hash(PRIOR/name)==digest,name
    for path, digest in manifest['source_hashes_normalized_LF'].items():
        assert hashlib.sha256((ROOT/path).read_text(encoding='utf-8').encode()).hexdigest()==digest,path
    runs = json.loads((OUT/'runs.json').read_text())
    def key(i):
        return tuple(i[k] for k in ['asset','fold','mode','scenario'])
    index = {key(r):r for r in runs}
    assert len(runs)==len(index)==1200==manifest['experiment_count']
    audits = json.loads((OUT/'selection-audit.json').read_text())
    assert len(audits)==60
    for a in audits:
        assert a['train'][1]+12*HOUR<=a['validation'][0]
        assert a['validation'][1]+12*HOUR<=a['test'][0]
        assert set(a['mapping'])==set(STATES)
        assert all(v in FAMILIES+['FLAT'] for v in a['mapping'].values())
        for r in a['audit']:
            if 'train_eligible' in r and r['train_eligible']:
                assert r['train']['entry_episodes']>=30 and r['train']['entry_days']>=10
                assert r['train_ci']['lower95']>0
    def dec(x):
        return Decimal(str(x))
    seen = set()
    with gzip.open(OUT/'forward-ledgers.jsonl.gz','rt',encoding='utf-8') as stream:
        for line in stream:
            d = json.loads(line)
            k = key(d['identity'])
            assert k not in seen
            seen.add(k)
            r = index[k]
            assert all(r[n]==v for n,v in d['summary'].items())
            assert r['net_return'] is None and r['executable_net_return'] is None
            fee = sum((abs(dec(f['delta'])*dec(f['scenario_price'])) for f in d['fills']),Decimal(0))*dec(r['costs']['fee_bps'])/10000
            cash = sum((-dec(f['quantity'])*dec(f['price_proxy'])*dec(f['rate']) for f in d['funding']),Decimal(0))
            friction = sum((dec(f['friction']) for f in d['fills']),Decimal(0))
            friction_formula = sum((abs(dec(f['delta'])*dec(f['reference'])) for f in d['fills']),Decimal(0))
            friction_formula *= (dec(r['costs']['spread_bps'])/2+dec(r['costs']['slippage_bps']))/10000
            assert abs(fee-dec(r['fees']))<Decimal('1e-7')
            assert abs(cash-dec(r['funding_scenario']))<Decimal('1e-7')
            assert abs(friction-dec(r['spread_slippage_cost']))<Decimal('1e-7')
            assert abs(friction-friction_formula)<Decimal('1e-6')
            assert all(f['price_proxy_time_ms']<=f['time_ms'] for f in d['funding'])
            assert all(s['scheduled_ms']==s['time']+r['costs']['delay_hours']*HOUR for s in d['signals'])
            assert abs(float((dec(r['gross_price_pnl'])-fee-friction+cash)/10000)-r['known_components_return'])<1e-9
    assert seen==set(index)
    cells = json.loads((OUT/'conditional.json').read_text())
    registry = json.loads((PRIOR/'registry-170.json').read_text())
    allowed = {(r['asset'],r['rule']) for r in registry}
    full = [r for r in cells if r['split']=='full_retrospective' and (r['asset'],r['rule']) in allowed]
    assert len(full)==850 and {r['state'] for r in full}==set(STATES+['UNKNOWN'])
    bars,rates,specs,_,_ = load_sources()
    # Recompute all170 full allocations and causal feature prefixes from original evidence.
    for asset,series in bars.items():
        states = classify(series)
        prefix = classify(series[:500])
        assert prefix=={t:v for t,v in states.items() if t<=series[499].time}
        data = {}
        for rule,d in read_full(asset):
            h,e,tr = extract(d,states)
            if (asset,rule) in allowed:
                for state in STATES+['UNKNOWN']:
                    row = next(r for r in full if (r['asset'],r['rule'],r['state'])==(asset,rule,state))
                    actual = sum(x['net'] for x in h if x['state']==state)
                    assert abs(actual-row['net'])<1e-7
            if rule in FAMILIES:
                data[rule] = h,e,tr
        # One fold/asset mapping recomputed, including chosen validation rejection.
        a = next(a for a in audits if a['asset']==asset and a['fold']=='final_locked28d')
        mapping,audit = choose(data,a)
        assert mapping==a['mapping'] and json.loads(json.dumps(audit))==a['audit']
        # Actual forward selector and fixed rule independently rebuilt for two assets.
        if asset in {'BTC','EWJ'}:
            start,end = a['test']
            symbol = 'par:'+asset
            definitions = {r['rule']:r['definition'] for r in registry if r['asset']==asset}
            own = {symbol:rates[asset]}
            shadows = {r:[{'time':o.time,'targets':o.targets} for o in orders_for(definitions[r],{symbol:series},own,{},start,end)] for r in FAMILIES}
            for mode in ['SELECTOR','FIXED:ATR','FILTER:ROLLING_Z']:
                signal,_ = overlay(shadows,states,symbol,start,end,mode,mapping)
                row = index[asset,a['fold'],mode,'base']
                d = replay({symbol:series},own,{symbol:specs[asset]},signal,start,end,Costs(**row['costs']))
                assert all(row[n]==v for n,v in d['summary'].items())
    portfolios = json.loads((OUT/'portfolio.json').read_text())
    assert len(portfolios)==40
    for p in portfolios:
        rs = [r for r in runs if r['mode']==p['mode'] and r['scenario']==p['scenario'] and
              (r['fold']=='final_locked28d')==(p['horizon']=='locked28d')]
        assert abs(p['net_scenario_return']-sum(r['known_components_return'] for r in rs)/10)<1e-10
        assert abs(sum(p['daily'].values())-p['net_scenario_return'])<1e-10
    fold_portfolios = json.loads((OUT/'fold-portfolios.json').read_text())
    assert len(fold_portfolios)==120
    for p in fold_portfolios:
        rs = [r for r in runs if (r['fold'],r['mode'],r['scenario'])==(p['fold'],p['mode'],p['scenario'])]
        assert len(rs)==10
        assert abs(p['net_scenario_return']-sum(r['known_components_return'] for r in rs)/10)<1e-10
    print('1200 regime ledgers reconciled; all170 attributions,10 causal feature prefixes/locked mappings,6 actual replays and40 portfolios verified')


if __name__=='__main__':
    validate()
