"""Reserve existing public Collector prefix via previously authorized read-only runtime.
No collection or normalization; this tool must never calculate price returns/labels.
"""

import gzip
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REMOTE = r"""
import gzip,hashlib,json,sys,datetime
from alpha_csv_export.pas_csv_export import Reader
from alpha_csv_export.pas_research import raw_identity,canonical
reader=Reader('/opt/perpetual-alpha-pas-data/pas/public.sqlite3')
stream=gzip.GzipFile(fileobj=sys.stdout.buffer,mode='wb',mtime=0)
def emit(x):stream.write((canonical(x)+'\n').encode())
identity=json.loads(raw_identity(reader.db))
coverage=[dict(x) for x in reader.query('SELECT symbol,COUNT(*) AS rows,MIN(MAX(requested_ms,received_ms,stored_ms)) AS min_available_ms,MAX(MAX(requested_ms,received_ms,stored_ms)) AS max_available_ms FROM observations WHERE seq<=? GROUP BY symbol',(reader.maximum,))]
emit({'type':'header','identity':identity,'source_max_seq':reader.maximum,'coverage':coverage,'captured_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'policy':'reserved only; no prices/returns/labels analyzed'})
last=0;count=0;digest=hashlib.sha256()
while True:
 rows=reader.query('SELECT * FROM observations WHERE seq>? AND seq<=? ORDER BY seq LIMIT 512',(last,reader.maximum))
 if not rows:break
 last=rows[-1]['seq']
 for row in rows:
  text=canonical(dict(row));digest.update((text+'\n').encode());emit(dict(row));count+=1
reader.close();emit({'type':'footer','rows':count,'canonical_prefix_sha256':digest.hexdigest()});stream.close()
"""


def main():
    assert (HERE / "freeze.json").exists()
    cache = ROOT / ".cache/h09-reserved-public-20261105"
    cache.mkdir(exist_ok=True)
    p = cache / "pas-reserved-prefix.jsonl.gz"
    part = cache / "pas-reserved-prefix.part"
    assert not p.exists() and not part.exists()
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=12",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=.cache/ovh-known-hosts",
        "ubuntu@57.129.179.248",
        "/opt/alpha-csv-export/venv/bin/python",
        "-I",
        "-",
    ]
    with part.open("xb") as f:
        res = subprocess.run(
            command, input=REMOTE.encode(), stdout=f, stderr=subprocess.PIPE, cwd=ROOT, timeout=300
        )
    meta = {
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "method": "Existing public Reader, read transaction; mode=ro/query_only; existing observations only",
        "remote_public_path": "/opt/perpetual-alpha-pas-data/pas/public.sqlite3",
        "no_services_accounts_or_secrets": True,
        "after_protocol_freeze_sha256": hashlib.sha256((HERE / "freeze.json").read_bytes()).hexdigest(),
    }
    if res.returncode:
        meta.update(
            status="UNAVAILABLE",
            reason=res.stderr.decode(errors="replace")[:700],
            incomplete_not_analyzed=True,
        )
    else:
        with gzip.open(part, "rt", encoding="utf8") as f:
            header = json.loads(next(f))
            last = None
            count = 0
            digest = hashlib.sha256()
            # Read exact raw bytes for integrity; never extract raw_json prices.
            for line in f:
                if line.startswith('{"canonical_prefix_sha256"'):
                    last = json.loads(line)
                    break
                digest.update(line.encode())
                count += 1
            assert last and count == last["rows"] and digest.hexdigest() == last["canonical_prefix_sha256"]
        part.rename(p)
        meta.update(
            status="RESERVED_NOT_ANALYZED",
            path=str(p.relative_to(ROOT)),
            sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
            header=header,
            footer=last,
            no_returns_labels_or_price_analysis=True,
            newer_full_market=all(
                v["max_available_ms"] > 1793366220000 for v in header["coverage"] if v["symbol"]
            ),
        )
    (HERE / "reserved-data.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf8")
    inv = json.loads((HERE / "new-data-inventory.json").read_text())
    inv["remote_status"] = meta["status"]
    inv["reserved_manifest"] = "reserved-data.json"
    (HERE / "new-data-inventory.json").write_text(json.dumps(inv, indent=2) + "\n", encoding="utf8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
