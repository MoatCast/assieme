"""Sessions -> per-agent totals, the wake graph, refusals and flags; rendered as Markdown or JSON."""
import collections
import datetime as dt
import json

from .model import HUMAN

EMPTY_CALLS = 2              # a wake the receiver answers in at most this many calls did no work
EMPTY_WAKES_PER_EDGE = 20    # flag a sender -> receiver pair with more empty wakes than this
REPEATED_REFUSALS = 3        # flag a command refused at least this often for one agent
PEAK_CONTEXT = 250_000       # flag a context above this: compaction is not keeping up
NOISY_LAST_HOUR = 10         # flag an edge with at least this many wakes in the last hour...
NOISY_FACTOR = 3             # ...and this many times its average hourly rate
SEND_LESS = 3                # how many edges the "send less" list names
SEND_LESS_MIN_SHARE = 0.01   # ...each worth at least this share of all input tokens


def aggregate(sessions, now=None, hours=None):
    """now/hours describe the window; with them, edges also get hourly rates and a last-hour count."""
    agents = collections.defaultdict(lambda: {'sessions': 0, 'calls': 0, 'tokens': 0, 'output': 0, 'peak': 0,
                                              'sources': set()})
    edges = collections.defaultdict(lambda: {'wakes': 0, 'empty': 0, 'tokens': 0, 'empty_tokens': 0, 'calls': 0,
                                             'last_hour': 0, 'channels': set(), 'sample': ''})
    hour_ago = now - dt.timedelta(hours=1) if now else None
    refusals = collections.Counter()
    notes = {}
    for s in sessions:
        a = agents[s.agent]
        a['sessions'] += 1
        a['sources'].add(s.source)
        for k in ('calls', 'tokens', 'output'):
            a[k] += getattr(s, k)
        a['peak'] = max(a['peak'], s.peak)
        for t in s.turns:
            e = edges[(t.sender, s.agent)]
            e['wakes'] += 1
            e['empty'] += t.calls <= EMPTY_CALLS
            e['tokens'] += t.tokens
            e['empty_tokens'] += t.tokens if t.calls <= EMPTY_CALLS else 0  # what the receiver re-read for nothing
            e['last_hour'] += bool(hour_ago and t.ts and hour_ago <= t.ts <= now)
            e['calls'] += t.calls
            e['channels'].add(t.channel)
            e['sample'] = t.text or e['sample']
        for (cmd, why), n in s.refusals.items():
            refusals[(s.agent, cmd, why)] += n
        notes.update(s.rule_notes)

    flags = []
    for name, a in agents.items():
        if a['peak'] > PEAK_CONTEXT:
            flags.append(f"{name}: context peaked at {a['peak'] // 1000}K tokens; compact or restart it")
    for (sender, receiver), e in edges.items():
        if sender != HUMAN and e['empty'] > EMPTY_WAKES_PER_EDGE:
            flags.append(f"{sender} -> {receiver}: {e['empty']} of {e['wakes']} wakes needed <= {EMPTY_CALLS} calls "
                         f"({e['tokens'] / 1e6:.1f}M tokens on all of them); stop the routine message or batch it")
    for (agent, cmd, why), n in refusals.items():
        if n >= REPEATED_REFUSALS:
            fix = notes.get(why) or 'fix the rule or the brief instead of retrying'
            flags.append(f"{agent}: '{cmd}' refused {n} times by {why}; {fix}")
    for (sender, receiver), e in edges.items():
        e['per_hour'] = round(e['wakes'] / hours, 2) if hours else None
        if (sender != HUMAN and hours and e['last_hour'] >= NOISY_LAST_HOUR
                and e['last_hour'] >= NOISY_FACTOR * e['per_hour']):
            flags.append(f"{sender} -> {receiver}: {e['last_hour']} wakes in the last hour, "
                         f"{e['last_hour'] / max(e['per_hour'], 0.01):.0f}x its usual rate; it just got noisy")
    total_tokens = sum(a['tokens'] for a in agents.values())
    send_less = sorted(((f, t, e) for (f, t), e in edges.items()
                        if f != HUMAN and e['empty_tokens'] >= SEND_LESS_MIN_SHARE * total_tokens and e['empty_tokens']),
                       key=lambda x: -x[2]['empty_tokens'])[:SEND_LESS]
    return {
        'agents': {k: {**v, 'sources': sorted(v['sources'])} for k, v in agents.items()},
        'edges': [{'from': f, 'to': t, **{**e, 'channels': sorted(e['channels'])}} for (f, t), e in edges.items()],
        'send_less': [{'from': f, 'to': t, 'empty': e['empty'], 'wakes': e['wakes'], 'empty_tokens': e['empty_tokens'],
                       'share': round(e['empty_tokens'] / total_tokens, 4) if total_tokens else 0,
                       'text': f"{f} -> {t}: {e['empty']} of {e['wakes']} wakes did no work and cost "
                               f"{e['empty_tokens'] / 1e6:.1f}M tokens"
                               f" ({100 * e['empty_tokens'] / (total_tokens or 1):.0f}% of all input); "
                               "batch these notes or stop sending them"}
                      for f, t, e in send_less],
        'empty_tokens': sum(e['empty_tokens'] for (f, _), e in edges.items() if f != HUMAN),
        'refusals': [{'agent': a, 'command': c, 'why': w, 'advice': notes.get(w, ''), 'count': n}
                     for (a, c, w), n in refusals.most_common()],
        'flags': flags,
    }


def to_json(r, meta):
    return json.dumps({**meta, **r}, indent=2, sort_keys=True)


def to_md(r, meta, show_text=False):
    total = sum(a['tokens'] for a in r['agents'].values())
    lines = [f"# wakegraph: last {meta['hours']} h (since {meta['since']})", '',
             '## Flags', ''] + ([f'- {f}' for f in r['flags']] or ['- none']) + ['']
    lines += ['## Send less', '',
              f"Wakes between agents that did no work cost {r['empty_tokens'] / 1e6:.1f}M tokens "
              f"({100 * r['empty_tokens'] / (total or 1):.0f}% of all input).", '']
    lines += [f"{i}. {x['text']}" for i, x in enumerate(r['send_less'], 1)] or ['- nothing to cut']
    lines += ['']
    lines += ['## Agents', '', '| Agent | Source | Sessions | Calls | Input tokens | Share | Peak context |',
              '|---|---|---|---|---|---|---|']
    for name, a in sorted(r['agents'].items(), key=lambda kv: -kv[1]['tokens']):
        lines.append(f"| {name} | {', '.join(a['sources'])} | {a['sessions']} | {a['calls']} | "
                     f"{a['tokens'] / 1e6:.1f}M | {100 * a['tokens'] / (total or 1):.0f}% | {a['peak'] // 1000}K |")
    lines += ['', '## Who woke whom', '',
              '| From | To | Via | Wakes | Per hour | Last hour | Empty (<= %d calls) | Cost | Cost of empty |%s'
              % (EMPTY_CALLS, ' Last wake |' if show_text else ''),
              '|---|---|---|---|---|---|---|---|---|' + ('---|' if show_text else '')]
    for e in sorted(r['edges'], key=lambda e: -e['tokens']):
        rate = '-' if e['per_hour'] is None else f"{e['per_hour']:.1f}"
        lines.append(f"| {e['from']} | {e['to']} | {', '.join(e['channels'])} | {e['wakes']} | {rate} | {e['last_hour']} | "
                     f"{e['empty']} | {e['tokens'] / 1e6:.2f}M | {e['empty_tokens'] / 1e6:.2f}M |"
                     + (f" {e['sample'].replace('|', '/')} |" if show_text else ''))
    lines += ['', '## Refused commands', '']
    lines += [f"- {x['agent']}: `{x['command']}` x{x['count']}, by {x['why']}" + (f". {x['advice']}" if x['advice'] else '')
              for x in r['refusals']] or ['- none']
    lines += ['', f"Total input tokens: {total / 1e6:.1f}M. A wake's cost is the receiver's input tokens summed over "
              'its calls in that turn, i.e. context x calls.']
    return '\n'.join(lines) + '\n'
