"""F1 live health and F2 stall alarm: what each agent is doing right now, read from its own logs.

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

from . import rules
from .adapters import CODEX_REFUSED, _strip_common_prefix, command_label, wake_sender
from .model import HUMAN, parse_ts

STUCK_AFTER = 15 * 60       # working with no new log row for this long
REPEATS = 4                 # the same command this many times in a row within one turn
GRACE = 120                 # a message this old that started no turn is "work waiting"
SUBAGENT_WINDOW = 3600      # unfinished subagents older than this are abandoned, not running
ASK_TOOLS = {'AskUserQuestion', 'ExitPlanMode'}
TAIL_BYTES = 512 * 1024

ORDER = ['stuck', 'waiting on you', 'idle, work waiting', 'waiting on subagents', 'working', 'idle', 'exited']
EXIT_GRACE = 120            # an agent seen this recently is never called exited


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


VALUE_FLAGS = {'--if', '--output', '--timeout', '--children', '--lines'}


def _hc_targets(command, verb):
    """'hc send builder --if idle "x"' -> ['builder'] for verb 'send'. Messages are never kept, only who they went to."""
    out = []
    for seg in re.split(r'[;&|\n]+', command or ''):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        for i in range(len(words) - 1):
            if os.path.basename(words[i]) == 'hc' and words[i + 1] == verb:
                rest = words[i + 2:]
                while rest and rest[0].startswith('-'):
                    rest = rest[2:] if rest[0] in VALUE_FLAGS else rest[1:]
                if rest:
                    out.append(rest[0])
    return out


def _send_targets(command):
    return _hc_targets(command, 'send')


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
        self.sent_at = None       # when the message it waits on was sent
        self.wakes = []           # (datetime, sender) messages from other agents that started a turn
        self.waits_on = []        # F2: agent names, or 'you', this agent is blocked on
        self.peers = set()        # agents it messaged or was woken by: its team
        self.thread = self.parent_thread = None
        self.children = []
        self.herdr = None
        self.cwd = None           # working folder, to tell a running agent from a closed one
        self.asked = False        # its latest reply ends with a question
        self.turn_by = None       # who started its current or last turn: an agent name or HUMAN
        self.ask_kind = ''        # F9: what it needs from you
        self.ask_since = None     # F9: since when
        self.context = 0          # A2: input tokens of its latest call, i.e. what every wake re-reads
        self.refused = []         # A5: (datetime, label, why, advice) refusals seen in the log tail

    def as_dict(self, now):
        return {'agent': self.name, 'source': self.source, 'state': self.state, 'detail': self.detail,
                'seconds': int((now - self.last).total_seconds()) if self.last else None, 'herdr': self.herdr}


def _claude(agent, rows, show_text, rules_for=lambda cwd: []):
    for o in rows:
        ts = parse_ts(o.get('timestamp'))
        if not ts:
            continue
        msg = o.get('message') or {}
        content = msg.get('content')
        agent.cwd = o.get('cwd') or agent.cwd
        if o.get('type') == 'assistant':
            agent.last = ts
            u = msg.get('usage') or {}
            if u:
                agent.context = (u.get('input_tokens', 0) + u.get('cache_read_input_tokens', 0)
                                 + u.get('cache_creation_input_tokens', 0)) or agent.context
            blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
            texts = [b.get('text', '') for b in blocks if b.get('type') == 'text' and b.get('text', '').strip()]
            if texts:  # only whether it ends with a question is kept, never the text
                agent.asked, agent.ask_since = texts[-1].rstrip().endswith('?'), ts
            if any(b.get('type') == 'tool_use' for b in blocks):
                agent.asked = False
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get('type') == 'tool_use':
                    cmd = (b.get('input') or {}).get('command') if b.get('name') == 'Bash' else None
                    agent.pending[b.get('id')] = (b.get('name'), command_label(b.get('name'), cmd, show_text), cmd)
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
                agent.turn_start, agent.cmds, agent.pending, agent.asked = ts, [], {}, False
                _note_wake(agent, ts, text)
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get('type') == 'tool_result':
                    tool = agent.pending.pop(b.get('tool_use_id'), None)
                    out = b.get('content') if isinstance(b.get('content'), str) else json.dumps(b.get('content'))
                    if b.get('is_error') and tool and rules.REFUSED.search(out or ''):
                        why, advice = rules.explain(out, tool[0], tool[2], rules_for(agent.cwd))
                        agent.refused.append((ts, tool[1], why, advice))


def _note_wake(agent, ts, text):
    sender, _ = wake_sender(text)
    agent.turn_by = sender
    if sender != HUMAN:
        agent.wakes.append((ts, sender))


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
        if kind == 'message' and p.get('role') == 'user':
            text = ' '.join(b.get('text', '') for b in p.get('content') or [] if isinstance(b, dict))
            if not text.lstrip().startswith(('<environment_context>', '# AGENTS.md', '<user_instructions>')):
                _note_wake(agent, ts, text)
                agent.asked = False
        elif kind == 'message' and p.get('role') == 'assistant':
            text = ' '.join(b.get('text', '') for b in p.get('content') or [] if isinstance(b, dict)).rstrip()
            if text:
                agent.asked, agent.ask_since = text.endswith('?'), ts
        elif kind == 'task_started':
            agent.state, agent.turn_start, agent.cmds, agent.pending = 'working', ts, [], {}
        elif kind in ('task_complete', 'turn_aborted'):
            agent.state, agent.pending = 'idle', {}
        elif isinstance(kind, str) and ('approval_request' in kind or 'request_user_input' in kind):
            agent.state, agent.pending, agent.ask_since = 'working', {'ask': (kind, 'approval', None)}, ts
        elif kind in ('function_call', 'custom_tool_call'):
            args = p.get('arguments') or p.get('input') or ''
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except ValueError:
                pass
            cmd = (args.get('cmd') or args.get('command')) if isinstance(args, dict) else args
            cmd = ' '.join(map(str, cmd)) if isinstance(cmd, list) else (cmd if isinstance(cmd, str) else '')
            agent.pending = {p.get('call_id'): (p.get('name'), command_label(p.get('name') or '?', cmd, show_text), cmd)}
            if cmd:
                agent.cmds.append(cmd)
                agent.sends += [(ts, t) for t in _send_targets(cmd)]
        elif kind in ('function_call_output', 'custom_tool_call_output'):
            tool = agent.pending.pop(p.get('call_id'), None)
            out = p.get('output') if isinstance(p.get('output'), str) else json.dumps(p.get('output'))
            if tool and CODEX_REFUSED.search(out or ''):
                agent.refused.append((ts, tool[1], 'Codex sandbox or approval', ''))
        elif kind == 'token_count' and isinstance(p.get('info'), dict):
            agent.context = (p['info'].get('last_token_usage') or {}).get('input_tokens') or agent.context


def _finish(agent, now):
    """Turn raw turn state into the health state the owner reads."""
    if agent.state != 'working':
        return
    age = (now - agent.last).total_seconds()
    tool = next(iter(agent.pending.values()), None)
    if tool and (tool[0] in ASK_TOOLS or tool[1] == 'approval'):
        agent.state, agent.detail = 'waiting on you', f'asked {_ago(age)} ago'
        agent.ask_kind = {'AskUserQuestion': 'a question', 'ExitPlanMode': 'a plan to approve'}.get(tool[0], 'an approval')
        agent.ask_since = agent.last
    elif tool and re.search(r'\bhc\s+wait\b', tool[2] or ''):  # waiting is its job: never "stuck" by silence
        agent.detail = 'waiting (hc wait)'
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


def collect(claude_dir, codex_dir, now, hours=12, show_text=False, herdr=None, running=None, rules_for=None):
    """Read recent logs -> list of Agent with health states. herdr: optional `hc ls --json` terminals."""
    cutoff = now.timestamp() - hours * 3600
    agents = []
    if rules_for is None:  # the agent's own settings and the user's; live view adds running agents' --settings files
        user = os.path.expanduser('~/.claude/settings.json')
        cache = {}
        rules_for = lambda cwd: cache.setdefault(cwd, rules.load(cwd, user))

    mains = [p for p in glob.glob(os.path.join(claude_dir, '*', '*.jsonl')) if os.path.getmtime(p) >= cutoff]
    latest = {}
    for p in mains:  # one row per project: its most recent session
        d = os.path.basename(os.path.dirname(p))
        if d not in latest or os.path.getmtime(p) > os.path.getmtime(latest[d]):
            latest[d] = p
    names = _strip_common_prefix(sorted(latest))
    for d, path in latest.items():
        a = Agent(names[d], 'claude-code', path)
        _claude(a, _tail_rows(path), show_text, rules_for)
        for sp in glob.glob(os.path.join(path[:-len('.jsonl')], 'subagents', '*.jsonl')):
            if now.timestamp() - os.path.getmtime(sp) > SUBAGENT_WINDOW:
                continue
            child = Agent(f'{a.name}/sub', 'claude-code', sp)
            _claude(child, _tail_rows(sp), show_text, rules_for)
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

    if running is not None:  # a folder with no Claude or Codex process: the session was closed
        for a in agents:
            if (a.cwd and a.cwd not in running and (now - a.last).total_seconds() > EXIT_GRACE
                    and a.state in ('idle', 'stuck', 'idle, work waiting')):
                a.state, a.detail = 'exited', ''
    for a in agents:  # it ended its turn with a question, in a turn a person started: the person owes an answer
        if a.state == 'idle' and a.asked and a.turn_by == HUMAN:
            a.state, a.ask_kind = 'waiting on you', 'a question in its reply'
            a.detail = f'asked you a question {_ago((now - a.ask_since).total_seconds())} ago'
    alias = _aliases(agents, herdr)
    _work_waiting(agents, now, alias)
    if herdr:
        _apply_herdr(agents, herdr)
    _wait_edges(agents, alias)
    agents.sort(key=lambda a: (ORDER.index(a.state), a.name))
    return agents


def _aliases(agents, herdr):
    """Every name an agent is addressed by: our name, its short form, and herdr's address and agent name."""
    alias = {a.name: a for a in agents}
    for a in agents:
        alias.setdefault(a.name.split(':')[-1], a)
        alias.setdefault(a.name.split('-')[-1], a)
    for t in herdr or []:
        a = _by_cwd(agents, t.get('cwd'))
        if a:
            for k in (t.get('address'), t.get('agent')):
                if k:
                    alias.setdefault(k, a)
    return alias


def _work_waiting(agents, now, alias):
    """A message sent to an idle agent after its last turn began, that started no turn: work is waiting."""
    for sender in agents:
        for ts, target in sender.sends:
            r = alias.get(target)
            if r and r is not sender:
                sender.peers.add(r)
                r.peers.add(sender)
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
            a.ask_kind = {'permission': 'a permission prompt', 'question': 'a question', 'auth': 'a sign-in'}.get(
                st.get('kind'), 'a prompt')
            a.ask_since = a.ask_since or a.last


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
    lines = [f"wakegraph status  {now.astimezone().strftime('%H:%M')}  {head}"]
    lines += [f"  !! {s['text']}" for s in stalls(agents, now)] + [f'  ! {x}' for x in alerts] + ['']
    return '\n'.join(lines + table(agents, now)) + '\n'


def table(agents, now):
    rows = [('STATE', 'AGENT', 'FOR', 'DETAIL', 'HERDR')]
    for a in agents:
        rows.append((a.state, a.name, _ago((now - a.last).total_seconds()), a.detail, a.herdr or '-'))
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    return ['  '.join(c.ljust(w) for c, w in zip(r[:4], widths)) + '  ' + r[4] for r in rows]


def to_json(agents, now):
    head, alerts = summary(agents)
    return json.dumps({'at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'summary': head, 'alerts': alerts,
                       'queue': owner_queue(agents, now),
                       'stalls': stalls(agents, now),
                       'agents': [a.as_dict(now) for a in agents]}, indent=2)


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def _wait_edges(agents, alias):
    """F2: who each agent is blocked on. Also links agents that woke each other into one team."""
    for a in agents:
        for _, sender in a.wakes:
            s = alias.get(sender)
            if s and s is not a:
                a.peers.add(s)
                s.peers.add(a)
    for a in agents:
        tool = next(iter(a.pending.values()), None)
        waited = [alias[t] for t in _hc_targets(tool[2] if tool else '', 'wait') if t in alias]
        if a.state == 'waiting on you':
            a.waits_on = ['you']
        elif a.state == 'working' and waited:  # `hc wait <agent>`: busy on paper, blocked in fact
            a.waits_on = [w.name for w in waited]
            a.detail = f"waiting for {', '.join(a.waits_on)} (hc wait)"
        elif a.state == 'idle':
            # its last message this turn went to X, and X has not answered since: it may wait on X
            sends = [(ts, alias.get(t)) for ts, t in a.sends if alias.get(t) not in (None, a)
                     and (a.turn_start is None or ts >= a.turn_start)]
            if sends:
                ts, x = max(sends, key=lambda s: s[0])
                if not any(w_ts > ts and alias.get(w) is x for w_ts, w in a.wakes):
                    a.waits_on = [x.name]
                    a.sent_at = ts
    # X took A's message and finished with nothing open: that was a report, not a request. Repeat until stable.
    changed = True
    while changed:
        changed = False
        for a in agents:
            x = alias.get(a.waits_on[0]) if a.waits_on and a.state == 'idle' else None
            if x and x.state == 'idle' and not x.waits_on and (
                    any(w_ts >= a.sent_at and alias.get(w) is a for w_ts, w in x.wakes)
                    or (x.turn_start and x.turn_start > a.sent_at)):  # it has had a turn since: nothing pending
                a.waits_on, changed = [], True


def stalls(agents, now):
    """F2: teams where nobody is making progress but work is open. Returns one description per stalled team."""
    by = {a.name: a for a in agents}
    seen, out = set(), []
    for start in agents:
        if start.name in seen or not start.peers:
            continue
        team, todo = [], [start]
        while todo:  # the team: everyone linked by messages
            a = todo.pop()
            if a.name in seen:
                continue
            seen.add(a.name)
            team.append(a)
            todo += [p for p in a.peers if p.name not in seen]
        if len(team) < 2:
            continue
        moving = [a for a in team if a.state in ('working', 'waiting on subagents') and not a.waits_on]
        blocked = [a for a in team if a.waits_on or a.state in ('stuck', 'idle, work waiting', 'waiting on you')]
        quiet = min((now - a.last).total_seconds() for a in team)
        if moving or not blocked or quiet < GRACE:
            continue
        roots = {}
        for a in team:
            r, looped = _root(a, by)
            if r is not a or a.state != 'idle':
                roots.setdefault((r, looped), []).append(a)
        parts = []
        for (r, looped), deps in sorted(roots.items(), key=lambda kv: -len(kv[1])):
            others = sorted(d.name for d in deps if d is not r)
            who = (f"{len(others)} {'agents wait' if len(others) > 1 else 'agent waits'} on {r.name} ({', '.join(others)}); "
                   if others else '')
            parts.append(who + _blocker(r, looped))
        out.append({'team': sorted(a.name for a in team), 'quiet_seconds': int(quiet),
                    'roots': [{'agent': r.name, 'state': r.state, 'looped': looped,
                               'waiting': sorted(d.name for d in deps if d is not r)}
                              for (r, looped), deps in roots.items()],
                    'text': f"Team stalled for {_ago(quiet)}: " + '; '.join(parts)})
    return out


def _root(a, by):
    """Follow waits-on links to the agent at the end of the chain -> (agent, True if the chain loops)."""
    path = [a]
    while path[-1].waits_on and path[-1].waits_on[0] in by:
        nxt = by[path[-1].waits_on[0]]
        if nxt in path:
            return nxt, True
        path.append(nxt)
    return path[-1], False


def _blocker(r, looped):
    if r.state == 'waiting on you':
        return f'{r.name} waits on you ({r.detail})'
    if r.state == 'stuck':
        return f'{r.name} is stuck ({r.detail})'
    if r.state == 'exited':
        return f'{r.name} has exited (its session is closed)'
    if r.state == 'idle, work waiting':
        return f'{r.name} never started on its message ({r.detail})'
    if looped:
        return f'{r.name} and {r.waits_on[0]} wait on each other'
    return f'{r.name} is idle with nothing to do'


def running_cwds():
    """Working folders of running Claude Code and Codex processes, or None when that can't be told. Never fatal."""
    try:
        ps = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    pids = [line.split(None, 1)[0] for line in ps.splitlines()
            if re.search(r'(^|/)(claude|codex)(\s|$)', (line.strip().split(None, 1) + [''])[1])]
    if not pids:
        return set()
    if os.path.isdir('/proc'):  # Linux
        out = set()
        for pid in pids:
            try:
                out.add(os.readlink(f'/proc/{pid}/cwd'))
            except OSError:
                pass
        return out
    try:  # macOS: one lsof call for all of them
        res = subprocess.run(['lsof', '-a', '-d', 'cwd', '-p', ','.join(pids), '-Fn'],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return {ln[1:] for ln in res.stdout.splitlines() if ln.startswith('n')}


def owner_queue(agents, now):
    """F9: decisions that are truly the owner's, each with how long it has waited and which agents it holds up."""
    by = {a.name: a for a in agents}
    blocks = {}
    for a in agents:
        if a.waits_on and a.waits_on != ['you']:
            r, _ = _root(a, by)
            if r is not a and r.state == 'waiting on you':
                blocks.setdefault(r.name, []).append(a.name)
    items = []
    for a in agents:
        if a.state == 'waiting on you':
            since = a.ask_since or a.last
            items.append({'agent': a.name, 'kind': a.ask_kind or 'an answer', 'seconds': int((now - since).total_seconds()),
                          'blocks': sorted(blocks.get(a.name, [])), 'key': f"{a.name}|{a.ask_kind}|{since.isoformat()}"})
    items.sort(key=lambda i: (-len(i['blocks']), -i['seconds']))
    return items
