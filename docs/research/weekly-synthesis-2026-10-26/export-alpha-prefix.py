"""Read-only VPS prefix export to gzip stdout. No server file writes."""
import datetime
import gzip
import json
import sqlite3
import sys

path = '/opt/perpetual-alpha-pas-data/pas/public.sqlite3'
c = sqlite3.connect('file:' + path + '?mode=ro', uri=True, timeout=15)
c.row_factory = sqlite3.Row
c.execute('PRAGMA query_only=ON')
c.execute('BEGIN')
header = {'kind': 'export-header', 'schema': 'pas-alpha-prefix/1', 'path': path,
          'captured_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'identity': json.loads(c.execute('SELECT value FROM identity').fetchone()[0]),
          'max_seq': c.execute('SELECT max(seq) FROM observations').fetchone()[0]}
with gzip.GzipFile(fileobj=sys.stdout.buffer, mode='wb', mtime=0) as out:
    out.write((json.dumps(header, separators=(',', ':')) + '\n').encode())
    for row in c.execute('SELECT * FROM observations WHERE seq<=? ORDER BY seq', (header['max_seq'],)):
        out.write((json.dumps(dict(row), separators=(',', ':')) + '\n').encode())
c.rollback()
c.close()
