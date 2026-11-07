"""Sessions -> per-agent totals, the wake graph, refusals and flags; rendered as Markdown or JSON."""
import collections
import json

from .model import HUMAN

EMPTY_CALLS = 2              # a wake the receiver answers in at most this many calls did no work
EMPTY_WAKES_PER_EDGE = 20    # flag a sender -> receiver pair with more empty wakes than this
REPEATED_REFUSALS = 3        # flag a command refused at least this often for one agent
PEAK_CONTEXT = 250_000       # flag a context above this: compaction is not keeping up


def aggregate(sessions):
    agents = collections.defaultdict(lambda: {'sessions': 0, 'calls': 0, 'tokens': 0, 'output': 0, 'peak': 0,
                                              'sources': set()})
    edges = collections.defaultdict(lambda: {'wakes': 0, 'empty': 0, 'tokens': 0, 'calls': 0, 'channels': set(),
                                             'sample': ''})
    refusals = collections.Counter()
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
            e['calls'] += t.calls
            e['channels'].add(t.channel)
            e['sample'] = t.text or e['sample']
        for cmd, n in s.refusals.items():
            refusals[(s.agent, cmd)] += n

    flags = []
    for name, a in agents.items():
        if a['peak'] > PEAK_CONTEXT:
            flags.append(f"{name}: context peaked at {a['peak'] // 1000}K tokens; compact or restart it")
    for (sender, receiver), e in edges.items():
        if sender != HUMAN and e['empty'] > EMPTY_WAKES_PER_EDGE:
            flags.append(f"{sender} -> {receiver}: {e['empty']} of {e['wakes']} wakes needed <= {EMPTY_CALLS} calls "
                         f"({e['tokens'] / 1e6:.1f}M tokens on all of them); stop the routine message or batch it")
    for (agent, cmd), n in refusals.items():
        if n >= REPEATED_REFUSALS:
            flags.append(f"{agent}: '{cmd}' refused {n} times; fix the rule or the brief instead of retrying")
    return {
        'agents': {k: {**v, 'sources': sorted(v['sources'])} for k, v in agents.items()},
        'edges': [{'from': f, 'to': t, **{**e, 'channels': sorted(e['channels'])}} for (f, t), e in edges.items()],
        'refusals': [{'agent': a, 'command': c, 'count': n} for (a, c), n in refusals.most_common()],
        'flags': flags,
    }


def to_json(r, meta):
    return json.dumps({**meta, **r}, indent=2, sort_keys=True)


def to_md(r, meta, show_text=False):
    total = sum(a['tokens'] for a in r['agents'].values())
    lines = [f"# wakegraph: last {meta['hours']} h (since {meta['since']})", '',
             '## Flags', ''] + ([f'- {f}' for f in r['flags']] or ['- none']) + ['']
    lines += ['## Agents', '', '| Agent | Source | Sessions | Calls | Input tokens | Share | Peak context |',
              '|---|---|---|---|---|---|---|']
    for name, a in sorted(r['agents'].items(), key=lambda kv: -kv[1]['tokens']):
        lines.append(f"| {name} | {', '.join(a['sources'])} | {a['sessions']} | {a['calls']} | "
                     f"{a['tokens'] / 1e6:.1f}M | {100 * a['tokens'] / (total or 1):.0f}% | {a['peak'] // 1000}K |")
    lines += ['', '## Who woke whom', '',
              '| From | To | Via | Wakes | Empty (<= %d calls) | Cost (input tokens) |%s' % (EMPTY_CALLS, ' Last wake |' if show_text else ''),
              '|---|---|---|---|---|---|' + ('---|' if show_text else '')]
    for e in sorted(r['edges'], key=lambda e: -e['tokens']):
        lines.append(f"| {e['from']} | {e['to']} | {', '.join(e['channels'])} | {e['wakes']} | {e['empty']} | "
                     f"{e['tokens'] / 1e6:.2f}M |" + (f" {e['sample'].replace('|', '/')} |" if show_text else ''))
    lines += ['', '## Refused commands', '']
    lines += [f"- {x['agent']}: `{x['command']}` x{x['count']}" for x in r['refusals']] or ['- none']
    lines += ['', f"Total input tokens: {total / 1e6:.1f}M. A wake's cost is the receiver's input tokens summed over "
              'its calls in that turn, i.e. context x calls.']
    return '\n'.join(lines) + '\n'
