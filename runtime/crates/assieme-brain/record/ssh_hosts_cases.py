"""Records `remote.ssh_hosts` into tests/data/ssh_hosts.json for `src/remote.rs`. Run from the repo root:
    .venv/bin/python runtime/crates/assieme-brain/record/ssh_hosts_cases.py"""
import json
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
sys.path.insert(0, ROOT)
from assieme import remote  # noqa: E402

CONFIGS = [
    '',
    'Host gpu-box\n  HostName 10.0.0.5\n  User ana\n  Port 2222\n',
    'Host a b\n  HostName shared.example\nHost *\n  User nobody\nHost c? !d e\nHost e\n  HostName=e.example\n  User\tbob\n',
    'host Lower\n  hostname first\n  HostName second\n  USER x\nMatch host z\n  HostName ignored\nHost after\n',
    '# comment\n\nHost only\n',
]
cases = []
for text in CONFIGS:
    with tempfile.NamedTemporaryFile('w', suffix='.config', delete=False) as f:
        f.write(text)
    cases.append({'config': text, 'hosts': remote.ssh_hosts(f.name)})
    os.unlink(f.name)
with open(os.path.join(os.path.dirname(__file__), '../tests/data/ssh_hosts.json'), 'w') as f:
    json.dump(cases, f, indent=1)
    f.write('\n')
