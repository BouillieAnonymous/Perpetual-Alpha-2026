"""ETH-only causal minute-grid momentum/reversal research; no orders or credentials."""
import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
import math
from pathlib import Path
import statistics

from study_pas_mean_reversion import prepare

MINUTE = 60000
CUTOFF = 1792452948184


def corr(x, y):
    return statistics.correlation(x,y) if len(x)>2 and statistics.pstdev(x) and statistics.pstdev(y) else None


def distribution(values):
    return dict(n=len(values),mean=statistics.mean(values) if values else None,
                median=statistics.median(values) if values else None,
                positive_fraction=sum(x>0 for x in values)/len(values) if values else None)


def features(quotes):
    ts=[q['t'] for q in quotes]
    grid, rejected = [], Counter()

    def asof(t):
        i=bisect_right(ts,t)-1
        return quotes[i] if i>=0 and t-ts[i]<=90000 else None

    for t in range((ts[0]//MINUTE+1)*MINUTE,ts[-1]//MINUTE*MINUTE+1,MINUTE):
        history=[asof(t-j*MINUTE) for j in range(31)]
        if not all(history):
            rejected['warmup_or_stale']+=1
            continue
        if any(b['event']>=a['event'] for a,b in zip(history,history[1:])):
            rejected['nonadvancing_book_event']+=1
            continue
        mids=[(q['bid']+q['ask'])/2 for q in history]
        returns=[math.log(a/b) for a,b in zip(mids,mids[1:])]
        sigma=statistics.stdev(returns)
        if sigma<=0:
            rejected['zero_volatility']+=1
            continue
        ret=math.log(mids[0]/mids[5])
        q=history[0]
        grid.append(dict(t=t,q=q,z=ret/(sigma*math.sqrt(5)),past5_bps=ret*10000,
                         sigma_bps=sigma*10000,spread_bps=(q['ask']-q['bid'])/mids[0]*10000,
                         liquidity=min(q['sizes'])*mids[0]))
    return grid,dict(rejected)


def timing(point, quotes, ts, horizon):
    i=bisect_left(ts,point['t']+1000)
    if i>=len(quotes) or ts[i]-point['t']>90000:
        return None
    j=bisect_left(ts,ts[i]+horizon*MINUTE)
    if j>=len(quotes) or ts[j]-(ts[i]+horizon*MINUTE)>90000:
        return None
    return i,j


def fit(rows):
    x,y=[r['z'] for r in rows],[r['future_bps'] for r in rows]
    if len(x)<30 or not statistics.pvariance(x):
        return dict(n=len(x),intercept=None,beta=None)
    beta=sum((a-statistics.mean(x))*(b-statistics.mean(y)) for a,b in zip(x,y))/sum((a-statistics.mean(x))**2 for a in x)
    return dict(n=len(x),intercept=statistics.mean(y)-beta*statistics.mean(x),beta=beta)


def relationship(rows, field='z'):
    return dict(n=len(rows),pearson=corr([r[field] for r in rows],[r['future_bps'] for r in rows]),
                future_mid=distribution([r['future_bps'] for r in rows]),
                momentum_signed_mid=distribution([(1 if r['z']>0 else -1)*r['future_bps'] for r in rows if abs(r['z'])>=1]))


def simulate(points,quotes,model,horizon,end,kind,fee=9,slip=2,filtered=False):
    ts=[q['t'] for q in quotes]
    trades,counts=[],Counter()
    busy=-1
    for p in points:
        if p['t']>end-(horizon*MINUTE+180000):
            counts['window_end_embargo']+=1
            continue
        if p['t']<=busy or abs(p['z'])<1:
            continue
        side=(1 if p['z']>0 else -1)*(1 if kind=='momentum' else -1)
        if filtered:
            if model['beta'] is None:
                counts['model_unavailable']+=1
                continue
            pred=side*(model['intercept']+model['beta']*p['z'])
            cost=p['spread_bps']+2*fee+2*slip+p['q']['funding']*horizon/60*10000+5
            if pred<=cost:
                counts['expected_cost_block']+=1
                continue
        pair=timing(p,quotes,ts,horizon)
        if pair is None:
            counts['missing_entry_or_exit_timing']+=1
            continue
        i,j=pair
        a=quotes[i]
        touch=a['ask'] if side==1 else a['bid']
        entry=touch*(1+side*slip/10000)
        step=Decimal(10)**(-a['decimals'])
        qty=float((Decimal('100')/Decimal(str(entry))/step).to_integral_value(rounding=ROUND_FLOOR)*step)
        if qty*entry<10 or qty>a['sizes'][1 if side==1 else 0]:
            counts['entry_depth_or_minimum']+=1
            continue
        # Entry is accepted independently of future exit depth; wait until depth permits.
        while j<len(quotes) and qty>quotes[j]['sizes'][0 if side==1 else 1]:
            j+=1
        if j>=len(quotes) or quotes[j]['t']>end:
            counts['unresolved_exit']+=1
            busy=end
            break
        b=quotes[j]
        close_touch=b['bid'] if side==1 else b['ask']
        exit=close_touch*(1-side*slip/10000)
        gross=side*qty*(close_touch-touch)
        fees=qty*(entry+exit)*fee/10000
        friction=gross-side*qty*(exit-entry)
        funding=qty*max(entry,exit)*max(q['funding'] for q in quotes[i:j+1])*(b['t']-a['t'])/3600000
        net=gross-fees-friction-funding
        trades.append(dict(decision_ms=p['t'],entry_ms=a['t'],exit_ms=b['t'],side=side,z=p['z'],
                           qty=qty,entry=entry,exit=exit,entry_touch=touch,exit_touch=close_touch,
                           gross_touch=gross,fees=fees,slippage=friction,funding_stress=funding,net=net))
        busy=b['t']
    return dict(n=len(trades),net=sum(t['net'] for t in trades),
                gross_touch=sum(t['gross_touch'] for t in trades),fees=sum(t['fees'] for t in trades),
                slippage=sum(t['slippage'] for t in trades),funding_stress=sum(t['funding_stress'] for t in trades),
                win_rate=sum(t['net']>0 for t in trades)/len(trades) if trades else None,
                counters=dict(counts),trades=trades)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input',type=Path)
    ap.add_argument('--external',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    quotes,manifest=prepare(args.input)
    quotes=[q for q in quotes if q['symbol']=='pas:ETH']
    grid,quality=features(quotes)
    ts=[q['t'] for q in quotes]
    end=ts[-1]
    external=json.loads(args.external.read_text(encoding='utf8'))
    candles={((c['T']+1)//MINUTE)*MINUTE:float(c['c']) for c in external['payload'] if c['s']=='ETH' and c['i']=='1m'}
    result=dict(schema='eth-momentum-research/1',manifest=manifest,quality=quality,grid_points=len(grid),
                cutoff_ms=CUTOFF,external=dict(rows=len(candles),endpoint=external['endpoint'],
                requested_ms=external['requested_ms'],received_ms=external['received_ms'],
                file_sha256=hashlib.sha256(args.external.read_bytes()).hexdigest(),
                status='event_time_only_no_historical_receipts_no_C_executable_backtest'),horizons={})
    for h in (5,10):
        labels=[]
        for p in grid:
            pair=timing(p,quotes,ts,h)
            if not pair:
                continue
            i,j=pair
            a,b=quotes[i],quotes[j]
            row={k:v for k,v in p.items() if k!='q'}
            row.update(entry_ms=a['t'],exit_ms=b['t'],
                       future_bps=math.log((b['bid']+b['ask'])/(a['bid']+a['ask']))*10000)
            for lag in (0,1,2):
                t=p['t']-lag*MINUTE
                row['external_lag'+str(lag)]=(math.log(candles[t]/candles[t-5*MINUTE])*10000
                                            if t in candles and t-5*MINUTE in candles else None)
            labels.append(row)
        training=[r for r in labels if r['exit_ms']<CUTOFF]
        validation=[r for r in labels if r['t']>CUTOFF]
        model=fit(training)
        medians={k:statistics.median(r[k] for r in training) for k in ('sigma_bps','spread_bps','liquidity')}
        groups={}
        for k,median in medians.items():
            for low in (True,False):
                subset=[r for r in validation if (r[k]<=median)==low]
                groups[k+('_low' if low else '_high')]=relationship(subset)
        external_stats={}
        for lag in (0,1,2):
            field='external_lag'+str(lag)
            subset=[r for r in validation if r[field] is not None]
            gaps=[r[field]-r['past5_bps'] for r in subset]
            unreflected=[r for r in subset if abs(r[field])>=5 and
                         (1 if r[field]>0 else -1)*(r[field]-r['past5_bps'])>=5]
            external_stats[field]=dict(n=len(subset),
                future_pas_pearson=corr([r[field] for r in subset],[r['future_bps'] for r in subset]),
                contemporaneous_pas_pearson=corr([r[field] for r in subset],[r['past5_bps'] for r in subset]),
                external_minus_local_future_pearson=corr(gaps,[r['future_bps'] for r in subset]),
                event_time_unreflected_5bps=distribution([(1 if r[field]>0 else -1)*r['future_bps']
                                                         for r in unreflected]))
        sims={}
        for split,points,boundary in [('development',[p for p in grid if p['t']<CUTOFF],CUTOFF),
                                      ('new_period',[p for p in grid if p['t']>CUTOFF],end)]:
            for kind in ('momentum','reversal'):
                for label,fee,slip,filtered in [('raw-base',9,2,False),('filtered-base',9,2,True),
                                                ('raw-double',18,4,False),('raw-zero',0,0,False)]:
                    sims[split+'-'+kind+'-'+label]=simulate(points,quotes,model,h,boundary,kind,fee,slip,filtered)
        result['horizons'][str(h)]=dict(model=model,training=relationship(training),
            new_period=relationship(validation),
            nonoverlap_new=relationship([r for r in validation if r['t']//MINUTE%10==0]),
            train_group_medians=medians,new_period_groups=groups,external_event_time=external_stats,
            simulations=sims,labels=labels)
    result['source_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
        (Path(__file__),Path('scripts/study_pas_mean_reversion.py'),args.output/'protocol.zh-CN.md')}
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'results.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf8')
    for h,r in result['horizons'].items():
        print('HORIZON',h,'TRAIN',r['training'],'NEW',r['new_period'],'MODEL',r['model'])
        print('EXTERNAL',r['external_event_time'])
        for k,v in r['simulations'].items():
            print(k,v['n'],round(v['net'],6),v['win_rate'],v['counters'])


if __name__=='__main__':
    main()
