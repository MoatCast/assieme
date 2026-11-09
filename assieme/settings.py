"""The app's settings: a small JSON file in the state folder. A damaged file gives defaults, so it can never turn a
fix to automatic: only the exact word 'auto' does."""
import copy
import json
import os
import threading

from . import quiet

_write = threading.Lock()  # the brain serves each call on its own thread

_DEFAULTS = {'fixes': {'nudge': 'ask', 'compact': 'ask', 'tell': 'ask'},
             'practices': {},
             'notify': {'needs_you': True, 'stalls': True, 'finished': True},
             'notify_paused_until': None,
             'show_commands': False,
             'groups': {},
             'first_run_done': False}


def defaults():
    return copy.deepcopy(_DEFAULTS)


def path():
    return os.path.join(quiet.state_dir(), 'settings.json')


def _dict(raw, key):
    v = raw.get(key)
    return v if isinstance(v, dict) else {}


def _clean(raw):
    s = defaults()
    fixes, notify = _dict(raw, 'fixes'), _dict(raw, 'notify')
    s['fixes'] = {k: 'auto' if fixes.get(k) == 'auto' else 'ask' for k in s['fixes']}
    s['notify'] = {k: notify.get(k) is not False for k in s['notify']}
    s['practices'] = {k: v for k, v in _dict(raw, 'practices').items() if isinstance(v, bool)}
    s['groups'] = {k: v for k, v in _dict(raw, 'groups').items() if isinstance(v, str) and v.strip()}
    s['show_commands'] = raw.get('show_commands') is True
    s['notify_paused_until'] = raw['notify_paused_until'] if isinstance(raw.get('notify_paused_until'), str) else None
    s['first_run_done'] = raw.get('first_run_done') is True
    return s


def load(file=None):
    try:
        with open(file or path()) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return defaults()
    return _clean(raw) if isinstance(raw, dict) else defaults()


def _check(key, value):
    top, _, sub = key.partition('.')
    good = {'fixes': sub in _DEFAULTS['fixes'] and value in ('ask', 'auto'),
            'notify': sub in _DEFAULTS['notify'] and isinstance(value, bool),
            'practices': bool(sub) and isinstance(value, bool),
            'show_commands': not sub and isinstance(value, bool),
            'first_run_done': not sub and isinstance(value, bool),
            'notify_paused_until': not sub and (value is None or isinstance(value, str)),
            'groups': not sub and isinstance(value, dict)}
    if not good.get(top):
        raise ValueError(f'no setting {key!r} with that value')
    return top, sub


def set_value(key, value, file=None):
    top, sub = _check(key, value)
    with _write:
        s = load(file)
        if sub:
            s[top][sub] = value
        else:
            s[top] = value
        quiet._write_json(file or path(), s)
    return load(file)
