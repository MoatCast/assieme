"""Automatic fixes: one attempt per problem, and the problem measured when it is attempted."""
import datetime as dt
import os
import threading

from . import quiet, status

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
