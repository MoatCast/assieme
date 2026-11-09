"""The Terminals view: one pane per Assieme agent, with its state, the needs-you strip, and the buttons that act.

Panes are built from `hc`'s terminal rows (hook status, folder, program), the log agents `status.collect` finds,
matched by worktree folder, the same suggestions as the command-line live view (`live.suggest`), the menu on the
agent's screen, and suggested answers to a plain-word question (`answers.Answers`).
"""
import hashlib
import os

from . import adapters, live, prompts, reports, status, team

URGENT = ('asking', 'stuck', 'waiting')  # what the needs-you strip lists, in this order
ACTIONS = ('nudge', 'compact', 'tell')    # `quiet` stays in the command line
CHANGED = 'This question changed. Take a look.'
MEMORY_FULL = 0.75  # Settings promises automatic Free up memory only past this
ANSWERED = 'You answered its question.'  # never the question: the action log keeps no words of the agents'


def _real(path):
    return os.path.realpath(path) if path else None


def match(rows, agents):
    """Terminal address -> the most recent log agent working in that terminal's folder."""
    out = {}
    for r in rows:
        here = [a for a in agents if a.cwd and _real(a.cwd) == _real(r.get('cwd'))]
        if here:
            out[r['address']] = max(here, key=lambda a: a.last.timestamp() if a.last else 0)
    return out


def _id(*parts):
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:10]


def _state(r, a, waiting_roots):
    hook = (r.get('status') or {}).get('state')
    if r.get('state') == 'finished':
        return 'exited'
    if hook == 'blocked' or (a and a.state == 'waiting on you'):
        return 'asking'
    if a and a.state == 'stuck':
        return 'stuck'
    if a and (a.state == 'idle, work waiting' or a.name in waiting_roots):
        return 'waiting'
    if hook == 'working' or (a and a.state == 'working'):
        return 'working'
    return 'idle'


def _since(r, a, state, now):
    when = None
    if a:
        when = (a.ask_since if state == 'asking' else None) or a.last
    if when:
        return max(0, int((now - when).total_seconds()))
    if r.get('status_ms'):
        return max(0, int(now.timestamp() - r['status_ms'] / 1000))
    return None


def _cap(text):
    return text[:1].upper() + text[1:] if text else text


def _button(role, kind, label, send, about=''):
    """`about` (the text above a menu) is part of the id: two menus with the same choices are different questions."""
    return {'id': _id(role, kind, label, send, about), 'label': label, 'kind': kind, 'send': send}


RUN_GAP_MS = 120_000  # agents of one start are created within seconds of each other


def _current_run(rows):
    """The rows of the team's latest start: `hc` keeps finished terminals of earlier runs under the same name."""
    live = [r.get('created_ms') or 0 for r in rows if r.get('state') == 'live']
    since = (min(live) if live else max((r.get('created_ms') or 0 for r in rows), default=0)) - RUN_GAP_MS
    return [r for r in rows if r.get('state') == 'live' or (r.get('created_ms') or 0) >= since]


def _latest(rows):
    """One row per role: the live terminal, else the newest (a restarted team keeps its old, finished rows)."""
    best = {}
    for r in rows:
        role = (r.get('labels') or {}).get(team.ROLE) or r['address']
        key = (r.get('state') == 'live', r.get('created_ms') or 0)
        if role not in best or key > best[role][0]:
            best[role] = (key, r)
    return [r for _, r in best.values()]


def _memory_full(a):
    limit = status.memory_limit(a) if a else None
    return bool(limit and a.context > limit * MEMORY_FULL)


def _fix(role, buttons, text):
    return {'id': _id(role, text, tuple(b['id'] for b in buttons)), 'text': text, 'buttons': buttons}


def view(team_name, rows, agents, now, read, answers, show_commands=False):
    rows = sorted(_latest(_current_run(rows)), key=lambda r: r.get('created_ms') or 0)
    matched = match(rows, agents)
    addr = {a.name: t for t, a in matched.items()}
    stalls = status.stalls(agents, now)
    queue = status.owner_queue(agents, now)
    roots = {root['agent'] for st in stalls for root in st.get('roots', []) if root.get('waiting')}
    holds = {q['agent']: q['blocks'] for q in queue}
    suggestions = [s for s in live.suggest(agents, stalls, addr, queue, now=now) if s.action in ACTIONS]
    out, strip = [], []
    for r in rows:
        role = (r.get('labels') or {}).get(team.ROLE) or r['address']
        a = matched.get(r['address'])
        state = _state(r, a, roots)
        # The agent's own word beats guessing from logs; a real prompt on screen beats both.
        report = None
        if state == 'exited':
            reports.clear(team_name, role)
        elif state != 'asking':
            report = reports.current(team_name, role, r)
        if report:
            state = 'asking' if report['state'] == 'need_decision' else 'stuck'
        argv0 = os.path.basename((r.get('argv') or [''])[0])
        p = {'role': role, 'agent': 'codex' if argv0 == 'codex' else 'claude', 'address': r['address'],
             'state': state, 'since_s': _since(r, a, state, now), 'detail': '', 'live': state != 'exited', 'fix': None,
             'command': None, 'memory_full': _memory_full(a)}
        if report:
            p['since_s'] = max(0, int(now.timestamp() - report['ms'] / 1000))
        if state == 'exited':
            lines = [x.strip() for x in (read(r['address']) or '').splitlines() if x.strip()]
            p['detail'] = lines[-1] if lines else ''
        elif state == 'asking':
            screen = read(r['address']) or ''
            menu, q = [] if report else prompts.menu(screen), report['note'] if report else None
            about = prompts.menu_context(screen) if menu else ''
            buttons = [_button(role, 'menu', label, str(n), about) for n, label in menu]
            if not menu:
                q = q or prompts.question(screen)
                got = answers.get(f'{team_name}/{role}/{_id(q)}', q) if q else None
                buttons = [_button(role, 'answer', x, x) for x in got or []]
            buttons.append(_button(role, 'reply', 'Reply', None))
            blocks = holds.get(a.name, []) if a else []
            p['detail'] = f'Holding up {len(blocks)} agent{"s" * (len(blocks) != 1)}' if blocks else 'Waiting for you'
            text = q or (about.splitlines()[-1] if about else '') or (a.ask_kind if a and a.ask_kind else 'Waiting for you')
            p['fix'] = _fix(role, buttons, text)
            strip.append({'role': role, 'state': state, 'text': f'{role} is asking: {text}'})
        else:
            s = next((s for s in suggestions if a and s.agent == a.name), None)
            said = s.text.replace(a.name, role) if s else ''  # the log's name for it is a long folder path
            if state == 'stuck' and a and a.cmds:
                p['command'] = a.cmds[-1] if show_commands else adapters.program(a.cmds[-1])
            if report:
                p['detail'] = report['note']
                strip.append({'role': role, 'state': state, 'text': f"{role} is stuck: {report['note']}"})
                if not s:
                    p['fix'] = _fix(role, [_button(role, 'reply', 'Reply', None)], report['note'])
            elif state in ('stuck', 'waiting'):
                p['detail'] = _cap(a.detail if a and a.detail else said)
                verb = 'seems stuck' if state == 'stuck' else 'has work waiting'
                strip.append({'role': role, 'state': state,
                              'text': f'{role} {verb}' + (f': {a.detail}' if a and a.detail else '')})
            if s:
                label = s.label or {'nudge': 'Ask it to try another way' if state == 'stuck' else 'Nudge',
                         'compact': 'Compact', 'tell': 'Tell it to stop retrying'}[s.action]
                p['detail'] = p['detail'] or _cap(said)
                p['fix'] = _fix(role, [dict(_button(role, 'action', label, s.message), action=s.action)], said)
        out.append(p)
    strip.sort(key=lambda x: URGENT.index(x['state']))
    return {'team': team_name, 'panes': out, 'strip': strip}


def act(team_name, role, fix, button, view_now, rt, log=None, now=None, by='you'):
    """Carry out one button, only if it is still on the pane right now (the person may have answered in the
    terminal meanwhile). Menus press the digit; answers and actions are sent as a message. Every press is logged."""
    p = next((x for x in view_now()['panes'] if x['role'] == role), None)
    f = p and p['fix']
    b = f and f['id'] == fix and next((x for x in f['buttons'] if x['id'] == button), None)
    if not b:
        return {'ok': False, 'detail': CHANGED}
    if b['kind'] == 'reply':
        return {'ok': True, 'detail': ''}  # the app opens the pane for typing
    action = b.get('action') or b['kind']  # nudge, compact or tell; menu; answer
    try:
        if b['kind'] == 'menu':
            rt.keys(p['address'], [b['send']])
            ok, detail = True, f"pressed {b['send']}"
        else:
            rt.send(p['address'], b['send'])
            ok, detail = True, 'sent'
    except Exception as e:  # a failed press is a message on the pane, never a dead brain call
        ok, detail = False, str(e)[:120]
    live.record(action, role, f['text'] if b.get('action') else ANSWERED, ok, detail, team=team_name, by=by,
                address=p['address'], log=log, now=now)
    return {'ok': ok, 'detail': detail}
