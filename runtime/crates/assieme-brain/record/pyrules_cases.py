"""Records Python's reading rules into tests/data/pyrules.json for `src/pyrules.rs`. Run from the repo root with the
repo's .venv Python: .venv/bin/python runtime/crates/assieme-brain/record/pyrules_cases.py

The glob cases read the fixture tree tests/data/glob_tree, which this script writes (fixed relative paths)."""
import datetime as dt
import glob
import json
import math
import os
import sys

CRATE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(CRATE, 'tests/data')

ISO = [
    # the spike's odd timestamps (rust-brain-fixtures/limits_fixtures.py)
    '2027-01-15', '2027-01-15 08:00:00', '20270115T080000Z', '2027-01-15T08:00Z', '2027-01-15T08Z',
    '2027-01-15T08:00:00,5Z', '2027-W03-5', '2027-01-15T08:00:00.123456789Z', '2027-01-15T08:00:00.1234567Z',
    '2027-01-15T08:00:00+0200', '2027-01-15T08:00:00+02', '2027-01-15T24:00:00Z', '2027-01-15T08:00:00.000-00:00',
    '2027-01-15T08:00:00.000z', '+2027-01-15T08:00:00Z', '2027-1-15T08:00:00Z', '  2027-01-15T08:00:00Z',
    '2027-02-29T08:00:00Z', '2027-01-15T08:00:60Z', '2027-01-15T08:00:00.Z', '２０２７-01-15T08:00:00Z',
    # each rule's own edges
    '2027-01-15T08:00:00+02:00', '2027-01-15T08:00:00-05:30', '2027-01-15T08:00:00+05:30:15',
    '2027-01-15T08:00:00+05:30:15.5', '2027-01-15T08:00:00+23:59', '2027-01-15T08:00:00+24:00',
    '2027-01-15T08:00:00+00:00', '2027-01-15T08:00:00-00:00', '2027-01-15T08:00:00Z', '2027-01-15T08:00:00',
    '2027-01-15T08:00:00.5', '2027-01-15T08:00:00.000001Z', '2027-01-15T08:00:00.1Z', '2027-01-15T08:00:00.12345Z',
    '2027-01-15T080000', '2027-01-15T0800', '2027-01-15T08', '2027-01-15T8:00', '2027-01-15T08:0',
    '2027-01-15T08:00:00ZZ', '2027-01-15T08:00:00Z+01', '2027-01-15T08:00:00+1', '2027-01-15T08:00:00+012',
    '2027-12-31T24:00:00', '2027-01-15T24:00:01', '2027-01-15T24:00:00.000001', '2027-02-30T24:00:00',
    '2027-W01', '2027W01', '2027W015', '2027-W01-1', '2027W01-1', '2027-W011', '2026-W53-1', '2027-W53-1',
    '2027-W00-1', '2027-W01-8', '2027-W01-0', '2027W0115T08', '2027-W03-5T08:00:00Z', '2027-W03T08',
    '20270115', '2027-0115', '202701-15', '2027-01', '2027', '', 'x' * 7, '0000-01-01T00:00:00', '0001-01-01T00:00:00Z',
    # jiff's Timestamp ends at 9999-12-30T22:00Z (it must fit every zone): later times are left out on purpose.
    '9999-12-30T21:59:59Z', '9999-12-31T24:00:00', '2024-02-29T12:00:00', '2027-01-15x08:00:00', '2027-01-15T08:00:00 ',
    '2027-01-15T08:00:00\n', '2027-01-15T08:00:00+02:00Z', '2027-01-15T08:00:00.123+02:00', '2027-01-15T08:00:00,123456Z',
    '2027-01-15T08:00:00.1234567890123Z', '2027-13-01T00:00:00', '2027-00-10T00:00:00', '2027-01-00T00:00:00',
    '2027-01-15T25:00:00', '2027-01-15T08:60:00', '2027-01-15T08:00:00+99:00', '2027-01-15T08:00:00-23:59:59.999999',
    '2027-01-15T08:00:00+0530', '2027-01-15T08:00:00+053015', '2027-01-15T08:00:00+053015.25', '2027-01-15T08:00:00+05:3015',
    '2027-01-15T08:00:00.', '2027-01-15T08:00:00,', '2027-01-15T08:00:00.5Z+01:00', '2027-01-15T08:00:00-',
    '2027-01-15T08:00:00+', '2027-01-15T', '2027-01-15TZ', '2027-01-15T08Z', '2027-01-15T08:00:00.٣Z',
]

JSON = [
    '{}', '[]', '1', '-0', '1.5', '1e5', '1E-5', '1e400', '-1e400', '1e-400', 'NaN', 'Infinity', '-Infinity',
    '[NaN, Infinity, -Infinity]', '{"a": NaN}', 'nan', 'inf', '+1', '01', '1.', '.5', '-', '9007199254740993',
    '18446744073709551615', '18446744073709551616', '-9223372036854775808', '-9223372036854775809',
    '100000000000000000000000000001', '[100000000000000000000000000001, -100000000000000000000000000001]',
    '12345678901234567890.0', '"NaN"', '"a\\u00e9b"', '"\\ud83d\\ude00"', '"tab\\tnew\\nline"', '"raw\ttab"',
    '  {"a": 1}  ', '\t\n\r[1]\r\n', '﻿{}', '{"a": 1, "a": 2}', '{"a": 1,}', '[1,]', '{"a" 1}', "{'a': 1}",
    'true', 'false', 'null', 'True', 'None', '"unterminated', '[1] [2]', '', ' ', '{"truncated": ',
    '{"timestamp": "2027-01-15T08:00:00Z", "n": 1.0}', '1.0', '100.0', '0.1', '-0.0', '1e16', '2.5e-7',
    ' {}', '{"k": " "}', '[1e309, -1e309]', '[NaN]x', 'Infinityx', '-NaN', '[-Infinity1]',
]

LINES = ['', 'a', 'a\n', 'a\nb', 'a\r\nb', 'a\rb', 'a\r\r\nb', '\n', '\n\n', 'a\n\n', 'a\x0bb', 'a\x0cb', 'a\x1cb',
         'a\x1db', 'a\x1eb', 'a\x85b', 'a b', 'a b', 'a\x1fb', 'a\tb', 'a\u0085', '\r\n\r\n', 'x\r', 'é\nü']

REPR = ['', 'a', "it's", 'say "hi"', 'both \' and "', 'back\\slash', 'new\nline', 'tab\t', 'cr\r', 'nul\x00',
        'bell\x07', 'del\x7f', 'c1\x85', 'nbsp ', 'soft­hyphen', 'é', '日本', '😀', 'ls ', 'ps ',
        'bom﻿', 'zwsp​', 'rtl‮', 'priv', 'thin space', 'brain.snapshot', 'fixes.nudge',
        "thresholds.quiet_minutes", 'run_found.a.b']

TREE = ['a.jsonl', 'b.jsonl', '.hidden.jsonl', 'c.txt', 'sub/d.jsonl', 'sub/.e.jsonl', '.dot/f.jsonl', 'sub/deeper/g.jsonl',
        'with space/h.jsonl', 'Upper.JSONL', 'x[1].jsonl']
GLOBS = ['*.jsonl', '*', '*/*.jsonl', '.*', '.*/*.jsonl', 'sub/*', 'sub/.*', '**/*.jsonl', 'sub/**', '*/*/*.jsonl',
         'with space/*', 'a.jsonl', 'missing.jsonl', '?.jsonl', '[ab].jsonl', '[!a].jsonl', 'x[[]1].jsonl',
         '*.JSONL', 'sub', 'sub/', '*/', 'nope/*']


def canonical(v):
    """Python's value as JSON the Rust test can compare: NaN, infinities and ints past 64 bits are tagged."""
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, int):
        return v if -2**63 <= v < 2**64 else {'$py': 'int', 'v': str(v)}
    if isinstance(v, float):
        if math.isnan(v):
            return {'$py': 'nan'}
        if math.isinf(v):
            return {'$py': 'inf' if v > 0 else '-inf'}
        return v
    if isinstance(v, list):
        return [canonical(x) for x in v]
    return {k: canonical(x) for k, x in v.items()}


def iso(s):
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    t = t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    d = t - dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    return str((d.days * 86400 + d.seconds) * 10**9 + d.microseconds * 1000)


def loads(s):
    try:
        return {'ok': canonical(json.loads(s))}
    except ValueError:
        return {'error': True}


def main():
    tree = os.path.join(DATA, 'glob_tree')
    for rel in TREE:
        p = os.path.join(tree, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'w').close()
    here = os.getcwd()
    os.chdir(tree)
    try:
        globs = [{'in': g, 'out': sorted(glob.glob(g))} for g in GLOBS]
    finally:
        os.chdir(here)
    out = {'fromisoformat': [{'in': s, 'out': iso(s)} for s in ISO],
           'json_loads': [{'in': s, **loads(s)} for s in JSON],
           'splitlines': [{'in': s, 'out': s.splitlines()} for s in LINES],
           'repr_str': [{'in': s, 'out': repr(s)} for s in REPR],
           'glob': globs}
    with open(os.path.join(DATA, 'pyrules.json'), 'w') as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print(sys.version.split()[0], {k: len(v) for k, v in out.items()})


if __name__ == '__main__':
    main()
