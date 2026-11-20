"""Automatic fixes: one attempt per problem, and the problem measured when it is attempted."""
import datetime as dt
import os
import re
import threading

from . import live, quiet, status

CHECK_S = 600  # how long a fix has to work before it counts as not having worked
LOCK = threading.Lock()  # check attempted -> send -> record, so two ticks never both send


def state_path():
    return os.path.join(quiet.state_dir(), 'fixes.json')


def _state():
    s = quiet._read_json(state_path(), {})
    return s if isinstance(s, dict) else {}


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


def check_outcomes(views, rt, log=None, now=None):
    """Judge each automatic fix not yet judged, once, and log an `outcome` entry. Everything is read from the log, so a
    restart forgets nothing. A fix whose agent has exited gets none."""
    now = now or status.now_utc()
    out = []
    with LOCK:
        entries = live.recent_actions(log, 500)
        done = {e.get('of') for e in entries if e.get('action') == 'outcome'}
        for e in entries:
            until = status.parse_ts(e.get('check_until'))
            if e.get('by') != 'auto' or not e.get('problem') or e['id'] in done or not until:
                continue
            pane = next((p for p in (views.get(e.get('team')) or {}).get('panes', []) if p['role'] == e.get('agent')), None)
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
    return out


def refused_command(send):
    """The command a refusal-loop tell is about; None for any other tell."""
    m = re.match(r'assieme: `(.+)` has been refused \d+ times', send or '')
    return m[1] if m else None
