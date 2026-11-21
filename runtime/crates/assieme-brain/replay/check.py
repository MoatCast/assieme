"""The one check: build a tiny made-up recording (no owner data) and replay it to both brains. Run it from the repo."""
import datetime as dt
import json
import os
import subprocess
import sys
import tarfile
import tempfile

NOW = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.timezone.utc)
ago = lambda m: (NOW - dt.timedelta(minutes=m)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
ROWS = [{'type': 'user', 'timestamp': ago(30), 'message': {'content': 'go'}},
        {'type': 'assistant', 'timestamp': ago(2), 'message': {'id': 'm1', 'stop_reason': 'tool_use', 'usage': {'input_tokens': 1},
         'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {'command': 'cargo build'}}]}}]
with tempfile.TemporaryDirectory() as d:
    p = f'{d}/claude/-Users-x-demo/s.jsonl'
    os.makedirs(os.path.dirname(p))
    open(p, 'w').write('\n'.join(map(json.dumps, ROWS)) + '\n')
    t = (NOW - dt.timedelta(minutes=2)).timestamp()
    os.utime(p, (t, t))
    os.makedirs(f'{d}/codex'), os.makedirs(f'{d}/state')
    for name, body in (('ps.txt', ''), ('lsof.txt', ''), ('core.json', '{}'), ('meta.json', json.dumps({'now': NOW.isoformat()}))):
        open(f'{d}/{name}', 'w').write(body)
    with tarfile.open(f'{d}/r.tar', 'w') as tar:
        tar.add(d, arcname='.', filter=lambda i: None if i.name.endswith('r.tar') else i)
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), 'replay.py'), f'{d}/r.tar', '--wait', '1'] + sys.argv[1:],
                       capture_output=True, text=True)
print(r.stdout, r.stderr[-1500:])
sys.exit(0 if r.returncode in (0, 1) and 'Traceback' not in r.stderr else 1)
