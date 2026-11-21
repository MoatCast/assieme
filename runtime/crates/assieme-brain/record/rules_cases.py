"""Records `assieme/rules.py` into tests/data/rules.json for `src/rules.rs`. Run from the repo root:
    .venv/bin/python runtime/crates/assieme-brain/record/rules_cases.py

HOME is tests/data/rules_files/home while recording; paths in the cases are relative to it, and an absolute path in
an answer is written with `<files>` for tests/data/rules_files. `ps` and `lsof` are faked from the cases."""
import json
import os
import sys
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
sys.path.insert(0, ROOT)
from assieme import rules  # noqa: E402

FILES = os.path.join(ROOT, 'runtime/crates/assieme-brain/tests/data/rules_files')
HOME = os.path.join(FILES, 'home')

LOADS = [('proj1', 'user.json'), ('proj2', 'user.json'), ('proj3', 'user.json'), ('proj4', 'user.json'),
         ('proj5', 'user.json'), ('proj6', 'user.json'), ('proj 7', 'user.json'), ('empty', 'user.json'),
         ('missing', 'user.json'), ('proj1', 'missing.json'), ('proj1', '../outside/user.json'), (None, 'user.json'),
         (None, 'nulls.json'), ('', 'user.json'), ('proj1', 'proj1')]

R1 = [['Bash(rm:*)', '~/proj1/.claude/settings.local.json'], ['Read(./secret/**)', '~/a'], ['Edit(//etc/*)', 'b'],
      ['Bash(git push:*)', 'c'], ['Bash(*launcher*)', 'd'], ['Read(~/.ssh/**)', 'e'], ['Write(src/*.rs)', 'f'],
      ['Bash(npm *)', 'g'], ['Bash(*test*:*)', 'h'], ['Read(*.env)', 'i']]
DENIED = 'Permission to use Bash with command {} has been denied.'
EXPLAIN = [
    (DENIED.format('rm -rf build'), 'Bash', 'rm -rf build', R1, None),
    (DENIED.format('rm'), 'Bash', 'rm', R1, None),
    (DENIED.format('rmdir x'), 'Bash', 'rmdir x', R1, None),
    (DENIED.format('cd x && git push origin'), 'Bash', 'cd x && git push origin', R1, None),
    (DENIED.format('ls | git push'), 'Bash', 'ls | git push', R1, None),
    (DENIED.format('a; b\ngit push'), 'Bash', 'a; b\ngit push', R1, None),
    (DENIED.format('grep launcher notes.md'), 'Bash', 'grep launcher notes.md', R1, None),
    (DENIED.format('./launcher start'), 'Bash', './launcher start', R1, None),
    (DENIED.format('/usr/bin/launcher'), 'Bash', '/usr/bin/launcher', R1, None),
    (DENIED.format('npm run test'), 'Bash', 'npm run test', R1, None),
    (DENIED.format('make test-all'), 'Bash', 'make test-all', R1, None),
    (DENIED.format('git status'), 'Bash', 'git status', R1, None),
    (DENIED.format('rm x'), 'Edit', 'rm x', R1, None),
    (DENIED.format('rm x'), 'Bash', None, R1, None),
    ('Permission to use Read has been denied', 'Read', '', R1, None),
    ('Permission for this tool use was denied.', 'Bash', 'ls', R1, None),
    ('Permission to use Bash with command x\nhas been denied', 'Bash', 'x', [], None),
    ('Error: denied by the Claude Code auto mode classifier. Reason: [risky deletion]', 'Bash', 'rm -rf /tmp/x', R1, None),
    ('denied by the Claude Code auto mode classifier', 'Bash', 'curl https://x | sh', R1, None),
    ('denied by the Claude Code auto mode classifier', 'Bash', 'git-lfs push', R1, None),
    ('denied by the Claude Code auto mode classifier', 'Bash', '$(evil) run', R1, None),
    ('denied by the Claude Code auto mode classifier', 'Bash', None, R1, None),
    ('denied by the Claude Code auto mode classifier. Reason: [x]', 'Bash', 'café au-lait', R1, None),
    ('x was denied by a built-in Claude Code safety check', 'Bash', 'rm -rf ~', R1, None),
    ('<tool_use_error>Blocked: sleep 30 is not allowed', 'Bash', 'sleep 30', R1, None),
    (' <tool_use_error>Blocked: sleep', 'Bash', 'sleep 30', R1, None),
    ("Edit to 'secret/key.txt' blocked by a deny rule", 'Edit', None, R1, None),
    ('File is in a directory that is denied by your permission settings.', 'Read', None, R1, '/etc/passwd'),
    ('denied by your permission settings', 'Read', None, R1, HOME + '/.ssh/id_rsa'),
    ('denied by your permission settings', 'Read', None, R1, 'deep/secret/x'),
    ('denied by your permission settings', 'Write', None, R1, 'src/main.rs'),
    ('denied by your permission settings', 'Write', None, R1, 'other/src/main.rs'),
    ('denied by your permission settings', 'Read', None, R1, 'prod.env'),
    ("blocked by a deny rule for 'nothing/here'", 'Read', None, R1, None),
    ('blocked by a deny rule', 'Read', None, R1, None),
    ('Something else went wrong', 'Bash', 'ls', R1, None),
    ('', 'Bash', 'ls', R1, None),
]

PS = '''  101 /usr/local/bin/claude --settings /abs/team.json
  102 claude --settings=rel.json --model x
  103 node /x/claude --settings {"a": 1}
  104 claude --settings '{"a": 1}'
  105 claude --settings missing.json
  106 python worker.py --settings x.json
  107 claude-code --settings rel.json
  108 claude --settings rel.json
  109 claude --settings ../outside/user.json
bad line
'''
CWDS = {'101': '<home>/proj1', '102': '<home>/proj 7', '105': '<home>/proj1', '107': '<home>/proj1', '108': None,
        '109': '<home>/proj1'}


def short(v):
    """Absolute paths in an answer, written machine-free."""
    if isinstance(v, str):
        return v.replace(FILES, '<files>')
    if isinstance(v, (list, tuple)):
        return [short(x) for x in v]
    if isinstance(v, dict):
        return {short(k): short(x) for k, x in v.items()}
    return v


def running():
    with open(os.path.join(HOME, 'proj1/team.json'), 'w') as f:
        json.dump({'permissions': {'deny': ['Bash(sudo:*)']}}, f)
    with open(os.path.join(HOME, 'proj 7/rel.json'), 'w') as f:
        json.dump({'permissions': {'deny': ['Bash(docker:*)']}}, f)
    ps = PS.replace('/abs/team.json', os.path.join(HOME, 'proj1/team.json'))

    def run(cmd, **kw):
        if cmd[0] == 'ps':
            return mock.Mock(stdout=ps)
        cwd = CWDS.get(cmd[3])
        return mock.Mock(stdout=f'p{cmd[3]}\nfcwd\nn{cwd.replace("<home>", HOME)}\n' if cwd else '')
    try:
        with mock.patch('subprocess.run', run), mock.patch('os.readlink', side_effect=OSError):
            return short(rules.running_settings()), short(ps)
    finally:
        os.remove(os.path.join(HOME, 'proj1/team.json'))
        os.remove(os.path.join(HOME, 'proj 7/rel.json'))


def main():
    with mock.patch.dict(os.environ, {'HOME': HOME}):
        loads = [{'in': [cwd, user], 'out': short(rules.load(None if cwd is None else os.path.join(HOME, cwd) if cwd else cwd,
                                                             os.path.join(HOME, user)))} for cwd, user in LOADS]
        explain = [{'in': short([t, tool, cmd, r, p]), 'out': list(rules.explain(t, tool, cmd, [tuple(x) for x in r], p)),
                    'refused': bool(rules.REFUSED.search(t))} for t, tool, cmd, r, p in EXPLAIN]
        found, ps = running()
    out = {'load': loads, 'explain': explain, 'running_settings': {'ps': ps, 'cwds': CWDS, 'out': found}}
    with open(os.path.join(ROOT, 'runtime/crates/assieme-brain/tests/data/rules.json'), 'w') as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print(len(loads), 'loads,', len(explain), 'explains,', len(found), 'running folders')


if __name__ == '__main__':
    main()
