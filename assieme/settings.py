"""The app's settings: a small JSON file in the state folder. A damaged file gives the defaults, which are Automatic:
only the exact word 'ask' keeps a fix on asking."""
import copy
import json
import os
import threading

from . import quiet

_write = threading.Lock()  # the brain serves each call on its own thread

_DEFAULTS = {'fixes': {'nudge': 'auto', 'compact': 'auto', 'tell': 'auto', 'quiet': 'auto'},
             'fixes_notice': False, 'fixes_version': 2, 'run_found': {},
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
    s['fixes'] = {k: 'ask' if fixes.get(k) == 'ask' else 'auto' for k in s['fixes']}
    s['notify'] = {k: notify.get(k) is not False for k in s['notify']}
    s['practices'] = {k: v for k, v in _dict(raw, 'practices').items() if isinstance(v, bool)}
    s['groups'] = {k: v for k, v in _dict(raw, 'groups').items() if isinstance(v, str) and v.strip()}
    s['show_commands'] = raw.get('show_commands') is True
    s['notify_paused_until'] = raw['notify_paused_until'] if isinstance(raw.get('notify_paused_until'), str) else None
    s['first_run_done'] = raw.get('first_run_done') is True
    s['fixes_notice'] = raw.get('fixes_notice') is True
    v = raw.get('fixes_version')
    s['fixes_version'] = v if isinstance(v, int) and not isinstance(v, bool) else 1  # a file without it is from before 2
    s['run_found'] = {k: True for k, v in _dict(raw, 'run_found').items() if v is True}
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
            'fixes_notice': not sub and isinstance(value, bool),
            'run_found': bool(sub) and isinstance(value, bool),
            'first_run_done': not sub and isinstance(value, bool),
            'notify_paused_until': not sub and (value is None or isinstance(value, str)),
            'groups': not sub and isinstance(value, dict)}
    if not good.get(top):
        raise ValueError(f'no setting {key!r} with that value')
    return top, sub


def migrate(file=None):
    """Once, for an install from before fixes ran themselves: switch every fix to automatic and owe the notice."""
    with _write:
        try:
            with open(file or path()) as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return False
        if not isinstance(raw, dict):
            return False
        s = _clean(raw)
        if s['fixes_version'] >= 2:
            return False
        s.update(fixes={k: 'auto' for k in s['fixes']}, fixes_notice=True, fixes_version=2)
        quiet._write_json(file or path(), s)
    return True


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
