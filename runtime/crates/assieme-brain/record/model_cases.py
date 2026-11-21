"""Records `assieme/model.py` into tests/data/model.json for `src/model.rs`. Run from the repo root:
    .venv/bin/python runtime/crates/assieme-brain/record/model_cases.py"""
import datetime as dt
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
sys.path.insert(0, ROOT)
from assieme import model  # noqa: E402

DATA = os.path.join(ROOT, 'runtime/crates/assieme-brain/tests/data')

PARSE_TS = ['2027-01-15T08:00:00Z', '2027-01-15T08:00:00z', '2027-01-15T08:00:00ZZ', '2027-01-15T08:00:00.5Z',
            '2027-01-15T08:00:00', '2027-01-15T08:00:00+02:00', 'Z2027-01-15', '2027-01-15Z', '2027-W03-5T08Z',
            '2027-01-15T24:00:00Z', 'yesterday', '', 12345, 1.5, None, True, ['2027-01-15'], {'t': 1},
            '2027-01-15T08:00:00.123456789Z', '2027-01-15 08:00:00Z']

T = '2027-01-15T08:00:00Z'
SCRIPTS = [
    [['wake', 'human', 'human', 'fix the  tests\nplease', True, T, 'main'], ['call', 100, 5, T], ['call', 300, 0, None]],
    [['wake', 'a', 'asm', 'hidden text', False, None, None], ['call', 10, 1, T]],
    [['call', 50, 2, T], ['call', 70, 0, T]],  # calls before any wake count on the session only
    [['wake', 'b', 'team', 'x' * 200, True, None, None], ['wake', 'c', 'hc', ' \t lead\x1cand tail \xa0 ', True, T, None],
     ['call', 5, 0, None]],
    [['wake', 'd', 'spawn', 'é' * 90, True, T, 'b']],
    [],
]


def ns(t):
    if t is None:
        return None
    d = t - dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    return str((d.days * 86400 + d.seconds) * 10**9 + d.microseconds * 1000)


def run(script):
    s = model.Session('agent', 'claude')
    for op in script:
        if op[0] == 'wake':
            _, sender, channel, text, show, ts, branch = op
            s.wake(sender, channel, text, show_text=show, ts=model.parse_ts(ts), branch=branch)
        else:
            _, inp, outp, ts = op
            s.call(inp, outp, ts=model.parse_ts(ts))
    return {'calls': s.calls, 'tokens': s.tokens, 'output': s.output, 'peak': s.peak,
            'samples': [[ns(t), n] for t, n in s.samples],
            'turns': [{'sender': t.sender, 'channel': t.channel, 'calls': t.calls, 'tokens': t.tokens, 'text': t.text,
                       'ts': ns(t.ts), 'changed': t.changed, 'branch': t.branch} for t in s.turns]}


def main():
    out = {'parse_ts': [{'in': x, 'out': ns(model.parse_ts(x))} for x in PARSE_TS],
           'session': [{'in': s, 'out': run(s)} for s in SCRIPTS]}
    with open(os.path.join(DATA, 'model.json'), 'w') as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print({k: len(v) for k, v in out.items()})


if __name__ == '__main__':
    main()
