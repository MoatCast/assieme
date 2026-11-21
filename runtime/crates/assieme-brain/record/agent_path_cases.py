"""Records `setup.agent_path` into tests/data/agent_path.json for `setup::agent_path` in `src/setup.rs`. Run from
the repo root: .venv/bin/python runtime/crates/assieme-brain/record/agent_path_cases.py

The folders are tests/data/agent_path (committed, with their modes and symlinks). In the cases `<d>` stands for that
folder; HOME is `<d>/home`. PATH and the login shell's PATH come from each case."""
import json
import os
import sys
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
sys.path.insert(0, ROOT)
from assieme import setup  # noqa: E402

D = os.path.join(ROOT, 'runtime/crates/assieme-brain/tests/data/agent_path')
B1, B2, L1, L2 = '<d>/bin1', '<d>/bin2', '<d>/login 1', '<d>/login2'
CASES = [
    ('claude', B1, ''), ('claude', f'{B2}:{B1}', ''), ('codex', B1, ''), ('codex', B1, L1), ('gemini', B1, ''),
    ('gemini', B1, L2), ('opencode', B1, f'{L1}:{L2}'), ('agy', B1, ''), ('agy', '', ''), ('cursor-agent', B1, ''),
    ('nothing', B1, f'{L1}:{L2}'), ('oc-link', '', L1), ('broken', '', L1), ('opencode', '', f'::{L2}:'),
    ('claude', f'{B1}:{B1}', ''), ('', B1, L1), ('<d>/bin2/claude', '', ''), ('<d>/bin1/codex', '', ''),
    ('codex', f'{B1}:{L1}', ''), ('claude', None, L1), ('gemini', '<d>/missing', ''), ('codex', '', f'{L1} '),
]


def main():
    out = []
    for name, path, login in CASES:
        env = {'HOME': os.path.join(D, 'home')}
        if path is not None:
            env['PATH'] = path.replace('<d>', D)
        with mock.patch.dict(os.environ, env, clear=True):
            got = setup.agent_path(name.replace('<d>', D), login_path=lambda login=login: login.replace('<d>', D))
        out.append({'in': {'name': name, 'path': path, 'login': login}, 'out': got.replace(D, '<d>') if got else None})
    with open(os.path.join(ROOT, 'runtime/crates/assieme-brain/tests/data/agent_path.json'), 'w') as f:
        json.dump(out, f, indent=1)
        f.write('\n')
    print(len(out), 'cases')


if __name__ == '__main__':
    main()
