"""Read-only VPS consistent transaction and bounded free ETF data attempts."""
import gzip
import hashlib
import json
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'docs/research/new-alpha-discovery-2026-10-18/candidate-validation'


def audit_vps():
    script = '''import sqlite3,json,time
c=sqlite3.connect('file:/opt/perpetual-alpha/data/market.sqlite3?mode=ro',uri=True,timeout=5)
c.execute('PRAGMA query_only=ON')
c.execute('BEGIN')
tables=c.execute("SELECT name,sql FROM sqlite_master WHERE type='table'").fetchall()
data=[]
for s in ['par:EWY','par:HYPE']:
 rows=c.execute('SELECT raw_json FROM snapshots WHERE symbol=? ORDER BY event_ms LIMIT 200001',(s,)).fetchall()
 if len(rows)>200000: raise ValueError('Read budget exceeded')
 data.extend(json.loads(r[0]) for r in rows)
print(json.dumps({'observed_ms':int(time.time()*1000),'schema':tables,'rows':data}))
c.rollback()
c.close()
'''
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=.cache/ovh-known-hosts', 'ubuntu@57.129.179.248', 'python3', '-']
    result = subprocess.run(command, input=script.encode(), capture_output=True, timeout=60, cwd=ROOT.parents[1])
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    data = json.loads(result.stdout)
    with gzip.open(OUT/'vps-readonly-export.json.gz', 'wt', encoding='utf-8', newline='\n') as stream:
        json.dump(data, stream, allow_nan=False)
    print('VPS exported rows:', len(data['rows']))


def etf():
    end = int(time.time())
    urls = {
        'yahoo-daily': f'https://query1.finance.yahoo.com/v8/finance/chart/EWY?period1=946684800&period2={end}&interval=1d&events=div%2Csplits',
        'yahoo-hourly': f'https://query1.finance.yahoo.com/v8/finance/chart/EWY?period1={end-729*86400}&period2={end}&interval=1h&events=div%2Csplits',
        'issuer': 'https://www.ishares.com/us/products/239681/ishares-msci-south-korea-capped-etf',
    }
    attempts = []
    for name, url in urls.items():
        started = time.time_ns()//1000000
        row = {'id': name, 'url': url, 'started_ms': started}
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read(16*1024*1024+1)
                row.update(status=response.status, final_url=response.url)
            if len(body) > 16*1024*1024:
                raise ValueError('Download byte limit')
            row.update(received_ms=time.time_ns()//1000000, sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))
            with gzip.open(OUT/(name+'.response.gz'), 'wb') as stream:
                stream.write(body)
        except Exception as error:
            row.update(error=type(error).__name__+': '+str(error), received_ms=time.time_ns()//1000000)
        attempts.append(row)
        print(name, row.get('error', row.get('bytes')), flush=True)
    (OUT/'etf-fetch.json').write_text(json.dumps(attempts, indent=2)+'\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    audit_vps()
    etf()
