"""All PAS assets: fixed local momentum/reversal plus event-time external diagnostics."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

from study_eth_momentum import CUTOFF, MINUTE, corr, distribution, features, fit, relationship, simulate, timing
from study_pas_mean_reversion import prepare


def external_summary(rows, field):
    rows=[r for r in rows if r[field] is not None]
    unreflected=[r for r in rows if abs(r[field])>=5 and
                 (1 if r[field]>0 else -1)*(r[field]-r['past5_bps'])>=5]
    return dict(n=len(rows),future_pas_pearson=corr([r[field] for r in rows],[r['future_bps'] for r in rows]),
                contemporaneous_pas_pearson=corr([r[field] for r in rows],[r['past5_bps'] for r in rows]),
                residual_future_pearson=corr([r[field]-r['past5_bps'] for r in rows],[r['future_bps'] for r in rows]),
                event_time_unreflected=distribution([(1 if r[field]>0 else -1)*r['future_bps'] for r in unreflected]))


def study(quotes, external):
    grid,quality=features(quotes)
    ts=[q['t'] for q in quotes]
    if 'error' in external:
        candles={}
    else:
        payload=external['payload']
        assert hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()==external['payload_sha256']
        candles={((c['T']+1)//MINUTE)*MINUTE:float(c['c']) for c in payload
                 if c['s']==external['external_coin'] and c['i']=='1m'}
    # A common price scale is required; do not silently invert FX instruments.
    ratios=[p['q']['oracle']/candles[p['t']] for p in grid if p['t'] in candles]
    ratio=statistics.median(ratios) if ratios else None
    units_ok=ratio is not None and .5<ratio<2
    result=dict(grid_points=len(grid),quality=quality,external_coin=external['external_coin'],
                external_candles=len(candles),oracle_over_external_median=ratio,
                external_units_ok=units_ok,horizons={})
    for h in (5,10):
        labels=[]
        for p in grid:
            pair=timing(p,quotes,ts,h)
            if not pair:
                continue
            i,j=pair; a,b=quotes[i],quotes[j]
            row={k:v for k,v in p.items() if k!='q'}
            row.update(entry_ms=a['t'],exit_ms=b['t'],
                       future_bps=math.log((b['bid']+b['ask'])/(a['bid']+a['ask']))*10000)
            for lag in (0,1,2):
                t=p['t']-lag*MINUTE
                row['external_lag'+str(lag)]=(math.log(candles[t]/candles[t-5*MINUTE])*10000
                                             if units_ok and t in candles and t-5*MINUTE in candles else None)
            labels.append(row)
        train=[r for r in labels if r['exit_ms']<CUTOFF]
        new=[r for r in labels if r['t']>CUTOFF]
        model=fit(train)
        medians={k:statistics.median(r[k] for r in train) for k in ('sigma_bps','spread_bps','liquidity')}
        groups={}
        for k,median in medians.items():
            for low in (True,False):
                subset=[r for r in new if (r[k]<=median)==low]
                groups[k+('_low' if low else '_high')]=relationship(subset)
        sims={}
        for split,points,boundary in [('development',[p for p in grid if p['t']<CUTOFF],CUTOFF),
                                      ('new_period',[p for p in grid if p['t']>CUTOFF],ts[-1])]:
            for kind in ('momentum','reversal'):
                for label,fee,slip,filtered in [('raw-base',9,2,False),('filtered-base',9,2,True),
                                                ('raw-double',18,4,False),('raw-zero',0,0,False)]:
                    sims[split+'-'+kind+'-'+label]=simulate(points,quotes,model,h,boundary,kind,fee,slip,filtered)
        result['horizons'][str(h)]=dict(model=model,training=relationship(train),new_period=relationship(new),
             nonoverlap_new=relationship([r for r in new if r['t']//MINUTE%10==0]),
             train_group_medians=medians,new_period_groups=groups,
             external_event_time={str(lag):external_summary(new,'external_lag'+str(lag)) for lag in (0,1,2)},
             simulations=sims,labels=labels)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--external',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    quotes,manifest=prepare(args.input)
    ext=json.loads(args.external.read_text(encoding='utf8'))
    mapping={r['pas_symbol']:r for r in ext}
    assets={}
    for s in sorted({q['symbol'] for q in quotes}):
        assets[s]=study([q for q in quotes if q['symbol']==s],mapping[s])
        print(s,'external scale ratio',assets[s]['oracle_over_external_median'],flush=True)
        for h,r in assets[s]['horizons'].items():
            print(h,[(k,v['n'],round(v['net'],4)) for k,v in r['simulations'].items()
                     if k.startswith('new_period') and k.endswith('base')],flush=True)
    result=dict(schema='all-asset-momentum-research/1',manifest=manifest,cutoff_ms=CUTOFF,
                external_file_sha256=hashlib.sha256(args.external.read_bytes()).hexdigest(),
                external_provenance=[{k:v for k,v in row.items() if k!='payload'} for row in ext],
                external_status='retrospective_event_time_only_no_executable_C_results',assets=assets)
    result['source_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        Path(__file__),Path('scripts/study_eth_momentum.py'),Path('scripts/study_pas_mean_reversion.py'),
        args.output/'protocol.zh-CN.md')}
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'results.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf8')
    with (args.output/'scenarios.csv').open('w',encoding='utf8',newline='') as f:
        fields=['n','gross_touch','fees','slippage','funding_stress','net','win_rate']
        w=csv.writer(f); w.writerow(['symbol','horizon_minutes','scenario']+fields)
        for s,a in assets.items():
            for h,r in a['horizons'].items():
                for k,v in r['simulations'].items():
                    w.writerow([s,h,k]+[v[x] for x in fields])
    with (args.output/'trades.csv').open('w',encoding='utf8',newline='') as f:
        fields=['decision_ms','entry_ms','exit_ms','side','z','qty','entry','exit','entry_touch','exit_touch',
                'gross_touch','fees','slippage','funding_stress','net']
        w=csv.writer(f); w.writerow(['symbol','horizon_minutes','scenario']+fields)
        for s,a in assets.items():
            for h,r in a['horizons'].items():
                for k,v in r['simulations'].items():
                    for t in v['trades']:
                        w.writerow([s,h,k]+[t[x] for x in fields])


if __name__=='__main__':
    main()
