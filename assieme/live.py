"""Suggestions, the action log and desktop notifications. Every action is written to the action log."""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass

from . import quiet, status

NOTIFY_MAX_AGE = 2 * 3600 # older items are shown, but never pop up a notification
COMPACT_AT = 150_000      # A2: suggest compacting an idle agent whose context is at least this big
REFUSAL_WINDOW = 30 * 60  # A5: two refusals of the same command within this window trigger the breaker
ACTED_HIDE = 15 * 60      # a fix you just carried out stays hidden this long


@dataclass
class Suggestion:
    text: str             # the alert, in plain words
    agent: str
    address: str = None   # herdr terminal to act on
    action: str = None    # 'nudge', 'compact' or 'tell' when assieme can carry out the fix
    message: str = ''     # what the nudge says
    hint: str = ''        # the fix, when it is the person's to make
    yours: bool = False   # a decision only the owner can make
    pair: tuple = None    # quiet: (from_cwd, to_cwd, label)
    label: str = ''       # the button's words on the agent's card, when `tell` means more than stop retrying


def log_path():
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'assieme', 'actions.jsonl')


def suggest(agents, stalls, addr, queue=(), done=(), now=None, send_less=()):
    """Alerts -> suggestions, most urgent first: stall blockers, what waits on you, stuck agents, lost messages,
    refusal loops, then oversized contexts. done: (agent, action) pairs carried out recently, kept hidden."""
    by = {a.name: a for a in agents}
    out, seen = [], set(done)

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
                               f"assieme: {names} are waiting on you. Check your messages and reply to them."))
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
                           'assieme: a message for you may not have started a turn. Check your latest messages and act on them.'))
    now = now or status.now_utc()
    for a in agents:  # A5: the second refusal of the same command is the moment to stop the loop
        if a.state == 'exited':
            continue
        recent = {}
        for ts, label, why, advice in a.refused:
            if (now - ts).total_seconds() <= REFUSAL_WINDOW:
                n, _ = recent.get((label, why), (0, ''))
                recent[(label, why)] = (n + 1, advice)
        for (label, why), (n, advice) in recent.items():
            if n >= 2:
                add(Suggestion(f'{a.name}: `{label}` refused {n} times by {why}' + (f'. Rule fix: {advice}' if advice else ''),
                               a.name, addr.get(a.name), 'tell',
                               f'assieme: `{label}` has been refused {n} times by {why}. Do not retry it. '
                               'Do the task another way, or ask the owner if you really need it.'))
                break
    warnings = status.warnings(agents, now)
    for w in warnings:  # F6, F7: told once per agent, after any refusal loop; F10 is the owner's to fix
        if w['message']:
            add(Suggestion(w['what'], w['agent'], addr.get(w['agent']), 'tell', w['message'], label=w['label']))
        elif w['kind'] == 'box':
            add(Suggestion(w['what'], w['agent'], hint=w['fix'], yours=True))
    growing = {w['agent']: w['per_hour'] for w in warnings if w['kind'] == 'growth'}
    for a in agents:  # A2: an idle team agent with a big context re-reads all of it on every wake
        if a.state == 'idle' and a.context >= COMPACT_AT and a.peers:  # a one-off session won't be woken: leave it
            pace = f' and is growing {growing[a.name] // 1000}K an hour' if a.name in growing else ''
            add(Suggestion(f'{a.name} holds {a.context // 1000}K tokens of context{pace}; every wake re-reads it',
                           a.name, addr.get(a.name), 'compact', '/compact'))
    for x in send_less:  # A3: the costliest empty pairs can be made quiet with one key
        f, t = _find(agents, x['from']), _find(agents, x['to'])
        if f and t and f.cwd and t.cwd and not quiet.is_quiet(f.cwd, t.cwd):
            add(Suggestion(f"{x['from']} -> {x['to']}: {x['empty']} of {x['wakes']} wakes did no work "
                           f"({x['empty_tokens'] / 1e6:.0f}M tokens in 24 h)", f.name, action='quiet',
                           pair=(f.cwd, t.cwd, f"{x['from']} -> {x['to']}")))
    return out


def _find(agents, name):
    """Report and status may shorten names differently; fall back to the last part of the name."""
    exact = [a for a in agents if a.name == name]
    if exact:
        return exact[0]
    tail = name.split('-')[-1].split(':')[-1]
    near = [a for a in agents if a.name.split('-')[-1].split(':')[-1] == tail]
    return near[0] if len(near) == 1 else None


def _nudge_text(a):
    if a.detail.startswith('same command'):
        return ('assieme: you have run the same command several times in a row. Stop retrying it. '
                'Say what you are trying to do, then try a different approach.')
    if a.detail.startswith('subagent'):
        return 'assieme: one of your subagents has gone silent. Check it or stop it, then carry on.'
    if a.detail.startswith('no reply'):
        return 'assieme: you have not answered for a while. Reply with your status: done, working on what, or blocked on what.'
    return ('assieme: no progress for a while. If a run is slow, narrow it. '
            'If you are stuck, say what is blocking you.')


def record(action, agent, reason, ok=True, detail='', team=None, by='you', address=None, log=None, now=None, **extra):
    """One Activity row. `extra` is what Undo needs (see activity.UNDO). Returns the entry."""
    if by == 'you':
        from . import serve  # serve imports more of the brain than this module may at import time
        by = serve.caller()  # 'assistant' when the person asked through their assistant (step 18a)
    entry = {'id': uuid.uuid4().hex[:12], 'at': (now or status.now_utc()).strftime('%Y-%m-%dT%H:%M:%SZ'), 'action': action,
             'agent': agent, 'address': address, 'team': team, 'by': by, 'reason': reason, 'ok': ok, 'detail': detail,
             **extra}
    _log(log or log_path(), entry)
    return entry


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
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


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


def announce(queue, stalls, now, store=None, notifier=notify, switches=None):
    """Notify once per new item in the owner's queue and per new stall. Returns the (title, body) pairs sent.

    Seen items are remembered in the state folder, so restarting assieme doesn't repeat them. Only names and kinds
    go into a notification, never message text.
    """
    store = store or os.path.join(os.path.dirname(log_path()), 'notified.json')
    try:
        with open(store) as f:
            seen = json.load(f)
    except (OSError, ValueError):
        seen = {}
    sent = []
    switches = switches or {}
    for q in queue if switches.get('needs_you', True) else ():
        if q['key'] in seen or q['seconds'] > NOTIFY_MAX_AGE:
            continue
        body = f"{q['kind']}, waiting {status._ago(q['seconds'])}" + (
            f"; holds up {', '.join(q['blocks'])}" if q['blocks'] else '')
        sent.append((q['key'], f"{q['agent']} needs you", body))
    for st in stalls if switches.get('stalls', True) else ():
        key = 'stall|' + '|'.join(sorted(r['agent'] for r in st.get('roots', [])))
        if key not in seen and st['quiet_seconds'] <= NOTIFY_MAX_AGE:
            sent.append((key, 'Team stalled', st['text'][:220]))
    for key, title, body in sent:
        notifier(f'assieme: {title}', body)
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


def record_delivery(notes, why, now=None):
    senders = {}
    for n in notes:
        senders[n['from_cwd']] = senders.get(n['from_cwd'], 0) + 1
    last = notes[-1]
    record('deliver', last.get('role') or last['to'], f'{len(notes)} note(s) held for its next turn.', detail=why, by='auto',
           team=last.get('team'), address=last['to'], now=now,
           to_cwd=last['to_cwd'], **{'from': senders})


def record_given_up(notes, now=None):
    last = notes[-1]
    record('dropped', last.get('role') or last['to'], f'{len(notes)} note(s) waited a day, but it has stopped.', ok=False,
           detail='It has stopped.', by='auto', team=last.get('team'), address=last['to'], now=now)


def acted(now, path=None):
    """(agent, action) pairs carried out successfully in the last ACTED_HIDE seconds, so their fix stays hidden."""
    out = set()
    for x in recent_actions(path, n=50):
        at = status.parse_ts(x.get('at'))
        if x.get('ok') and at and (now - at).total_seconds() <= ACTED_HIDE:
            out.add((x.get('agent'), x.get('action')))
    return out
