"""Public-only standing authorization; bounded grants and durable renewal notices."""
import json
import os
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .activation import require_approval
from .policy import dec, validate_snapshot

DAY = 86_400_000
LEAD = 900_000


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        if os.name != 'nt':
            folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(folder)
            finally:
                os.close(folder)
    finally:
        temp.unlink(missing_ok=True)


def recover_notice(approval_path, monitor):
    """Recover a crash between atomic grant replacement and notice enqueue."""
    journal = approval_path.with_name('renewal-pending.json')
    if not journal.exists():
        return
    saved = json.loads(journal.read_text())
    current = json.loads(approval_path.read_text())
    if current == saved['new']:
        monitor.renewal_notice(saved['notice'])
    elif current != saved['old']:
        raise ValueError('Renewal journal conflicts with current approval; operator review required')
    journal.unlink()


def maybe_renew(permission_path, approval_path, runtime_path, policy, client,
                par_wallet_file, monitor, evidence, smtp_config, now):
    """No signer, leverage operation, resume, orders, or expiry bypass exists here."""
    approval_path = Path(approval_path)
    recover_notice(approval_path, monitor)
    permission = json.loads(Path(permission_path).read_text())
    expected = {'schema', 'enabled', 'purpose', 'dex', 'team_wallet', 'api_wallet',
                'policy_hash', 'not_before_ms', 'duration_ms', 'renew_before_ms'}
    if set(permission) != expected or permission['schema'] != 'pas-standing-trading/1':
        raise ValueError('Invalid standing permission schema')
    if permission['enabled'] is False:
        return False
    p = replace(policy, enabled=True)
    if (permission['enabled'] is not True or permission['purpose'] != 'trading'
            or permission['dex'] != 'pas' or p.dex != 'pas'
            or permission['team_wallet'] != client.team
            or permission['api_wallet'].lower() != client.settings['API_WALLET_ADDRESS'].lower()
            or permission['policy_hash'] != p.digest
            or type(permission['not_before_ms']) is not int or permission['not_before_ms'] > now
            or type(permission['duration_ms']) is not int or permission['duration_ms'] != DAY
            or type(permission['renew_before_ms']) is not int or permission['renew_before_ms'] != LEAD):
        raise ValueError('Standing permission identity/policy/limits mismatch')
    old = json.loads(approval_path.read_text())
    if old['expires_ms'] - now > LEAD:
        return False
    # Expired grants remain expired; renewal never auto-resumes a stopped trader.
    require_approval(approval_path, 'trading', p, client, now, par_wallet_file)
    if (smtp_config is None or evidence['issues']
            or not 0 <= now - evidence['time_ms'] < 180_000
            or monitor.db.execute('SELECT COUNT(*) FROM outbox WHERE delivered_ms IS NULL AND attempts>0').fetchone()[0]):
        raise ValueError('Renewal requires healthy public checks and available email channel')
    s = evidence['snapshot']
    validate_snapshot(s, p, now)
    db = sqlite3.connect(Path(runtime_path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        identity = json.loads(db.execute('SELECT value FROM identity').fetchone()[0])
        state = json.loads(db.execute('SELECT value FROM state').fetchone()[0])
        pending = db.execute("SELECT COUNT(*) FROM intents WHERE status!='terminal'").fetchone()[0]
        requests = db.execute('SELECT COUNT(*) FROM requests WHERE consumed=0').fetchone()[0]
    finally:
        db.close()
    if (identity.get('policy_hash') != p.digest or identity.get('team') != client.team
            or identity.get('dex') != 'pas' or state['paused'] or state.get('market_wait')
            or pending or requests or state['last_tick_ms'] is None
            or not 0 <= now-state['last_tick_ms'] < 180_000
            or dec(s['quantity']) != dec(state['expected_quantity'])
            or state['baseline'] is None or state['peak'] is None
            or dec(state['baseline'])-dec(s['equity']) >= dec(p.max_loss)
            or dec(state['peak'])-dec(s['equity']) >= dec(p.max_drawdown)
            or abs(dec(s['quantity']))*dec(s['mark']) > dec(p.max_notional)*dec('1.1')
            or dec(s['free']) < dec(s['equity'])*dec(p.min_free_fraction)):
        raise ValueError('Renewal requires healthy active trader and consistent account risk')
    # Recheck public wallet authorization immediately before creating the grant.
    agents = client.query('extraAgents', user=client.team)
    if not any(a['address'].lower() == permission['api_wallet'].lower()
               and a['validUntil'] > now + DAY for a in agents):
        raise ValueError('API wallet authorization does not cover the next 24 hours')
    new = {**old, 'not_before_ms': now, 'expires_ms': now + DAY}
    notice = {'event': 'trading-approval-renewed', 'renewal_id': str(new['expires_ms']),
              'message': 'PAS交易授权已自动续期24小时；策略和风险预算未改变。',
              'time_ms': now, 'previous_expires_ms': old['expires_ms'],
              'expires_ms': new['expires_ms'],
              'expires_utc': datetime.fromtimestamp(new['expires_ms']/1000, timezone.utc).isoformat(),
              'strategy': 'ETH Funding-LAST', 'max_notional_USDP': p.max_notional,
              'policy_hash': p.digest,
              'leverage': '1x Cross', 'checks': 'public account, risk, trader and authorization passed',
              'position_warning': 'Stop/pause does not flatten positions'}
    journal = approval_path.with_name('renewal-pending.json')
    atomic_json(journal, {'old': old, 'new': new, 'notice': notice})
    # Fail closed if operator revoked permission or replaced the grant meanwhile.
    if json.loads(Path(permission_path).read_text()) != permission or json.loads(approval_path.read_text()) != old:
        raise ValueError('Permission/approval changed during renewal')
    # Serialize publication against operator pauses/new order reservations. The
    # trading ledger is never modified by the renewal process.
    db = sqlite3.connect(Path(runtime_path).resolve().as_uri() + '?mode=rw', uri=True)
    try:
        db.execute('BEGIN IMMEDIATE')
        fresh = json.loads(db.execute('SELECT value FROM state').fetchone()[0])
        fields = ['paused', 'market_wait', 'generation', 'pause_epoch', 'expected_quantity', 'baseline', 'peak']
        if (any(fresh.get(k) != state.get(k) for k in fields)
                or db.execute("SELECT COUNT(*) FROM intents WHERE status!='terminal'").fetchone()[0]
                or db.execute('SELECT COUNT(*) FROM requests WHERE consumed=0').fetchone()[0]):
            raise ValueError('Trader changed during renewal; fresh checks required')
        if json.loads(Path(permission_path).read_text()) != permission or json.loads(approval_path.read_text()) != old:
            raise ValueError('Permission/approval changed before publication')
        atomic_json(approval_path, new)
        db.commit()
    finally:
        db.close()
    recover_notice(approval_path, monitor)
    return True
