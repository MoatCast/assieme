"""Automatic fixes: one attempt per problem, and the problem measured when it is attempted."""
import datetime as dt
import os
import re
import threading

from . import budget, live, quiet, status

CHECK_S = 600  # how long a fix has to work before it counts as not having worked
LOCK = threading.Lock()  # check attempted -> send -> record, so two ticks never both send
RESUME = ('assieme: the person has let you carry on. Assieme paused you because the same problem continued after its '
          'suggestion; try a different approach.')
PAUSED = f'No progress for {CHECK_S // 60} min after Assieme asked it to try another way.'
GONE = 'This has already carried on.'


def state_path():
    return os.path.join(quiet.state_dir(), 'fixes.json')


def _state():
    s = quiet._read_json(state_path(), {})
    return s if isinstance(s, dict) else {}


def _paused(state):
    """team/role -> {'at', 'of', 'address', 'stopped', 'notes', 'measured'}. Damaged reads as nobody paused."""
    if not isinstance(state.get('paused'), dict):
        state['paused'] = {}
    return state['paused']


def _pane(views, team, role):
    return next((p for p in (views.get(team) or {}).get('panes', []) if p['role'] == role), None)


def _pause(paused, team, role, address, of, measured, rt, now, log, press=True):
    """Under LOCK. Presses the agent's stop key once; it is never pressed again for this pause."""
    k = f'{team}/{role}'
    if k in paused:
        return paused[k]
    stopped = False
    if press:
        try:
            rt.interrupt(address)
            stopped = True
        except Exception:  # not retried: Resume, or the person typing in its terminal, ends the pause
            pass
    paused[k] = {'at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'of': of, 'address': address, 'stopped': stopped, 'notes': [],
                 'measured': measured}
    live.record('fix_pause', role, PAUSED, team=team, by='auto', address=address, log=log, now=now,
                title=f'Paused {role}: still stuck after a fix')
    return paused[k]


def pause(team, role, address, of, rt, now, log=None, measured=None):
    with LOCK:
        state = _state()
        x = _pause(_paused(state), team, role, address, of, measured, rt, now, log)
        quiet._write_json(state_path(), state)
        return x


def paused_teams():
    return {k.split('/', 1)[0] for k in _paused(_state())}


def is_paused(team, role):
    return isinstance(_paused(_state()).get(f'{team}/{role}'), dict)


def hold(team, role, sender, text):
    """Keep a message for an agent paused after a fix. False when it isn't paused, so the caller sends it as usual."""
    with LOCK:
        state = _state()
        x = _paused(state).get(f'{team}/{role}')
        if not isinstance(x, dict):
            return False
        x.setdefault('notes', []).append({'from': sender, 'text': text})
        quiet._write_json(state_path(), state)
        return True


def resume(team, role, rt, log=None, now=None, by='you'):
    """Let it carry on: RESUME, then the messages held for it."""
    now = now or status.now_utc()
    with LOCK:
        state = _state()
        x = _paused(state).pop(f'{team}/{role}', None)
        if not isinstance(x, dict):
            return {'ok': False, 'detail': GONE}
        quiet._write_json(state_path(), state)
    texts = [RESUME] + _held(x)
    try:
        for t in texts:
            rt.send(x['address'], t)
        ok, detail = True, ''
    except Exception as e:
        ok, detail = False, str(e)[:120]
    live.record('fix_resume', role, 'You let it carry on.', ok, detail, team=team, by=by, address=x['address'], log=log,
                now=now, title=f'Let {role} carry on')
    return {'ok': ok, 'detail': detail}


def _held(x):
    notes = x.get('notes') or []
    return ['Messages that arrived while you were paused:\n' +
            '\n'.join(f"- from {n['from']}: {n['text']}" for n in notes)] if notes else []


def _measured_line(m):
    if not isinstance(m, dict):
        return ''
    what = m.get('what') or f"no progress for {m.get('minutes')} min"
    return f'Before the fix: {what}.' + (f" {budget.words(m['tokens'])} tokens used on it." if m.get('tokens') else '')


def pane_fix(team, role, now):
    """The Needs-you card of an agent paused after a fix, or None."""
    x = _paused(_state()).get(f'{team}/{role}')
    if not isinstance(x, dict):
        return None
    at = status.parse_ts(x.get('at'))
    return {'id': f'fixpause:{team}/{role}', 'pause': {'at': at.astimezone().strftime('%H:%M') if at else '', 'of': x.get('of')},
            'text': PAUSED, 'detail': _measured_line(x.get('measured')), 'question': None,
            'buttons': [{'id': 'hint', 'label': 'Give it a hint', 'kind': 'reply'},
                        {'id': 'resume', 'label': 'Resume as is', 'kind': 'action'}]}


def problem_key(team, role, action, started):
    """Minute precision, so every poll of one problem agrees."""
    return f'{team}/{role}/{action}/{started:%Y%m%dT%H%M}'


def attempted(key, entries):
    return any(e.get('problem') == key for e in entries)


def measure(pane, tokens_now, tokens_at_start):
    spent = tokens_now - tokens_at_start if tokens_now is not None and tokens_at_start is not None else None
    return {'minutes': int(pane['since_s'] // 60), 'what': pane.get('detail') or '', 'tokens': spent}


def start_tokens(team, role, started, tokens_now, now):
    """The token count when this problem began, remembered the first tick it was seen. A problem that began after
    the remembered one was seen is a new one."""
    with LOCK:
        state = _state()
        starts = state.setdefault('starts', {})
        k = f'{team}/{role}'
        seen = status.parse_ts((starts.get(k) or {}).get('at'))
        if seen is None or started > seen:
            starts[k] = {'at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'tokens': tokens_now}
            quiet._write_json(state_path(), state)
        return starts[k].get('tokens')


def check_until(now):
    return (now + dt.timedelta(seconds=CHECK_S)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _carried_on(pane, x):
    """The person made a paused agent go on: a turn began after the pause. The hook alone can't say so, as Claude
    Code's Stop hook doesn't run on Esc and may still read working. An agent never pressed (it was asking) has no stale
    hook: working is enough, as answering a permission prompt continues its turn rather than starting one."""
    turn, at = status.parse_ts(pane.get('turn_at')), status.parse_ts(x.get('at'))
    return bool(turn and at and turn > at) or (not x.get('stopped') and pane['state'] == 'working')


def check_outcomes(views, rt, log=None, now=None):
    """Judge each automatic fix not yet judged, once, and log an `outcome` entry. Everything is read from the log, so a
    restart forgets nothing. A fix whose agent has exited gets none, and so does one whose send failed.
    A nudge that ends still stuck pauses its agent; a pause ends when the agent exits or the person makes it work."""
    now = now or status.now_utc()
    out, carried = [], []
    with LOCK:
        state = _state()
        before = repr(state)
        paused = _paused(state)
        entries = live.recent_actions(log, 500)
        done = {e.get('of') for e in entries if e.get('action') == 'outcome'}
        for e in entries:
            until = status.parse_ts(e.get('check_until'))
            if e.get('by') != 'auto' or not e.get('problem') or e['id'] in done or not until or e.get('ok') is False:
                continue
            pane = _pane(views, e.get('team'), e.get('agent'))
            if not pane or pane.get('state') == 'exited':
                continue
            action = e['action']
            if action == 'nudge':
                fixed = pane['state'] == 'working'
            elif action == 'compact':
                fixed = not pane.get('memory_full')
            else:  # tell: still stuck once the command it was about is refused after the fix, fixed if not by the time is up
                at = status.parse_ts(e.get('at'))
                if not e.get('refused') or not at:
                    continue  # an older entry, or a warning tell: nothing to judge it by
                again = any(label == e['refused'] and status.parse_ts(ts) > at for ts, label in pane.get('refused', []))
                if not again and now < until:
                    continue
                fixed = not again
            if not fixed and now < until:
                continue
            out.append(live.record('outcome', e['agent'], '', team=e.get('team'), by='auto', log=log, now=now,
                                   of=e['id'], result='fixed' if fixed else 'still_stuck'))
            if action == 'nudge' and not fixed:  # a real question on screen: Esc would refuse it, so it is not pressed
                _pause(paused, e['team'], e['agent'], pane['address'], e['id'], e.get('measured'), rt, now, log,
                       press=pane['state'] != 'asking')
        for k in list(paused):
            team, role = k.split('/', 1)
            if team not in views:
                continue  # not looked at this tick
            pane, x = _pane(views, team, role), paused[k]
            if not isinstance(x, dict) or not pane or pane.get('state') == 'exited' or pane['address'] != x.get('address'):
                del paused[k]  # it exited, or a new agent has its name
                n = len(x.get('notes') or []) if isinstance(x, dict) else 0
                if n:
                    live.record('fix_dropped', role, 'It stopped while paused.', team=team, by='auto', log=log, now=now,
                                title=f"Did not deliver {n} message{'s' * (n != 1)} to {role}")
            elif _carried_on(pane, x):
                del paused[k]
                carried.append((team, role, x))
        if repr(state) != before:
            quiet._write_json(state_path(), state)
    for team, role, x in carried:  # sent after LOCK, as resume does
        try:
            for t in _held(x):
                rt.send(x['address'], t)
            ok, detail = True, ''
        except Exception as e:
            ok, detail = False, str(e)[:120]
        live.record('fix_resume', role, 'It started working again.', ok, detail, team=team, by='auto', address=x['address'],
                    log=log, now=now, title=f'Let {role} carry on')
    return out


def refused_command(send):
    """The command a refusal-loop tell is about; None for any other tell."""
    m = re.match(r'assieme: `(.+)` has been refused \d+ times', send or '')
    return m[1] if m else None
