"""Independent outcome/overlap checks; never fetches data or alters frozen evidence."""
import csv
import gzip
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]

def main():
    out = Path(sys.argv[1]) if len(sys.argv)>1 else HERE/'output-reviewed'
    cache = ROOT/'.cache/btc-eth-volatility-stage01-20261103'
    summary = json.loads((out/'summary.json').read_text(encoding='utf8'))
    for name,key in [('normalized-pas-btc-eth.jsonl.gz','raw_sha256'),('causal-minute-panel.jsonl.gz','panel_sha256')]:
        assert hashlib.sha256((cache/name).read_bytes()).hexdigest()==summary[key]
    panel={'pas:BTC':[], 'pas:ETH':[]}
    with gzip.open(cache/'causal-minute-panel.jsonl.gz','rt',encoding='utf8') as f:
        for line in f:
            p=json.loads(line); panel[p['symbol']].append(p)
    btc,eth=panel['pas:BTC'],panel['pas:ETH']
    dev=sorted(p['log_return'] for i,p in enumerate(btc) if p['log_return'] is not None and p['decision_ms']-60000>=1792454400000 and p['decision_ms']<1793059200000)
    lo,hi=(dev[round(q*(len(dev)-1))] for q in [.05,.95])
    assert 100*lo==summary['threshold_log_return_pct']['q05']
    assert 100*hi==summary['threshold_log_return_pct']['q95']
    expected=[i for i,p in enumerate(btc) if p['log_return'] is not None and (p['log_return']<=lo or p['log_return']>=hi)]
    with (out/'events.csv').open(encoding='utf8',newline='') as f: events=list(csv.DictReader(f))
    assert [int(e['i']) for e in events]==expected
    checked=0; cluster=0
    for k,(e,i) in enumerate(zip(events,expected)):
        if k==0 or i-expected[k-1]>=30: cluster+=1
        overlap=(k>0 and i-expected[k-1]<30) or (k+1<len(expected) and expected[k+1]-i<30)
        assert int(e['cluster'])==cluster and (e['overlap_30']=='True')==overlap
        for h in [5,15,30]:
            block=[p['log_return'] for p in eth[i+1:i+h+1]]
            valid=len(block)==h and all(r is not None for r in block)
            assert bool(e[str(h)])==valid
            if valid: assert math.isclose(float(e[str(h)]),100*math.fsum(block),abs_tol=1e-12)
            else: assert e[f'missing_{h}']==('tail' if i+h>=len(eth) else 'invalid return')
            checked+=1
    assert cluster==summary['overlap_clusters']
    browser=subprocess.run(['node',str(HERE/'verify_dashboard.cjs'),str(out)],capture_output=True,text=True,check=True)
    result={'status':'PASS','frozen_hashes':2,'events_independent_of_future_labels':len(expected),'outcomes_checked':checked,'overlap_clusters':cluster,'dashboard':json.loads(browser.stdout),'png_visual_review':5}
    (out/'verification.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file() and p.name!='manifest.json'}
    for name in ['build_eda.py','dashboard-template.html','README.md','protocol.md','verify_dashboard.cjs','verify_eda.py']:
        hashes[name]=hashlib.sha256((HERE/name).read_bytes()).hexdigest()
    (out/'manifest.json').write_text(json.dumps(hashes,indent=2),encoding='utf8')
    print(json.dumps(result,indent=2))

if __name__=='__main__': main()
