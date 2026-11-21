"""Writes tests/data/app_calls.json: every brain call in app/src/bridge.ts, with the keys of its params object.
Run from the repo root: python3 runtime/crates/assieme-brain/record/app_calls.py
`{ id }` and `{ a: x }` both give the key; `...spread` and a params variable give none."""
import json
import re

HERE = 'runtime/crates/assieme-brain'
text = open('app/src/bridge.ts').read()
calls = {}
for m in re.finditer(r'\bbrain(?:<.*?>)?\(\s*"([\w.]+)"\s*(?:,\s*\{([^}]*)\})?', text, re.S):
    keys = [p.split(':')[0].strip() for p in (m.group(2) or '').split(',') if p.strip() and not p.strip().startswith('...')]
    calls.setdefault(m.group(1), set()).update(keys)
rows = [{'method': k, 'keys': sorted(v)} for k, v in sorted(calls.items())]
with open(f'{HERE}/tests/data/app_calls.json', 'w') as f:
    json.dump(rows, f, indent=1)
    f.write('\n')
print(len(rows), 'methods')
