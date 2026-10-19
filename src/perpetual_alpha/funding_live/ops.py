"""WAL-consistent backup to a NEW file; restore never starts a signer."""
import argparse
import json
import os
import sqlite3
from pathlib import Path


def backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or not source.is_file() or not destination.parent.is_dir():
        raise ValueError('Existing source and distinct new backup destination required')
    reader = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    try:
        identity = json.loads(reader.execute('SELECT value FROM identity WHERE id=1').fetchone()[0])
        if identity.get('schema') != 'funding-exchange-ledger/1':
            raise ValueError('Not a Funding exchange ledger')
        # Exclusive creation forbids destroying a previous backup or the live DB.
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        writer = sqlite3.connect(destination)
        try:
            reader.backup(writer)
            if writer.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup integrity failed; preserve for inspection')
            state = json.loads(writer.execute('SELECT value FROM state').fetchone()[0])
            pending = writer.execute("SELECT COUNT(*) FROM intents WHERE status='unknown'").fetchone()[0]
            writer.close()
            writer = None
            with destination.open('r+b') as stream:
                os.fsync(stream.fileno())
            return {'destination': str(destination), 'identity': identity, 'pending_intents': pending,
                    'generation': state['generation'], 'restore_requires_exchange_reconciliation': True}
        finally:
            if writer:
                writer.close()
    finally:
        reader.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(backup(args.source, args.destination)))


if __name__ == '__main__':
    main()
