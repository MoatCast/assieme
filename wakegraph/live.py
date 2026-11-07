"""The live view: what `wakegraph` opens with no arguments.

Team health, stall alarms and what needs you, each alert with a ready fix. A fix that wakegraph can carry out
(today: a nudge through `hc send`) takes one key and a y to confirm. Nothing acts on its own. Every action is
written to the action log and shown in the view.
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from . import adapters, report, status

REFRESH = 5               # seconds between redraws
COST_EVERY = 600          # seconds between recomputing the 24 h cost line
NOTIFY_MAX_AGE = 2 * 3600 # older items are shown, but never pop up a notification
WATCH_EVERY = 15          # seconds between checks in `wakegraph watch`


@dataclass
class Suggestion:
    text: str             # the alert, in plain words
    agent: str
    address: str = None   # herdr terminal to act on
    action: str = None    # 'nudge' when wakegraph can carry out the fix
    message: str = ''     # what the nudge says
    hint: str = ''        # the fix, when it is the person's to make
    yours: bool = False   # a decision only the owner can make


def log_path():
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'wakegraph', 'actions.jsonl')


def addresses(agents, terminals):
    """Agent name -> herdr terminal address, matched by working folder."""
    out = {}
    for t in terminals or []:
        a = status._by_cwd(agents, t.get('cwd'))
        if a and t.get('state') == 'live' and t.get('address'):
            out.setdefault(a.name, t['address'])
    return out


def suggest(agents, stalls, addr, queue=()):
    """Alerts -> suggestions, most urgent first: stall blockers, then what waits on you, stuck agents, lost messages."""
    by = {a.name: a for a in agents}
    out, seen = [], set()

    def add(s):
        if (s.agent, s.action) not in seen:
            seen.add((s.agent, s.action))
            out.append(s)

    for st in stalls:
        for root in st.get('roots', []):
            r = by.get(root['agent'])
            if r and r.state == 'idle' and not r.waits_on and root['waiting']:
                names = ', '.join(root['waiting'])
                add(Suggestion(f"{r.name} is idle while {len(root['waiting'])} agent(s) wait on it ({names})", r.name,
                               addr.get(r.name), 'nudge',
                               f"wakegraph: {names} are waiting on you. Check your messages and reply to them."))
    for q in queue or [{'agent': a.name, 'kind': a.detail, 'seconds': None, 'blocks': []}
                       for a in agents if a.state == 'waiting on you']:
        where = f"hc attach {addr[q['agent']]}" if q['agent'] in addr else 'its terminal'
        waited = '' if q['seconds'] is None else f", waiting {status._ago(q['seconds'])}"
        held = f"; holds up {', '.join(q['blocks'])}" if q['blocks'] else ''
        add(Suggestion(f"{q['agent']}: {q['kind']}{waited}{held}", q['agent'], addr.get(q['agent']),
                       hint=f'answer it in {where}', yours=True))
    for a in agents:
        if a.state == 'stuck':
            add(Suggestion(f'{a.name} is stuck: {a.detail}', a.name, addr.get(a.name), 'nudge', _nudge_text(a)))
    for a in agents:
        if a.state == 'idle, work waiting':
            add(Suggestion(f'{a.name} has work waiting: {a.detail}', a.name, addr.get(a.name), 'nudge',
                           'wakegraph: a message for you may not have started a turn. Check your latest messages and act on them.'))
    return out


def _nudge_text(a):
    if a.detail.startswith('same command'):
        return ('wakegraph: you have run the same command several times in a row. Stop retrying it. '
                'Say what you are trying to do, then try a different approach.')
    if a.detail.startswith('subagent'):
        return 'wakegraph: one of your subagents has gone silent. Check it or stop it, then carry on.'
    if a.detail.startswith('no reply'):
        return 'wakegraph: you have not answered for a while. Reply with your status: done, working on what, or blocked on what.'
    return ('wakegraph: no progress for a while. If a run is slow, narrow it. '
            'If you are stuck, say what is blocking you.')


def run_action(s, runner=None, log=None, now=None):
    """Carry out a suggestion's action. Returns (ok, detail) and appends it to the action log."""
    if s.action != 'nudge':
        return False, 'nothing to do automatically'
    if not s.address:
        ok, detail = False, 'not running in herdr, so wakegraph cannot reach it'
    else:
        runner = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=60))
        try:
            res = runner(['hc', 'send', s.address, s.message])
            ok = res.returncode == 0
            detail = ((res.stdout or res.stderr or '').strip().splitlines() or ['sent' if ok else 'failed'])[-1][:120]
        except (OSError, subprocess.SubprocessError) as e:
            ok, detail = False, str(e)[:120]
    _log(log or log_path(), {'at': (now or status.now_utc()).strftime('%Y-%m-%dT%H:%M:%SZ'), 'action': s.action,
                             'agent': s.agent, 'address': s.address, 'reason': s.text, 'ok': ok, 'detail': detail})
    return ok, detail


def _log(path, entry):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    except OSError:
        pass  # a log that cannot be written must not stop the view


def recent_actions(path=None, n=5):
    try:
        with open(path or log_path()) as f:
            lines = f.readlines()[-n:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def cost_line(claude_dir, codex_dir, now):
    """One line on what wakes cost in the last 24 h, and the pair to cut first."""
    since = now - dt.timedelta(hours=24)
    sessions = []
    if os.path.isdir(claude_dir):
        sessions += adapters.claude_code(claude_dir, since)
    if os.path.isdir(codex_dir):
        sessions += adapters.codex(codex_dir, since)
    r = report.aggregate(sessions, now, 24)
    total = sum(a['tokens'] for a in r['agents'].values())
    if not total:
        return ''
    line = (f"Last 24 h: wakes that did no work cost {r['empty_tokens'] / 1e6:.0f}M tokens "
            f"({100 * r['empty_tokens'] / total:.0f}% of input).")
    if r['send_less']:
        x = r['send_less'][0]
        line += f" Cut first: {x['from']} -> {x['to']} ({x['empty_tokens'] / 1e6:.0f}M). Details: wakegraph report"
    return line


def render(agents, stalls, suggestions, now, cost='', actions=(), prompt=''):
    head, _ = status.summary(agents)
    lines = [f"wakegraph  {now.astimezone().strftime('%H:%M')}  {head}", '']
    lines += [f"!! {s['text']}" for s in stalls]
    shown = list(enumerate(suggestions[:9], 1))
    mine = [(i, s) for i, s in shown if s.yours]
    other = [(i, s) for i, s in shown if not s.yours]
    lines += [f'Needs you ({len(mine)}):' if mine else 'Nothing needs you right now.']
    lines += [f'  [{i}] {s.text}  -> {s.hint}' for i, s in mine]
    if other:
        lines += ['Other alerts:']
        for i, s in other:
            fix = f'press {i} to nudge' if s.address else 'nudge it yourself (not in herdr)'
            lines.append(f'  [{i}] {s.text}  -> {fix}')
    lines += ['']
    if cost:
        lines += [cost, '']
    lines += status.table(agents, now)
    if actions:
        lines += ['', 'Recent actions:']
        lines += [f"  {x['at'][11:16]}  {x['action']} {x['agent']}: {'ok' if x['ok'] else 'failed'} ({x['detail']})"
                  for x in actions]
    lines += ['', prompt or 'keys: 1-9 act on an alert · r refresh · q quit']
    return '\n'.join(lines) + '\n'


def run(claude_dir, codex_dir, hours=12, use_herdr=True):
    """The interactive loop. Without a terminal (piped output), print one snapshot and stop."""
    def snapshot():
        now = status.now_utc()
        terminals = status.herdr_terminals() if use_herdr else None
        agents = status.collect(claude_dir, codex_dir, now, hours, herdr=terminals, running=status.running_cwds())
        stalls = status.stalls(agents, now)
        queue = status.owner_queue(agents, now)
        announce(queue, stalls, now)
        return now, agents, stalls, suggest(agents, stalls, addresses(agents, terminals), queue)

    cost, cost_at = '', None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        now, agents, stalls, sugg = snapshot()
        sys.stdout.write(render(agents, stalls, sugg, now, prompt=' '))
        return 0

    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    note = ''
    try:
        while True:
            now, agents, stalls, sugg = snapshot()
            if cost_at is None or (now - cost_at).total_seconds() > COST_EVERY:
                cost, cost_at = cost_line(claude_dir, codex_dir, now), now
            draw = lambda prompt='': sys.stdout.write('\033[H\033[2J' + render(
                agents, stalls, sugg, now, cost, recent_actions(), prompt or note))
            draw()
            sys.stdout.flush()
            ready, _, _ = select.select([sys.stdin], [], [], REFRESH)
            if not ready:
                continue
            key = sys.stdin.read(1)
            note = ''
            if key in ('q', '\x1b'):
                return 0
            if key.isdigit() and 0 < int(key) <= min(9, len(sugg)):
                s = sugg[int(key) - 1]
                if not (s.action and s.address):
                    note = f'[{key}] has no action wakegraph can take: {s.hint or "it is not running in herdr"}'
                    continue
                draw(f'Nudge {s.agent} ({s.address}) with:\n  "{s.message}"\nSend it? [y/N]')
                sys.stdout.flush()
                if sys.stdin.read(1).lower() == 'y':
                    ok, detail = run_action(s)
                    note = f"{'Sent' if ok else 'Failed'}: nudge {s.agent} ({detail})"
                else:
                    note = 'Cancelled.'
    except KeyboardInterrupt:
        return 0
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def notify(title, body, runner=None):
    """A desktop notification: macOS Notification Centre, notify-send on Linux, else the terminal bell."""
    runner = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, timeout=10))
    if sys.platform == 'darwin':
        cmd = ['osascript', '-e', f'display notification {json.dumps(body)} with title {json.dumps(title)}']
    elif shutil.which('notify-send'):
        cmd = ['notify-send', title, body]
    else:
        sys.stdout.write('\a')
        return
    try:
        runner(cmd)
    except (OSError, subprocess.SubprocessError):
        pass


def announce(queue, stalls, now, store=None, notifier=notify):
    """Notify once per new item in the owner's queue and per new stall. Returns the (title, body) pairs sent.

    Seen items are remembered in the state folder, so restarting wakegraph doesn't repeat them. Only names and kinds
    go into a notification, never message text.
    """
    store = store or os.path.join(os.path.dirname(log_path()), 'notified.json')
    try:
        with open(store) as f:
            seen = json.load(f)
    except (OSError, ValueError):
        seen = {}
    sent = []
    for q in queue:
        if q['key'] in seen or q['seconds'] > NOTIFY_MAX_AGE:
            continue
        body = f"{q['kind']}, waiting {status._ago(q['seconds'])}" + (
            f"; holds up {', '.join(q['blocks'])}" if q['blocks'] else '')
        sent.append((q['key'], f"{q['agent']} needs you", body))
    for st in stalls:
        key = 'stall|' + '|'.join(sorted(r['agent'] for r in st.get('roots', [])))
        if key not in seen and st['quiet_seconds'] <= NOTIFY_MAX_AGE:
            sent.append((key, 'Team stalled', st['text'][:220]))
    for key, title, body in sent:
        notifier(f'wakegraph: {title}', body)
        seen[key] = now.isoformat()
    if sent:
        cutoff = (now - dt.timedelta(days=2)).isoformat()
        seen = {k: v for k, v in seen.items() if v >= cutoff}
        try:
            os.makedirs(os.path.dirname(store), exist_ok=True)
            with open(store, 'w') as f:
                json.dump(seen, f)
        except OSError:
            pass
    return [(t, b) for _, t, b in sent]


def watch(claude_dir, codex_dir, hours=12, use_herdr=True):
    """`wakegraph watch`: no screen, just a notification when something needs you or a team stalls."""
    print(f'wakegraph watch: checking every {WATCH_EVERY} s; notifications only. Ctrl-C to stop.', flush=True)
    try:
        while True:
            now = status.now_utc()
            agents = status.collect(claude_dir, codex_dir, now, hours,
                                    herdr=status.herdr_terminals() if use_herdr else None,
                                    running=status.running_cwds())
            for title, body in announce(status.owner_queue(agents, now), status.stalls(agents, now), now):
                print(f"{now.astimezone().strftime('%H:%M')}  {title}: {body}", flush=True)
            time.sleep(WATCH_EVERY)
    except KeyboardInterrupt:
        return 0
