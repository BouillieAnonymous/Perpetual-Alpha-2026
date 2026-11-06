"""Offline ten-asset EDA; reads frozen public data, writes a new output directory."""
import argparse
import csv
import gzip
import hashlib
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PIPE = ROOT/'.cache/pas-launch-prep/src/perpetual_alpha/pas_research.py'
sys.path.insert(0,str(PIPE.parents[1]))
from perpetual_alpha import pas_research as pas
sys.path.insert(0,str(HERE.parent/'btc-eth-volatility'))
from analyze_stage02a import rolling_rv, distribution, stamp, utc

HASH = 'fc5ab7a251de06813e9ab74e366d97faee242da96ef0c0f2cd1e722eb861d898'
PREFIX = '62b88879c03aff3cf6b43561409c4bef962704fa3df0e761097df6bc9cfc338b'
PIPE_HASH = '32905565e37c052e95b680c0cd3cd46aa50fbd6c0aab950b126c9dc521539b2c'
COLORS = ['#1674b8','#e57820','#9333ea','#00856a','#cc3b60','#8d631b','#475569','#c08c00','#0094a8','#a34c91']

def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def trailing(mask,w=60):
    acc=[0]
    for v in mask: acc.append(acc[-1]+v)
    return [100*(acc[i+1]-acc[max(0,i-w+1)])/(i+1-max(0,i-w+1)) for i in range(len(mask))]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input',type=Path,default=ROOT/'.cache/validation-2026-10-30/pas-prefix.jsonl.gz')
    ap.add_argument('--output',type=Path,default=HERE/'output')
    a=ap.parse_args()
    if a.output.exists(): raise FileExistsError('Preserve evidence; choose a new output directory')
    if not a.input.exists(): raise FileNotFoundError('BLOCKED: frozen ten-asset cache missing; no fetch or synthetic fallback')
    assert digest(a.input)==HASH,'Frozen input hash mismatch'
    assert hashlib.sha256(PIPE.read_text(encoding='utf8').replace('\r\n','\n').encode()).hexdigest()==PIPE_HASH
    db=sqlite3.connect(':memory:'); db.row_factory=sqlite3.Row; db.executescript(pas.SCHEMA)
    books=defaultdict(list); prefix=hashlib.sha256(); last=0
    with gzip.open(a.input,'rt',encoding='utf8') as f:
        header=json.loads(next(f)); symbols=header['identity']['symbols']
        assert header['identity']=={'schema':'public-venue-collection/1','source':pas.SOURCE,'dex':'pas','collateral':'USDP','symbols':list(pas.SYMBOLS),'endpoint':pas.URL}
        assert len(symbols)==10
        for line in f:
            row=json.loads(line); assert row['seq']>last; last=row['seq']
            prefix.update((pas.canonical(row)+'\n').encode())
            assert hashlib.sha256(row['raw_json'].encode()).hexdigest()==row['sha256']
            if row['kind']!='book': continue
            r,_=pas.normalize(row)
            db.execute(f"INSERT INTO samples({','.join(r)}) VALUES({','.join('?' for _ in r)})",tuple(r.values()))
            books[r['symbol']].append(r)
    assert last==header['max_seq'] and prefix.hexdigest()==PREFIX
    assert set(books)==set(symbols)
    start=min(r['available_ms'] for rs in books.values() for r in rs)//60000*60000
    end=min(max(r['available_ms'] for r in rs) for rs in books.values())//60000*60000
    times=list(range(start,end+60000,60000)); n=len(times)
    keep=['decision_ms','book_seq','book_event_ms','quote_valid','mid','bid','ask','spread_bps','book_event_age_ms','log_return']
    panel={s:[] for s in symbols}
    for r in pas.panel(db,start,end+60000): panel[r['symbol']].append({k:r[k] for k in keep})
    db.close()
    checks=0
    for s in symbols:
        rs=sorted(books[s],key=lambda r:(r['available_ms'],r['seq'])); j=0; prev=None; prevok=False
        for t,p in zip(times,panel[s]):
            while j<len(rs) and rs[j]['available_ms']<=t: j+=1
            r=rs[j-1] if j else None
            ok=bool(r and r['quality']=='ready' and r['event_ms'] is not None and 0<=t-r['event_ms']<=90000 and 0<=t-r['received_ms']<=90000)
            ret=None; active=False
            if ok and prevok and r['seq']!=prev['seq'] and r['event_ms']>prev['event_ms']:
                ret=math.log(r['mid'])-math.log(prev['mid'])
                active=(r['bid'],r['ask'])!=(prev['bid'],prev['ask']) and 30000<=r['event_ms']-prev['event_ms']<=90000
            assert p['book_seq']==(r['seq'] if r else None) and p['quote_valid']==ok and p['log_return']==ret
            p['strict_return']=ret if active else None
            p['same_event']=bool(prev and r and r['seq']==prev['seq'])
            p['unchanged_top']=bool(ret is not None and (r['bid'],r['ask'])==(prev['bid'],prev['ask']))
            prev,prevok=r,ok; checks+=1
    joint=next(i for i in range(n) if all(panel[s][i]['quote_valid'] for s in symbols))
    data={}; quality={}
    for s in symbols:
        ps=panel[s]; ra=[p['log_return'] for p in ps]; rb=[p['strict_return'] for p in ps]
        data[s]={'index':[100*p['mid']/ps[joint]['mid'] if i>=joint and p['quote_valid'] else None for i,p in enumerate(ps)],
                 'returns':{'A':[None if r is None else r*100 for r in ra],'B':[None if r is None else r*100 for r in rb]},
                 'rv':{str(w):{m:rolling_rv(rs,w) for m,rs in [('A',ra),('B',rb)]} for w in [5,15,30,60]},
                 'spread':[p['spread_bps'] for p in ps], 'age':[None if p['book_event_age_ms'] is None else p['book_event_age_ms']/1000 for p in ps],
                 'flags':[[int(not p['quote_valid']) for p in ps],[int(p['same_event']) for p in ps],[int(p['unchanged_top']) for p in ps]],
                 'coverage':{m:trailing([r is not None for r in rs]) for m,rs in [('A',ra),('B',rb)]}}
        quality[s]={'grid_points':n,'valid_quotes':sum(p['quote_valid'] for p in ps),'valid_returns_A':sum(r is not None for r in ra),'zero_returns_A':sum(r==0 for r in ra),'valid_returns_B':sum(r is not None for r in rb),'spread_bps':distribution(data[s]['spread']),'rv_complete':{w:{m:sum(v is not None for v in vals) for m,vals in modes.items()} for w,modes in data[s]['rv'].items()}}
    dev=sorted(p['log_return'] for p in panel['pas:BTC'] if p['log_return'] is not None and p['decision_ms']-60000>=stamp('2026-10-20') and p['decision_ms']<stamp('2026-10-27'))
    lo,hi=(dev[round(q*(len(dev)-1))] for q in [.05,.95]); assert lo<0<hi
    events=[]; cluster=0
    for i,p in enumerate(panel['pas:BTC']):
        r=p['log_return']
        if r is not None and (r<=lo or r>=hi):
            if not events or i-events[-1]['i']>=30: cluster+=1
            events.append({'i':i,'utc':utc(times[i]),'direction':'down' if r<=lo else 'up','btc_return_pct':100*r,'cluster':cluster})
    for k,e in enumerate(events):
        e['overlap']=bool((k and e['i']-events[k-1]['i']<30) or (k+1<len(events) and events[k+1]['i']-e['i']<30))
        e['outcomes']={}
        for s in symbols[1:]:
            e['outcomes'][s]={}
            for h in [5,15,30]:
                rs=[p['log_return'] for p in panel[s][e['i']+1:e['i']+h+1]]
                valid=len(rs)==h and all(r is not None for r in rs)
                e['outcomes'][s][str(h)]={'value':100*math.fsum(rs) if valid else None,'missing':'' if valid else ('tail' if e['i']+h>=n else 'invalid return')}
    eventstats={}
    for s in symbols[1:]:
        eventstats[s]={}
        for d in ['up','down']:
            es=[e for e in events if e['direction']==d]; eventstats[s][d]={}
            for h in ['5','15','30']:
                vals=[e['outcomes'][s][h]['value'] for e in es if e['outcomes'][s][h]['value'] is not None]
                eventstats[s][d][h]={'events':len(es),'valid':len(vals),'missing':len(es)-len(vals),'tail_missing':sum(e['outcomes'][s][h]['missing']=='tail' for e in es),'overlap_events':sum(e['overlap'] for e in es),'distribution':distribution(vals)}
    jointcov={m:trailing([all(data[s]['returns'][m][i] is not None for s in symbols) for i in range(n)]) for m in ['A','B']}
    jointcov['quotes']=trailing([all(panel[s][i]['quote_valid'] for s in symbols) for i in range(n)])
    summary={'source':'hyperliquid-testnet/pas','input_sha256':HASH,'prefix_sha256':PREFIX,'max_seq':last,'pipeline_sha256':PIPE_HASH,'start_utc':utc(start),'end_utc':utc(end),'common_start_utc':utc(times[joint]),'grid_points':n,'symbols':symbols,'threshold_pct':{'q05':lo*100,'q95':hi*100},'development_n':len(dev),'event_count':len(events),'overlap_clusters':cluster,'quality':quality,'event_stats':eventstats,'validation':{'independent_asset_minutes':checks,'raw_payload_hashes':last,'prefix_hash_match':True}}
    # Cross-check BTC/ETH against their later Stage 1 frozen panel on common history.
    old=ROOT/'.cache/btc-eth-volatility-stage01-20261103/causal-minute-panel.jsonl.gz'
    assert digest(old)=='87bced947df20fdb6d8fe87e4655cbcfb943456478bd2147885ef7d1e01cf203'
    matched=0
    with gzip.open(old,'rt',encoding='utf8') as f:
        for line in f:
            p=json.loads(line); i=(p['decision_ms']-start)//60000
            if 0<=i<n:
                new=panel[p['symbol']][i]
                for key in ['book_seq','quote_valid','log_return']:
                    assert new[key]==p[key],(p['symbol'],i,key)
                assert new['strict_return']==p['strict_log_return']; matched+=1
    summary['validation']['stage1_overlap_asset_minutes']=matched
    a.output.mkdir(parents=True)
    (a.output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    with (a.output/'events.csv').open('w',newline='',encoding='utf8') as f:
        wr=csv.writer(f); wr.writerow(['utc','direction','btc_return_pct','cluster','overlap','target','horizon_min','signed_log_return_pct','missing_reason'])
        for e in events:
            for s,hs in e['outcomes'].items():
                for h,v in hs.items(): wr.writerow([e['utc'],e['direction'],e['btc_return_pct'],e['cluster'],e['overlap'],s,h,v['value'],v['missing']])
    payload={'times':times,'symbols':symbols,'colors':COLORS,'assets':data,'events':events,'summary':summary,'joint':jointcov}
    template=(HERE/'dashboard-template.html').read_text(encoding='utf8')
    (a.output/'dashboard.html').write_text(template.replace('__DATA__',json.dumps(payload,separators=(',',':'),allow_nan=False)),encoding='utf8')
    print('Validated data; rendering static figures',flush=True)
    render(a.output,payload)
    report(a.output,summary)
    hashes={p.name:digest(p) for p in a.output.iterdir() if p.is_file()}
    hashes.update({p.name:digest(p) for p in HERE.iterdir() if p.is_file()})
    (a.output/'manifest.json').write_text(json.dumps(hashes,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in summary.items() if k not in ['quality','event_stats']},indent=2))

def render(out,d):
    sys.path.insert(0,str(ROOT/'.cache/plot-libs'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as md
    times=[datetime.fromtimestamp(t/1000,timezone.utc) for t in d['times']]
    sm=d['summary']; sy=d['symbols']; assets=d['assets']
    footer=f"PAS Testnet asynchronous REST, not trades | {sm['start_utc']} to {sm['end_utc']}\nUTC as-of available<=t; event/receipt TTL 90s. Missing stays missing; A retains fresh zero; B active only. No executable P&L."
    def plot(ax,y,label,c,style='-'):
        ax.plot(times,[math.nan if v is None else v for v in y],style,lw=.65,color=c,label=label,
                marker='.' if style==':' else None,markersize=1.8)
    def save(fig,axs,name,title,time=True):
        fig.suptitle(title,fontsize=16)
        for ax in axs.flat:
            ax.grid(alpha=.2)
            if time: ax.xaxis.set_major_formatter(md.DateFormatter('%m-%d',tz=timezone.utc))
            handles,_=ax.get_legend_handles_labels()
            if handles: ax.legend(fontsize=7,loc='upper left')
        fig.text(.025,.012,footer,fontsize=8); fig.tight_layout(rect=(0,.055,1,.96)); fig.savefig(out/name,dpi=130); plt.close(fig)
    fig,axs=plt.subplots(5,2,figsize=(16,14),sharex=True)
    for ax,s,c in zip(axs.flat,sy,COLORS):
        plot(ax,assets[s]['index'],s,c); ax.axhline(100,color='#999',lw=.4); ax.set_ylabel('Mid index')
    save(fig,axs,'figure-01.png','1 — Ten assets / common start Mid = 100')
    fig,axs=plt.subplots(5,2,figsize=(16,14),sharex=True)
    for ax,s,c in zip(axs.flat,sy,COLORS):
        plot(ax,assets[s]['returns']['A'],s+' A',c); ax.set_ylabel('signed log return %')
        for k,color,label in [(0,'#c12c40','missing/expired'),(1,'#7948b8','same event')]:
            flags=assets[s]['flags'][k]; ax.scatter([times[i] for i,v in enumerate(flags) if v],[.04+k*.04 for v in flags if v],transform=ax.get_xaxis_transform(),s=3,color=color,label=label)
    save(fig,axs,'figure-02.png','2 — Signed minute returns / missing and reused events marked')
    fig,axs=plt.subplots(10,4,figsize=(21,23),sharex=True)
    for row,(s,c) in enumerate(zip(sy,COLORS)):
        for col,w in enumerate(['5','15','30','60']):
            ax=axs[row,col]
            plot(ax,assets[s]['rv'][w]['A'],'A',c)
            plot(ax,assets[s]['rv'][w]['B'],'B strict','#222',':')
            ax.set_title(s+f' RV{w} (%)',fontsize=9)
    save(fig,axs,'figure-03.png','3 — Complete-window RV / rows = assets, columns = minutes; no annualization')
    fig,axs=plt.subplots(3,3,figsize=(16,13),sharey=False)
    for ax,s in zip(axs.flat,sy[1:]):
        for offset,direction,color in [(-.16,'up','#1674b8'),(.16,'down','#cf6040')]:
            for j,h in enumerate(['5','15','30'],1):
                es=[e for e in d['events'] if e['direction']==direction and e['outcomes'][s][h]['value'] is not None]
                vals=[e['outcomes'][s][h]['value'] for e in es]
                if vals:
                    ax.boxplot([vals],positions=[j+offset],widths=.26,showfliers=False,medianprops={'color':color})
                    ax.scatter([j+offset+((e['i']%9)-4)*.017 for e in es],vals,s=2,alpha=.18,
                               color=['#b52233' if e['overlap'] else color for e in es])
        ax.set_title(s+' | blue up / orange down',fontsize=10); ax.axhline(0,color='#999',lw=.5)
        ax.set_xticks([1,2,3],['+5m','+15m','+30m']); ax.set_ylabel('signed log return %')
    save(fig,axs,'figure-04.png','4 — BTC shock outcomes / boxes median & IQR; red points = overlapping events',False)
    fig,axs=plt.subplots(10,4,figsize=(21,23),sharex=True)
    for row,(s,c) in enumerate(zip(sy,COLORS)):
        asset=assets[s]
        for col,key,unit in [(0,'spread','full spread bps'),(1,'age','event age seconds')]:
            plot(axs[row,col],asset[key],s,c); axs[row,col].set_title(unit,fontsize=9)
        axs[row,1].axhline(90,color='red',ls='--',lw=.6)
        for m,style in [('A','-'),('B',':')]:
            invalid=[1 if v is None else 0 for v in asset['rv']['30'][m]]
            plot(axs[row,2],trailing(invalid),'invalid RV30 '+m,c if m=='A' else '#222',style)
            plot(axs[row,3],asset['coverage'][m],'valid returns '+m,c if m=='A' else '#222',style)
        axs[row,2].set_title('trailing60 invalid RV30 %',fontsize=9); axs[row,3].set_title('trailing60 valid returns %',fontsize=9)
    save(fig,axs,'figure-05.png','5 — Execution friction / raw age (including stale) / missing windows / coverage')

def report(out,s):
    rows=['# PAS 十资产可视化观察报告','',f"真实 PAS Testnet 公共异步 REST 行情；{s['start_utc']} — {s['end_utc']}。共同指数基点 {s['common_start_utc']}。",'',
          '此快照截至10月30日，不能覆盖BTC/ETH版的11月3日尾段。available=max(requested,received,stored)≤UTC决策时点；event/receipt TTL90秒。缺失不补零、不插值，RV要求完整连续窗。异步报价不是同步成交，PAS衍生报价不等于相应底层现货/指数。全部历史已见，不是干净样本外。','',
          '## 数据与摩擦（直接计算）','', '|资产|有效A收益|其中零收益|有效B收益|RV30 A / B完整窗|spread中位数bps|','|---|---:|---:|---:|---:|---:|']
    for symbol,q in s['quality'].items():
        rows.append(f"|{symbol}|{q['valid_returns_A']}|{q['zero_returns_A']}|{q['valid_returns_B']}|{q['rv_complete']['30']['A']} / {q['rv_complete']['30']['B']}|{q['spread_bps']['quantiles']['0.5']:.3f}|")
    rows += ['', '全spread可作 Mid不变时跨价往返成本参考，未含手续费、滑点、深度和延迟，不是可实现收益估计。B改变完整窗口资格；共同完整窗A/B数值相同，不是去零后抬高同一窗口RV。','', '## 事件描述','',f"固定开发段BTC Q05/Q95={s['threshold_pct']['q05']:.6f}% / {s['threshold_pct']['q95']:.6f}%；{s['event_count']}事件，{s['overlap_clusters']}重叠连通簇（不代表独立样本）。不依赖目标未来标签入选；分布仅用各期限完整结果。",'', '|目标|冲击|期限m|有效/总事件|缺失（含尾段）|中位数%|IQR%|','|---|---|---:|---:|---:|---:|---:|']
    for symbol,dirs in s['event_stats'].items():
        for direction,hs in dirs.items():
            for h,v in hs.items():
                q=v['distribution'].get('quantiles',{})
                rows.append(f"|{symbol}|{direction}|{h}|{v['valid']}/{v['events']}|{v['missing']} ({v['tail_missing']})|{q.get('0.5',math.nan):.5f}|{q.get('0.75',math.nan)-q.get('0.25',math.nan):.5f}|")
    rows += ['', '## 直接图像观察','', '1. 图1：HYPE在10月29日前后明显上涨，与BTC/ETH此时的价格路径分离；这是此PAS样本中的路径差异，不是独立Alpha。','2. 图1/2/5：USDEUR在10月30日同时呈现较大的价格、分钟收益与spread尖峰；图表保留尖峰，没有按结果剔除。不能据此推断外汇现货发生相同幅度变化。','3. 图3/5：USDJPY、USDEUR的Strict完整RV窗口非常稀少；USDJPY的RV30仅8个，USDEUR为87个。新有效零收益与共同覆盖率需一起读，不能把B空白误作低波动。', '', '各资产小图使用自己的纵轴；比较幅度须读数，不能只比较屏幕高度。事件分布也使用各资产自己的纵轴。', '', '## 可能经济解释（未识别）','', '跨资产差异可能包含不同交易时段、报价离散化、共同报价源或共同冲击的影响；仅凭PAS报价不能区分这些机制。', '', '## 下一阶段候选假设（本轮不检验）','', '1. 非加密资产静止报价是否随UTC时间呈现规律？','2. 不同资产的spread变化是否与共同报价机制有关？','3. BTC冲击后其他资产的分布是否主要反映各自的活跃状态？','', '## 证据不支持的解释','', '不支持BTC因果领先、可预测方向、阈值最优、可交易获利或样本外有效。高度重叠事件和结果缺失使分布有依赖及事后可用性选择，不能据图直接排列最佳策略。','', '## 打开','', '在本地浏览器打开dashboard.html；可切换10资产、BTC对照、A/B、UTC范围和曲线，拖动缩放，悬停读数。图1–5 PNG在同目录。']
    (out/'observations.md').write_text('\n'.join(rows),encoding='utf8')

if __name__=='__main__': main()

