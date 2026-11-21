"""Records quiet's state-file helpers (the foundation's part of `assieme/quiet.py`) into tests/data/quiet.json for
`src/quiet.rs`. Run from the repo root: .venv/bin/python runtime/crates/assieme-brain/record/quiet_cases.py

The files read live in tests/data/quiet_files (committed; their paths are relative to the crate)."""
import json
import os
import sys
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
sys.path.insert(0, ROOT)
from assieme import quiet  # noqa: E402

CRATE = os.path.join(ROOT, 'runtime/crates/assieme-brain')
FILES = ['good.json', 'list.json', 'damaged.json', 'empty.json', 'bom.json', 'nan.json', 'not_utf8.json',
         'a_folder.json', 'with space.json', 'missing.json', 'missing_folder/x.json']
ENVS = [{'HOME': '/home/x'}, {'HOME': '/home/x', 'XDG_STATE_HOME': '/state'}, {'HOME': '/home/x', 'XDG_STATE_HOME': ''},
        {'HOME': '/home/with space/', 'XDG_STATE_HOME': 'relative/state'}]


def canonical(v):
    return {'$py': 'nan'} if isinstance(v, float) and v != v else \
        [canonical(x) for x in v] if isinstance(v, list) else {k: canonical(x) for k, x in v.items()} if isinstance(v, dict) else v


def main():
    here = os.getcwd()
    os.chdir(CRATE)
    try:
        reads = [{'in': f'tests/data/quiet_files/{f}', 'out': canonical(quiet._read_json(f'tests/data/quiet_files/{f}', 'DEFAULT'))}
                 for f in FILES]
    finally:
        os.chdir(here)
    dirs = []
    for env in ENVS:
        with mock.patch.dict(os.environ, env, clear=True):
            dirs.append({'in': env, 'out': quiet.state_dir()})
    out = {'read_json': reads, 'state_dir': dirs}
    with open(os.path.join(CRATE, 'tests/data/quiet.json'), 'w') as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print({k: len(v) for k, v in out.items()})


if __name__ == '__main__':
    main()
