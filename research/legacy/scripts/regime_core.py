"""Causal features, mature labels and deterministic overlays on unchanged rules."""
import bisect
import statistics
from collections import defaultdict

from analyze_long_history import interval
from regime_protocol import DAY, FAMILIES, FILTER, STATES

from perpetual_alpha.offline_quant.data import HOUR
from perpetual_alpha.offline_quant.signals import Order, efficiency, vol


def classify(rows):
    result, sigmas = {}, []
    for i, bar in enumerate(rows):
        state, ratio, strength, sigma = 'UNKNOWN', None, None, None
        if i >= 24:
            segment = rows[i-24:i+1]
            sigma, strength = vol(segment), efficiency(segment)
            if i >= 192 and all(b.time-a.time == HOUR for a, b in zip(rows[i-192:i], rows[i-191:i+1])):
                reference = statistics.median(sigmas[-168:])
                if reference > 0:
                    ratio = sigma/reference
                    state = ('TREND' if strength > .35 else 'RANGE')+('_HIGH' if sigma > reference else '_LOW')
            sigmas.append(sigma)
        result[bar.time] = {'state': state, 'efficiency25': strength, 'rv24': sigma,
                            'relative_rv': ratio, 'feature_end_ms': bar.time}
    return result


def mature(episodes, state, start, end):
    return [r for r in episodes if r['entry_state'] == state and start <= r['entry_decision_ms']
            and r['exit_ms'] <= end]


def metrics(hours, episodes, triggers, state, start, end):
    subset = [r for r in hours if start < r['end_ms'] <= end and r['state'] == state]
    es = mature(episodes, state, start, end)
    daily = {d: 0. for d in range(start//DAY, (end-1)//DAY+1)}
    monthly = defaultdict(float)
    value, peak, dd = 10000., 10000., 0.
    for r in subset:
        daily[(r['end_ms']-1)//DAY] += r['net']/10000
        month = month_of(r['end_ms']-1)
        monthly[month] += r['net']/10000
        value += r['net']
        peak = max(peak, value)
        dd = max(dd, (peak-value)/peak)
    counts = defaultdict(int)
    for e in es:
        counts[month_of(e['entry_decision_ms'])] += 1
    days = len({e['entry_decision_ms']//DAY for e in es})
    enough = len(es) >= 30 and days >= 10 and sum(v >= 5 for v in counts.values()) >= 3
    net = sum(r['net'] for r in subset)
    totals = {k: sum(r[k] for r in subset) for k in ['gross', 'fee', 'spread', 'slippage', 'funding', 'turnover']}
    return {**totals, 'net': net, 'return_on_initial': net/10000,
            'state_hours': len(subset), 'occupied_hours': sum(r['gross_fraction'] > 0 for r in subset),
            'entry_episodes': len(es), 'entry_days': days, 'episode_months': dict(counts),
            'trigger_count': sum(start <= t < end and s == state for t, s in triggers),
            'mean_capital_fraction_in_state': statistics.mean(r['gross_fraction'] for r in subset) if subset else 0.,
            'attributed_path_drawdown': dd, 'daily_pnl_vol_on_initial': statistics.stdev(daily.values()) if len(daily)>1 else None,
            'months': dict(monthly), 'status': 'INSUFFICIENT_DATA' if not enough else 'REJECTED' if net<=0 else 'INCONCLUSIVE',
            'daily': daily}


def month_of(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t/1000, timezone.utc).strftime('%Y-%m')


def extract(detail, states):
    """Exhaustive hourly time-state allocation; no future labels in state assignment."""
    fills = defaultdict(list)
    for f in detail['fills']:
        fills[f['time_ms']].append(f)
    funding = defaultdict(float)
    for f in detail['funding']:
        funding[(f['time_ms']-1)//HOUR*HOUR] += f['cashflow_scenario']
    previous, hours = 10000., []
    end = detail['equity'][-1]['time_ms']
    for e in detail['equity']:
        t = e['time_ms']-HOUR
        # End liquidation belongs to the final hour, unlike ordinary boundary fills.
        fs = fills[t]+(fills[end] if e['time_ms'] == end else [])
        fee, friction = sum(f['fee'] for f in fs), sum(f['friction'] for f in fs)
        net, cash = e['equity_scenario']-previous, funding[t]
        previous = e['equity_scenario']
        cost = detail['summary']['costs']
        total = cost['spread_bps']/2+cost['slippage_bps']
        spread = friction*(cost['spread_bps']/2)/total if total else 0.
        label_time = t-HOUR
        hours.append({'end_ms': e['time_ms'], 'state': states.get(label_time, {}).get('state', 'UNKNOWN'),
                      'state_time_ms': label_time, 'gross': net+fee+friction-cash, 'net': net,
                      'fee': fee, 'spread': spread, 'slippage': friction-spread, 'funding': cash,
                      'turnover': sum(abs(f['delta']*f['reference']) for f in fs),
                      'gross_fraction': e['gross_fraction']})
    # Aggregating all partial reductions with identical position-open time avoids pseudo trades.
    episodes = {}
    for trade in detail['trades']:
        t = trade['entry_ms']
        row = episodes.setdefault(t, {'entry_ms': t, 'entry_decision_ms': t-HOUR,
                                     'entry_state': states.get(t-HOUR, {}).get('state', 'UNKNOWN'),
                                     'exit_ms': t, 'net': 0., 'segments': 0})
        row['exit_ms'] = max(row['exit_ms'], trade['exit_ms'])
        row['net'] += trade['net_scenario']
        row['segments'] += 1
    triggers = [(r['time'], states.get(r['time'], {}).get('state', 'UNKNOWN'))
                for r in detail['signals'] if any(r['targets'].values())]
    for key, expected in [('net', detail['summary']['known_components_return']*10000),
                          ('fee', detail['summary']['fees']), ('funding', detail['summary']['funding_scenario']),
                          ('gross', detail['summary']['gross_price_pnl'])]:
        assert abs(sum(r[key] for r in hours)-expected)<1e-6, key
    return hours, list(episodes.values()), triggers


def choose(data, fold):
    mapping, audit = {}, []
    for state in STATES:
        candidates = []
        for rule in FAMILIES:
            h, e, t = data[rule]
            train = metrics(h, e, t, state, *fold['train'])
            ci = interval(list(train['daily'].values()))
            eligible = train['entry_episodes']>=30 and train['entry_days']>=10 and train['net']>0 and ci['lower95'] is not None and ci['lower95']>0
            audit.append({'state': state, 'rule': rule, 'train': train, 'train_ci': ci, 'train_eligible': eligible})
            if eligible:
                candidates.append((train['net'], -FAMILIES.index(rule), rule))
        picked = max(candidates)[2] if candidates else 'FLAT'
        validation = None
        if picked != 'FLAT':
            h, e, t = data[picked]
            validation = metrics(h, e, t, state, *fold['validation'])
            if not (validation['entry_episodes']>=5 and validation['entry_days']>=3 and validation['net']>0):
                picked = 'FLAT'
        mapping[state] = picked
        audit.append({'state': state, 'chosen': picked, 'validation_of_train_winner': validation})
    return mapping, audit


def overlay(shadows, states, symbol, start, end, mode, mapping=None):
    timeline = {rule: {r['time']: r['targets'][symbol] for r in rows} for rule, rows in shadows.items()}
    latest = {}
    for rule, line in timeline.items():
        earlier = sorted(t for t in line if t < start)
        latest[rule] = line[earlier[-1]] if earlier else 0.
    orders, decisions = [], []
    chosen, previous, switches = 'FLAT', None, 0
    for t in range(start, end, HOUR):
        changed = set()
        for rule, line in timeline.items():
            if t in line:
                latest[rule] = line[t]
                changed.add(rule)
        state = states.get(t, {}).get('state', 'UNKNOWN')
        prior = chosen
        if mode == 'SELECTOR':
            if t//HOUR % 12 == 0:
                chosen = (mapping or {}).get(state, 'FLAT')
                decisions.append({'time_ms': t, 'state': state, 'chosen': chosen})
        elif mode.startswith('FILTER:'):
            rule = mode.split(':')[1]
            chosen = rule if state == FILTER[rule] else 'FLAT'
        elif mode.startswith('FIXED:'):
            chosen = mode.split(':')[1]
        else:
            chosen = 'FLAT'
        switches += chosen != prior
        target = latest[chosen] if chosen != 'FLAT' else 0.
        if target != previous or chosen in changed:
            orders.append(Order(t, {symbol: target}, mode, {'state': state, 'family': chosen}))
            previous = target
    return orders, {'family_switches_including_flat': switches, 'decisions': decisions}


def lookup(states, t):
    """Explicit as-of lookup for tests; never nearest future sample."""
    times = sorted(states)
    i = bisect.bisect_right(times, t)-1
    return states[times[i]] if i >= 0 else {'state': 'UNKNOWN'}
