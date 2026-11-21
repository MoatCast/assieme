"""Records `assieme/settings.py` into tests/data/settings.json for `src/settings.rs`. Run from the repo root:
    .venv/bin/python runtime/crates/assieme-brain/record/settings_cases.py

Each case is a settings file's text (None: no file), one call, its answer (or the error's words) and the file after.
The file lives in a scratch folder while recording; only its text is kept."""
import json
import math
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
sys.path.insert(0, ROOT)
from assieme import settings  # noqa: E402

V1 = json.dumps({'fixes': {'nudge': 'ask', 'compact': 'auto'}, 'notify': {'stalls': False}})
FULL = json.dumps({'fixes': {'nudge': 'ask', 'compact': 'weird', 'tell': 'ask', 'extra': 'ask'}, 'fixes_version': 2,
                   'notify': {'needs_you': False, 'stalls': 0, 'finished': None, 'other': False},
                   'practices': {'tests': True, 'lint': 'yes', 'fmt': False}, 'groups': {'a': 'One', 'b': '  ', 'c': 3, 'd': ' x '},
                   'show_commands': 1, 'notify_paused_until': '2027-01-15T08:00:00Z', 'first_run_done': True,
                   'fixes_notice': 'true', 'run_found': {'t1': True, 't2': 1, 'a.b': True, 't3': False},
                   'thresholds': {'quiet_minutes': 30, 'memory_full': 0.96, 'other': 5}, 'unknown': 1})
FILES = [None, '', 'not json', '[1, 2]', '{}', '"x"', V1, FULL, '{"fixes_version": 2.0}', '{"fixes_version": true}',
         '{"fixes_version": 3, "thresholds": {"quiet_minutes": 5, "memory_full": 0.5}}',
         '{"thresholds": {"quiet_minutes": 120.0, "memory_full": 1}}', '{"thresholds": {"quiet_minutes": true}}',
         '{"thresholds": {"quiet_minutes": NaN}}', '{"notify_paused_until": 5}', '{"groups": []}', '{"fixes_version": 1}']

SETS = [('fixes.nudge', 'ask'), ('fixes.nudge', 'auto'), ('fixes.nudge', 'never'), ('fixes.other', 'ask'), ('fixes', 'ask'),
        ('notify.stalls', False), ('notify.stalls', 0), ('notify.other', True), ('practices.tests', True),
        ('practices.tests', 'yes'), ('practices', True), ('show_commands', True), ('show_commands', 1),
        ('show_commands.x', True), ('fixes_notice', False), ('run_found.team', True), ('run_found.a.b', True),
        ('run_found.team', False), ('run_found', True), ('first_run_done', True), ('notify_paused_until', None),
        ('notify_paused_until', '2027-01-15T08:00:00Z'), ('notify_paused_until', 5), ('groups', {'a': 'x'}),
        ('groups', ['a']), ('groups.a', 'x'), ('thresholds.quiet_minutes', 5), ('thresholds.quiet_minutes', 120),
        ('thresholds.quiet_minutes', 4), ('thresholds.quiet_minutes', 121), ('thresholds.quiet_minutes', 4.99),
        ('thresholds.quiet_minutes', 30.5), ('thresholds.quiet_minutes', True), ('thresholds.quiet_minutes', '30'),
        ('thresholds.memory_full', 0.5), ('thresholds.memory_full', 0.95), ('thresholds.memory_full', 0.49),
        ('thresholds.memory_full', 0.951), ('thresholds.memory_full', 1), ('thresholds.other', 1), ('thresholds', {}),
        ('unknown', 1), ("it's", True), ('fixes.it\'s"', 'ask'), ('', True), ('.', True), ('practices.', True),
        ('fixes_version', 1), ('thresholds.quiet_minutes', float('nan'))]


def canonical(v):
    if isinstance(v, float) and math.isnan(v):
        return {'$py': 'nan'}
    if isinstance(v, dict):
        return {k: canonical(x) for k, x in v.items()}
    return v


def call(text, fn):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'settings.json')
        if text is not None:
            with open(path, 'w') as f:
                f.write(text)
        try:
            out = {'ok': canonical(fn(path))}
        except ValueError as e:
            out = {'error': str(e)}
        after = open(path).read() if os.path.exists(path) else None
        out['after'] = canonical(json.loads(after)) if after is not None and after != text else None
        return out


def main():
    cases = []
    for text in FILES:
        cases.append({'file': text, 'call': ['load'], **call(text, settings.load)})
        cases.append({'file': text, 'call': ['migrate'], **call(text, settings.migrate)})
    for text in (None, FULL, V1):
        for key, value in SETS:
            cases.append({'file': text, 'call': ['set_value', key, canonical(value)],
                          **call(text, lambda p, k=key, v=value: settings.set_value(k, v, p))})
    with open(os.path.join(ROOT, 'runtime/crates/assieme-brain/tests/data/settings.json'), 'w') as f:
        json.dump(cases, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print(len(cases), 'cases')


if __name__ == '__main__':
    main()
