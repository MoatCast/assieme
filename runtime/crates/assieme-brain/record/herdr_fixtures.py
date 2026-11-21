"""Records what Python's herdr.py answers for each command line, script and saved workspace: `tests/data/herdr.json`.

Run from the repository root with the repo's own Python:  .venv/bin/python runtime/crates/assieme-brain/record/herdr_fixtures.py
The other modules it asks (New team's models and checks, git) are replaced by the fixed answers the Rust test uses.
"""
import builtins
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
sys.path.insert(0, ROOT)
from assieme import herdr, launch, newteam  # noqa: E402

KNOWN = {'claude': {'fable', 'opus', 'sonnet', 'haiku'}, 'codex': {'gpt-5.5', 'gpt-5.4'}}
herdr._known_models = lambda: KNOWN
herdr._jobs = lambda: {'planner': 'Plans.', 'builder': 'Builds.', 'reviewer': 'Reviews.', 'tester': 'Tests.'}
newteam.kind_brief = lambda kind: f'standard {kind} brief'


def validate(roles):
    if any('refuse' in r['name'] for r in roles):
        raise newteam.NewTeamError('roles', 'No refusing here.')


newteam.validate = validate
launch.check_model_effort = lambda agent, model, effort: None if effort in ('low', 'medium', 'high', 'xhigh') else 'bad effort'


def checkout(cwd):
    """Folders under /w/<project>/wt/<name> are worktrees of /w/<project>, on a branch named after the folder."""
    if cwd and '/wt/' in cwd:
        return cwd.split('/wt/')[0], 'branch-' + os.path.basename(cwd)
    return cwd, None


herdr._checkout = checkout
SCRIPTS = {}
real_open = builtins.open


def fake_open(path, *a, **k):
    import io
    if isinstance(path, str) and path.startswith('/scripts/'):
        return io.StringIO(SCRIPTS[path])
    return real_open(path, *a, **k)


herdr.open = fake_open  # the module's own `open`, not the builtin

CLAUDE = [
    [], ['hello', 'world'], ['--model', 'opus'], ['--model=sonnet', 'do it'], ['--model', 'claude-opus-4-5-20251101'],
    ['--model', 'nonsense-model'], ['--model', 'bad model!'], ['--effort', 'high'], ['--effort', 'ludicrous'],
    ['--permission-mode', 'plan', 'review it'], ['--permission-mode', 'bypassPermissions'], ['--permission-mode', 'acceptEdits'],
    ['--dangerously-skip-permissions', 'go'], ['--allowedTools', 'Bash', 'Edit', '--', 'x'], ['--allowedTools', 'Bash,Edit', 'prompt'],
    ['--disallowedTools', 'Edit', 'Write'], ['--disallowed-tools', 'Write,Read', 'hi'], ['--disallowedTools', 'Bash'],
    ['--add-dir', '/a', '/b', 'then text'], ['--append-system-prompt', 'Be brief.', 'start'], ['--system-prompt', 'You are X.'],
    ['--append-system-prompt-file', 'nope.md'], ['--system-prompt-file', '/scripts/missing-brief.md', 'go'],
    ['--settings', 'x.json', '--mcp-config', 'm.json', 'go'], ['--verbose', '--debug', 'go'], ['--resume', 'abc', 'x'],
    ['--output-format', 'json', '-p', 'task'], ['a', None, 'b'], ['--model', None], ['--append-system-prompt', None],
    ['--effort', None, 'x'], ['--', '--model', 'opus'], ['-', 'x'], ['--name', 'n', '--session-id', 's'], ['\x01', 'go'],
    ['--model', 'opus', '\x01'], ['--allowedTools'], ['--add-dir'], ['--unknown-flag=3', 'x'],
]
CODEX = [
    [], ['fix it'], ['exec', 'fix it'], ['e', 'fix it'], ['-m', 'gpt-5.5'], ['--model', 'gpt-4'], ['--model=gpt-5.4', 'go'],
    ['-c', 'model=gpt-5.5'], ['-c', 'model="gpt-5.4"', 'x'], ['-c', "model='nope'"], ['-c', 'model_reasoning_effort=high'],
    ['-c', 'model_reasoning_effort="medium"', '-c', 'sandbox_mode=read-only'], ['-s', 'read-only', 'look'], ['--sandbox', 'danger-full-access'],
    ['--sandbox', 'workspace-write'], ['-a', 'never'], ['--ask-for-approval', 'on-request'], ['--full-auto'], ['--yolo', 'x'],
    ['--dangerously-bypass-approvals-and-sandbox'], ['-c', 'sandbox_workspace_write.network_access=true'], ['-c', 'foo=bar', '-c', 'baz'],
    ['-c', None], ['-C', '/somewhere', 'go'], ['-i', 'a.png', 'describe'], ['--profile', 'p', '--enable', 'f'], ['-m', None],
    ['exec', '--model', 'gpt-5.5', 'x', None], ['--config', 'model=gpt-5.5'], ['--search', 'q'], ['\x01', 'x'],
]
NAMES = ['builder-1', 'reviewer', 'planner', 'test-runner', 'lead', 'coordinator', 'dev']
agent_roles = []
for kind, cases in (('claude', CLAUDE), ('codex', CODEX)):
    for args in cases:
        for name, env, holds, base in ((NAMES[len(agent_roles) % 7], [], False, None),
                                       ('builder', ['PATH', 'FOO', 'API_KEY'], True, '/b')):
            role, notes = herdr.agent_role(name, kind, list(args), env, holds_task=holds, base=base, known=KNOWN)
            agent_roles.append({'name': name, 'kind': kind, 'args': args, 'env': env, 'holds': holds, 'base': base,
                                'role': role, 'notes': [list(n) for n in notes]})

ARGV = [
    ['claude'], ['/usr/local/bin/claude', '--model', 'opus'], ['env', 'FOO=1', 'claude', 'x'], ['A=1', 'B=2', 'codex', 'x'],
    ['bash', '-c', 'claude --model opus "do things"'], ['sh', '-c', 'cd /x && FOO=bar codex exec "hi"'],
    ['zsh', '-c', 'echo hi\nexec claude --effort high'], ['bash', '-c', 'echo only'], ['bash', '-c'], ['npm', 'run', 'dev'],
    ['bash', '-c', 'X=$(command -v claude); $X -p hi'], [None], ['claude', None], [], ['bash', '-c', 'aider --yes'],
    ['bash', '-c', 'PATH="$PATH" gemini'], ['env', 'env', 'X=1', 'pi'],
]
splits = [{'argv': a, 'out': list(herdr._split(a))} for a in ARGV]

WORDS = [
    ('claude --model opus "do it"', {}), ('FOO=$HOME/x claude', {'HOME': '/h'}), ("echo 'a $B' c", {'B': '1'}),
    ('echo ${A:-def} ${B:+yes} ${C:-} $D', {'A': '', 'B': 'b'}), ('echo ${opts[@]} x', {}), ('a b | c d', {}),
    ('a b && c', {}), ('a b; c', {}), ('a b > out', {}), ('"unterminated', {}), ("it's", {}), ('a\\ b c', {}),
    ('echo $(command -v claude) x', {}), ('echo $(dirname /a/b/c) $(basename /a/b/c/) $(which x)', {}),
    ('echo $(cd /a/../b && pwd)', {}), ('echo $(unknown thing)', {}), ('echo $(nested $(command -v x))', {}),
    ('PATH="$PATH" run', {}), ('a#b c', {}), ('"a b"c\'d e\'', {}), ('x ${BASH_SOURCE[0]} $0 ${1}', {'BASH_SOURCE[0]': '/s/x.sh', '0': '/s/x.sh', '1': 'one'}),
    ('~/bin/x ~ ~user', {}), ('echo "$A and ${B:-none}"', {'A': 'aa'}), ("echo '${NOPE}'", {}), ('echo "esc \\" q \\\\ \\n"', {}),
    ('', {}), ('   ', {}), ('a  b\tc', {}), ('2>&1 x', {}), ('x (y) z', {}),
]
words = [{'text': t, 'env': e, 'out': herdr._words(t, dict(e))} for t, e in WORDS]

HC_SCRIPT = '''#!/bin/bash
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# comment
hc run --name lead --cwd /w/app --label x=y -- claude --model opus "Plan the work"
hc run --name builder-1 --cwd /w/app/wt/one --parent lead -- codex exec "Build it"
hc run --name reviewer --cwd /w/app/wt/two --parent lead --on foo -- claude --permission-mode plan
hc run --name shell --cwd /w/app -- bash
herdr agent start dev --kind claude --pane 3 -- --model sonnet
herdr agent start other --kind gemini -- x
'''
FUNC_SCRIPT = '''#!/bin/bash
ROOT=/w/app
start() {
  hc run --name "$1" --cwd "$ROOT/wt/$1" --parent lead -- claude --model haiku "$2"
}
hc run --name lead --cwd $ROOT -- claude "Lead the team"
start alpha "do alpha"
start beta "do beta"
start alpha "again"
'''
OPTS_SCRIPT = '''opts=(--model opus)
hc run --name a --cwd /w/x ${opts[@]} -- claude hi
hc run --name b --cwd /w/x -- ${CLI} hi
hc run --name --cwd /w/x -- claude
hc run --cwd /w/y -- codex x
if true; then hc run --name c --cwd /w/z -- claude; fi
echo "hc run --name no --cwd /w -- claude"
'''
MULTI_SCRIPT = '''hc run --name boss --cwd /w/one -- sh -c 'claude --model opus "plan
  and more"'
hc run --name refuse-me --cwd /w/one -- claude hi
hc run --name boss --cwd /w/one -- codex x
hc run --name "" --cwd /w/two -- claude
hc run --name w --cwd /w/three/wt/w1 --parent nobody -- claude
'''
NONE_SCRIPT = "echo nothing here\n"
for name, text in (('hc', HC_SCRIPT), ('func', FUNC_SCRIPT), ('opts', OPTS_SCRIPT), ('multi', MULTI_SCRIPT), ('none', NONE_SCRIPT)):
    SCRIPTS[f'/scripts/{name}.sh'] = text
scripts = [{'name': n, 'text': SCRIPTS[f'/scripts/{n}.sh'], 'draft': herdr._from_script(f'/scripts/{n}.sh')} for n in
           ('hc', 'func', 'opts', 'multi', 'none')]

WORKSPACES = [
    {'id': 1, 'custom_name': 'one', 'identity_cwd': '/w/app', 'tabs': [{'panes': {
        'a': {'managed_agent_kind': 'claude', 'agent_name': 'lead', 'cwd': '/w/app'},
        'b': {'agent_session': {'agent': 'codex'}, 'cwd': '/w/app/wt/b'},
        'c': {'cwd': '/w/app'}}}, {'panes': {'d': {'managed_agent_kind': 'pi', 'cwd': '/w/other'}}}]},
    {'id': 2, 'tabs': []}, {'id': 3, 'tabs': [{'panes': {'a': {'managed_agent_kind': 'claude'}}}]},
    {'id': 4, 'tabs': [{'panes': {'a': {'managed_agent_kind': 'claude', 'agent_name': 'Name With Spaces!', 'cwd': '/w/q'},
                                  'b': {'managed_agent_kind': 'claude', 'agent_name': 'Name With Spaces!', 'cwd': '/w/q'}}}]},
]
workspaces = [{'workspace': w, 'draft': herdr._from_workspace(w), 'panes': len(herdr._agent_panes(w))} for w in WORKSPACES]

path = os.path.join(os.path.dirname(__file__), '..', 'tests', 'data', 'herdr.json')
with open(path, 'w') as f:
    json.dump({'agent_roles': agent_roles, 'splits': splits, 'words': words, 'scripts': scripts, 'workspaces': workspaces},
              f, indent=0, ensure_ascii=False)
    f.write('\n')
print(len(agent_roles), 'agent roles,', len(splits), 'splits,', len(words), 'words,', len(scripts), 'scripts')
