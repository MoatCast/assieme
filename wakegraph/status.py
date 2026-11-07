"""F1 live health: what each agent is doing right now, read from its own logs.

States: working, stuck, waiting on subagents, waiting on you, idle with work waiting, idle.
Only the tail of each log is read, so --watch stays cheap on large sessions.
"""
import datetime as dt
import glob
import json
import os
import re
import shlex
import subprocess

from .adapters import _strip_common_prefix, command_label
from .model import parse_ts

STUCK_AFTER = 15 * 60       # working with no new log row for this long
REPEATS = 4                 # the same command this many times in a row within one turn
GRACE = 120                 # a message this old that started no turn is "work waiting"
SUBAGENT_WINDOW = 3600      # unfinished subagents older than this are abandoned, not running
ASK_TOOLS = {'AskUserQuestion', 'ExitPlanMode'}
TAIL_BYTES = 512 * 1024

ORDER = ['stuck', 'waiting on you', 'idle, work waiting', 'waiting on subagents', 'working', 'idle']


def _tail_rows(path, size=TAIL_BYTES):
    with open(path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        f.seek(max(0, end - size))
        data = f.read().decode('utf-8', 'ignore').splitlines()
    if end > size:
        data = data[1:]  # first line is cut
    rows = []
    for line in data:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if isinstance(o, dict):
            rows.append(o)
    return rows


def _first_row(path):
    with open(path, errors='ignore') as f:
        try:
            return json.loads(f.readline())
        except ValueError:
            return {}


def _send_targets(command):
    """'hc send builder --if idle "x"' -> ['builder']. Messages are never kept, only who they went to."""
    out = []
    for seg in re.split(r'[;&|\n]+', command or ''):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        for i in range(len(words) - 1):
            if os.path.basename(words[i]) == 'hc' and words[i + 1] == 'send':
                rest = words[i + 2:]
                while rest and rest[0].startswith('-'):
                    rest = rest[2:] if rest[0] == '--if' else rest[1:]
                if rest:
                    out.append(rest[0])
    return out


class Agent:
    def __init__(self, name, source, path):
        self.name, self.source, self.path = name, source, path
        self.state = 'idle'
        self.last = None          # datetime of the last log row
        self.turn_start = None    # datetime the current or last turn began
        self.detail = ''
        self.pending = {}         # tool id -> (tool name, label)
        self.cmds = []            # full commands in this turn, for the repeat check
        self.sends = []           # (datetime, target) messages this agent sent
        self.thread = self.parent_thread = None
        self.children = []
        self.herdr = None

    def as_dict(self, now):
        return {'agent': self.name, 'source': self.source, 'state': self.state, 'detail': self.detail,
                'seconds': int((now - self.last).total_seconds()) if self.last else None, 'herdr': self.herdr}


def _claude(agent, rows, show_text):
    for o in rows:
        ts = parse_ts(o.get('timestamp'))
        if not ts:
            continue
        msg = o.get('message') or {}
        content = msg.get('content')
        if o.get('type') == 'assistant':
            agent.last = ts
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get('type') == 'tool_use':
                    cmd = (b.get('input') or {}).get('command') if b.get('name') == 'Bash' else None
                    agent.pending[b.get('id')] = (b.get('name'), command_label(b.get('name'), cmd, show_text))
                    if cmd:
                        agent.cmds.append(cmd)
                        agent.sends += [(ts, t) for t in _send_targets(cmd)]
            if msg.get('stop_reason') == 'end_turn':
                agent.state = 'idle'
            elif msg.get('stop_reason'):
                agent.state = 'working'
        elif o.get('type') == 'user' and not o.get('isMeta'):
            agent.last = ts
            agent.state = 'working'
            text = content if isinstance(content, str) else ' '.join(
                b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text') if isinstance(content, list) else ''
            if text.startswith('[Request interrupted'):  # the person pressed Esc: the turn is over
                agent.state, agent.pending = 'idle', {}
                continue
            if text:
                agent.turn_start, agent.cmds, agent.pending = ts, [], {}
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get('type') == 'tool_result':
                    agent.pending.pop(b.get('tool_use_id'), None)


def _codex(agent, rows, show_text):
    for o in rows:
        p = o.get('payload') or {}
        if o.get('type') == 'session_meta':
            agent.thread = p.get('id')
            src = p.get('source') if isinstance(p.get('source'), dict) else {}
            sub = src.get('subagent') if isinstance(src.get('subagent'), dict) else {}
            spawn = sub.get('thread_spawn') if isinstance(sub.get('thread_spawn'), dict) else {}
            agent.parent_thread = spawn.get('parent_thread_id')
            continue
        ts = parse_ts(o.get('timestamp'))
        if not ts:
            continue
        agent.last = ts
        kind = p.get('type')
        if kind == 'task_started':
            agent.state, agent.turn_start, agent.cmds, agent.pending = 'working', ts, [], {}
        elif kind in ('task_complete', 'turn_aborted'):
            agent.state, agent.pending = 'idle', {}
        elif isinstance(kind, str) and ('approval_request' in kind or 'request_user_input' in kind):
            agent.state, agent.pending = 'working', {'ask': (kind, 'approval')}
        elif kind in ('function_call', 'custom_tool_call'):
            args = p.get('arguments') or p.get('input') or ''
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except ValueError:
                pass
            cmd = (args.get('cmd') or args.get('command')) if isinstance(args, dict) else args
            cmd = ' '.join(map(str, cmd)) if isinstance(cmd, list) else (cmd if isinstance(cmd, str) else '')
            agent.pending = {p.get('call_id'): (p.get('name'), command_label(p.get('name') or '?', cmd, show_text))}
            if cmd:
                agent.cmds.append(cmd)
                agent.sends += [(ts, t) for t in _send_targets(cmd)]
        elif kind in ('function_call_output', 'custom_tool_call_output'):
            agent.pending.pop(p.get('call_id'), None)


def _finish(agent, now):
    """Turn raw turn state into the health state the owner reads."""
    if agent.state != 'working':
        return
    age = (now - agent.last).total_seconds()
    tool = next(iter(agent.pending.values()), None)
    if tool and (tool[0] in ASK_TOOLS or tool[1] == 'approval'):
        agent.state, agent.detail = 'waiting on you', f'asked {_ago(age)} ago'
    elif age > STUCK_AFTER:
        agent.state = 'stuck'
        agent.detail = f'silent {_ago(age)}, last {tool[1]}' if tool else f'no reply for {_ago(age)} (stuck, or the session closed)'
    elif len(agent.cmds) >= REPEATS and len(set(agent.cmds[-REPEATS:])) == 1:
        agent.state = 'stuck'
        agent.detail = f"same command x{REPEATS}: {tool[1] if tool else 'Bash'}"
    else:
        agent.detail = f'running {tool[1]}' if tool else 'thinking'


def _ago(seconds):
    m = int(seconds // 60)
    return f'{m} min' if m < 120 else f'{m // 60} h'


def collect(claude_dir, codex_dir, now, hours=12, show_text=False, herdr=None):
    """Read recent logs -> list of Agent with health states. herdr: optional `hc ls --json` terminals."""
    cutoff = now.timestamp() - hours * 3600
    agents = []

    mains = [p for p in glob.glob(os.path.join(claude_dir, '*', '*.jsonl')) if os.path.getmtime(p) >= cutoff]
    latest = {}
    for p in mains:  # one row per project: its most recent session
        d = os.path.basename(os.path.dirname(p))
        if d not in latest or os.path.getmtime(p) > os.path.getmtime(latest[d]):
            latest[d] = p
    names = _strip_common_prefix(sorted(latest))
    for d, path in latest.items():
        a = Agent(names[d], 'claude-code', path)
        _claude(a, _tail_rows(path), show_text)
        for sp in glob.glob(os.path.join(path[:-len('.jsonl')], 'subagents', '*.jsonl')):
            if now.timestamp() - os.path.getmtime(sp) > SUBAGENT_WINDOW:
                continue
            child = Agent(f'{a.name}/sub', 'claude-code', sp)
            _claude(child, _tail_rows(sp), show_text)
            if child.last:
                a.children.append(child)
        if a.last:
            agents.append(a)

    by_thread, codex_agents = {}, {}
    for path in glob.glob(os.path.join(codex_dir, '**', '*.jsonl'), recursive=True):
        if os.path.getmtime(path) < cutoff:
            continue
        meta = _first_row(path).get('payload') or {}
        a = Agent('', 'codex', path)
        _codex(a, [_first_row(path)] + _tail_rows(path), show_text)
        if not a.last:
            continue
        src = meta.get('source') if isinstance(meta.get('source'), dict) else {}
        kind = 'subagent' if a.parent_thread else ((src.get('subagent') or {}).get('other') if isinstance(src.get('subagent'), dict) else '')
        a.name = f"codex:{os.path.basename(meta.get('cwd') or '') or '?'}" + (f'/{kind}' if kind else '')
        a.cwd = meta.get('cwd')
        by_thread[a.thread] = a
        if kind == 'subagent':
            continue
        if a.name not in codex_agents or a.last > codex_agents[a.name].last:
            codex_agents[a.name] = a
    for a in by_thread.values():
        if a.parent_thread in by_thread:
            by_thread[a.parent_thread].children.append(a)
    agents += [a for a in codex_agents.values() if not (a.name.endswith('/guardian') and a.state != 'working')]

    for a in agents + [c for a in agents for c in a.children]:
        _finish(a, now)
    for a in agents:
        busy = [c for c in a.children if c.state in ('working', 'waiting on you')]
        lost = [c for c in a.children if c.state == 'stuck']
        if a.state == 'idle' and busy:
            a.state, a.detail = 'waiting on subagents', f'{len(busy)} running'
        elif a.state == 'idle' and lost:
            a.state, a.detail = 'stuck', f'subagent {lost[0].detail}'

    _work_waiting(agents, now, herdr)
    if herdr:
        _apply_herdr(agents, herdr)
    agents.sort(key=lambda a: (ORDER.index(a.state), a.name))
    return agents


def _work_waiting(agents, now, herdr):
    """A message sent to an idle agent after its last turn began, that started no turn: work is waiting."""
    alias = {a.name: a for a in agents}
    for a in agents:
        alias.setdefault(a.name.split(':')[-1], a)
    for t in herdr or []:  # herdr addresses and agent names point at a cwd we can match
        a = _by_cwd(agents, t.get('cwd'))
        if a:
            for k in (t.get('address'), t.get('agent')):
                if k:
                    alias.setdefault(k, a)
    for sender in agents:
        for ts, target in sender.sends:
            r = alias.get(target)
            if (r and r is not sender and r.state == 'idle' and (now - ts).total_seconds() > GRACE
                    and (r.turn_start is None or ts > r.turn_start) and (r.last is None or ts > r.last)):
                r.state = 'idle, work waiting'
                r.detail = f'message from {sender.name} {_ago((now - ts).total_seconds())} ago started no turn'


def _by_cwd(agents, cwd):
    if not cwd:
        return None
    key = re.sub(r'[^A-Za-z0-9]', '-', cwd)
    for a in agents:
        if a.source == 'claude-code' and os.path.basename(os.path.dirname(a.path)) == key:
            return a
    for a in agents:
        if getattr(a, 'cwd', None) == cwd:
            return a
    return None


def _apply_herdr(agents, terminals):
    """herdr's own status is shown beside ours. Only 'blocked' adds what logs cannot see: a permission prompt."""
    for t in terminals:
        a = _by_cwd(agents, t.get('cwd'))
        if not a or t.get('state') != 'live':
            continue
        st = t.get('status') if isinstance(t.get('status'), dict) else {}
        a.herdr = st.get('state') or 'unreported'  # the agent never sent herdr its status
        if a.herdr == 'blocked' and a.state in ('working', 'stuck', 'idle'):
            a.state, a.detail = 'waiting on you', f"herdr: {st.get('kind') or 'prompt'}"


def _hc_json(*args):
    out = subprocess.run(['hc', *args, '--json'], capture_output=True, text=True, timeout=5, check=False).stdout
    return json.loads(out)


def herdr_terminals():
    """Live herdr-core terminals with their reported status; None when hc is missing. Never fatal."""
    try:
        terminals = _hc_json('ls').get('terminals') or []
        for t in terminals:
            if t.get('state') == 'live':
                t['status'] = (_hc_json('get', t['address']) or {}).get('status')
        return terminals
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError, KeyError, TypeError):
        return None


def summary(agents):
    counts = {s: sum(a.state == s for a in agents) for s in ORDER}
    head = ', '.join(f'{n} {s}' for s, n in counts.items() if n) or 'no active agents'
    alerts = [f'{a.state}: {a.name} ({a.detail})' for a in agents if a.state in ORDER[:3]]
    return head, alerts


def to_text(agents, now):
    head, alerts = summary(agents)
    lines = [f"wakegraph status  {now.astimezone().strftime('%H:%M')}  {head}"] + [f'  ! {x}' for x in alerts] + ['']
    rows = [('STATE', 'AGENT', 'FOR', 'DETAIL', 'HERDR')]
    for a in agents:
        rows.append((a.state, a.name, _ago((now - a.last).total_seconds()), a.detail, a.herdr or '-'))
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    lines += ['  '.join(c.ljust(w) for c, w in zip(r[:4], widths)) + '  ' + r[4] for r in rows]
    return '\n'.join(lines) + '\n'


def to_json(agents, now):
    head, alerts = summary(agents)
    return json.dumps({'at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'summary': head, 'alerts': alerts,
                       'agents': [a.as_dict(now) for a in agents]}, indent=2)


def now_utc():
    return dt.datetime.now(dt.timezone.utc)
