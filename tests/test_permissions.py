"""Permission levels and the person's rules, as Claude Code settings and Codex sandbox and program rules."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock

from assieme import launch, newteam, permissions, preset, prompts, team, worktree
from assieme.preset import Role


def role(agent='claude', level='safe', owns=(), rules=(), checkout='branch'):
    return Role('builder-1', agent, 'b.md', 'brief', None, checkout, level=level, owns=list(owns), rules=list(rules))


BLOCK_RUN = {'decision': 'block', 'program': ['build/cloud/run.sh'], 'words': 'Don’t run the cloud launcher'}
ALLOW_LINT = {'decision': 'allow', 'program': ['make', 'lint'], 'words': 'It may run the linter'}


def settings(argv):
    return json.loads(argv[argv.index('--settings') + 1])


class RuleCheckTest(unittest.TestCase):
    def test_plain_program_words(self):
        self.assertEqual(permissions.check_rule('block', ['npm', 'publish']), ['npm', 'publish'])

    def test_push_stays_blocked_at_every_level(self):
        for program in (['git', 'push'], ['git', 'push', 'origin'], ['git'], ['git', 'tag']):
            with self.assertRaisesRegex(ValueError, 'Pushing stays blocked'):
                permissions.check_rule('allow', program)
        permissions.check_rule('block', ['git'])  # blocking more is always fine

    def test_push_floor_by_any_git_path(self):
        for program in (['/usr/bin/git'], ['/opt/homebrew/bin/git', 'push'], ['./git', 'tag']):
            with self.assertRaisesRegex(ValueError, 'Pushing stays blocked'):
                permissions.check_rule('allow', program)

    def test_no_bare_interpreter_allow(self):
        for program in (['python3'], ['python'], ['node'], ['npx'], ['uvx'], ['pnpm', 'dlx'], ['bunx'], ['deno'],
                        ['perl'], ['ruby'], ['osascript'], ['make'], ['/usr/bin/python3'], ['python3', '-c'],
                        ['uv', 'run'], ['npm', 'exec']):
            with self.assertRaisesRegex(ValueError, 'anything', msg=program):
                permissions.check_rule('allow', program)
        self.assertEqual(permissions.check_rule('allow', ['python3', 'scripts/check.py']), ['python3', 'scripts/check.py'])
        permissions.check_rule('allow', ['make', 'lint'])
        permissions.check_rule('block', ['python3'])  # blocking is always fine

    def test_no_launcher_allow(self):
        with self.assertRaisesRegex(ValueError, 'anything run through it'):
            permissions.check_rule('allow', ['bash'])

    def test_no_shell_symbols(self):
        for bad in (['rm -rf'], ['curl', '*'], ['a;b'], ['$(x)'], [], ['a'] * 7):
            with self.assertRaises(ValueError):
                permissions.check_rule('block', bad)
        with self.assertRaises(ValueError):
            permissions.check_rule('maybe', ['ls'])

    def test_owned_paths_stay_inside(self):
        self.assertIsNone(permissions.check_owns(['payments/retry.py', 'src/*.py']))
        for bad in (['/etc/passwd'], ['../x'], ['a/../../b'], 'x'):
            self.assertTrue(permissions.check_owns(bad))

    def test_script_variants(self):
        self.assertEqual(permissions.variants(['./build/run.sh', 'x']),
                         [['build/run.sh', 'x'], ['./build/run.sh', 'x'], ['bash', 'build/run.sh', 'x'],
                          ['bash', './build/run.sh', 'x'], ['sh', 'build/run.sh', 'x'], ['sh', './build/run.sh', 'x']])
        self.assertEqual(permissions.variants(['npm', 'publish']), [['npm', 'publish']])
        self.assertIn(['/w/build/run.sh'], permissions.variants(['build/run.sh'], '/w'))
        self.assertIn(['sh', '/w/build/run.sh'], permissions.variants(['build/run.sh'], '/w'))


class ClaudeTest(unittest.TestCase):
    def test_no_level_starts_as_before(self):
        a = launch.argv(role(level=None), block_push=False, which=lambda n: None, cwd='/w')
        self.assertEqual(settings(a)['permissions'], {'allow': ['mcp__assieme']})
        self.assertEqual(a[0], 'claude')  # no push block env either: unchanged
        self.assertNotIn('--permission-mode', a)

    def test_safe_edits_its_worktree_and_blocks_the_floor(self):
        a = launch.argv(role(), block_push=False, which=lambda n: None, cwd='/w/b1')
        p = settings(a)['permissions']
        self.assertIn('Edit(//w/b1/**)', p['allow'])
        self.assertIn('Bash(git commit:*)', p['allow'])
        self.assertIn('Bash(git push:*)', p['deny'])
        self.assertIn('Bash(npm install:*)', p['deny'])
        self.assertIn('Read(~/.ssh/**)', p['deny'])
        # Full paths of git are denied too, and shell commands that name a secret path.
        self.assertIn('Bash(/usr/bin/git push:*)', p['deny'])
        self.assertIn('Bash(* ~/.ssh*)', p['deny'])
        self.assertIn('Bash(* $HOME/.ssh*)', p['deny'])
        self.assertIn(f'Bash(* {os.path.expanduser("~")}/.ssh*)', p['deny'])
        # The push block is on for every role with a level, whatever the practice switch said.
        self.assertEqual(a[0], '/usr/bin/env')
        self.assertIn('GIT_CONFIG_KEY_0=url.assieme-push-blocked://.pushInsteadOf', a)
        # Anything not listed asks the person, whatever their own default mode is.
        self.assertEqual(a[a.index('--permission-mode') + 1], 'default')

    def test_owned_files_only(self):
        p = settings(launch.argv(role(owns=['payments/retry.py', './a.py']), which=lambda n: None, cwd='/w'))['permissions']
        self.assertIn('Edit(//w/payments/retry.py)', p['allow'])
        self.assertIn('Edit(//w/a.py)', p['allow'])
        self.assertNotIn('Edit(//w/**)', p['allow'])

    def test_tests_allowed_from_the_worktree(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        open(os.path.join(d, 'uv.lock'), 'w').close()
        p = settings(launch.argv(role(), which=lambda n: None, cwd=d))['permissions']
        self.assertIn('Bash(uv run pytest -q:*)', p['allow'])

    def test_read_only_and_trusted(self):
        a = launch.argv(role(level='read-only'), which=lambda n: None, cwd='/w')
        self.assertIn('--disallowedTools', a)
        self.assertIn('Bash(git commit:*)', settings(a)['permissions']['deny'])
        p = settings(launch.argv(role(level='trusted'), which=lambda n: None, cwd='/w'))['permissions']
        self.assertIn('Bash(npm install:*)', p['allow'])
        self.assertNotIn('Bash(npm install:*)', p['deny'])
        self.assertIn('Bash(git push:*)', p['deny'])

    def test_rules_match_the_program_run(self):
        p = settings(launch.argv(role(rules=[BLOCK_RUN, ALLOW_LINT]), which=lambda n: None, cwd='/w'))['permissions']
        for d in ('Bash(build/cloud/run.sh:*)', 'Bash(./build/cloud/run.sh:*)', 'Bash(bash build/cloud/run.sh:*)',
                  'Bash(/w/build/cloud/run.sh:*)'):
            self.assertIn(d, p['deny'])
        self.assertIn('Bash(make lint:*)', p['allow'])


class CodexTest(unittest.TestCase):
    def test_sandbox_per_level(self):
        self.assertEqual(permissions.codex_args(role('codex', level=None)), [])
        self.assertEqual(permissions.codex_args(role('codex')), ['--sandbox', 'workspace-write'])
        self.assertEqual(permissions.codex_args(role('codex', level='read-only')), ['--sandbox', 'read-only'])
        self.assertIn('sandbox_workspace_write.network_access=true', permissions.codex_args(role('codex', level='trusted')))
        a = launch.argv(role('codex', level='read-only', checkout='read-only'), which=lambda n: None)
        self.assertEqual(a.count('--sandbox'), 1)

    def test_rules_file_written_and_removed(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = permissions.write_codex_rules(d, [role('codex', rules=[BLOCK_RUN])])
        text = open(path).read()
        self.assertIn('prefix_rule(pattern=["git", "push"], decision="forbidden"', text)
        self.assertIn('prefix_rule(pattern=["./build/cloud/run.sh"], decision="forbidden"', text)
        self.assertIn('prefix_rule(pattern=["git", "commit"], decision="allow"', text)
        permissions.write_codex_rules(d, [role('codex', level=None)])
        self.assertFalse(os.path.exists(path))

    def test_agents_sharing_a_folder_share_one_merged_file(self):
        # Codex loads every .rules file in the folder, so one file holds every block and only the allows all share.
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        a = role('codex', rules=[BLOCK_RUN, ALLOW_LINT])
        b = Role('reviewer', 'codex', 'r.md', 'brief', None, 'read-only', level='read-only')
        path = permissions.write_codex_rules(d, [a, b])
        text = open(path).read()
        self.assertEqual(os.listdir(os.path.dirname(path)), ['assieme.rules'])
        self.assertIn('pattern=["build/cloud/run.sh"], decision="forbidden"', text)
        self.assertIn('pattern=["git", "commit"], decision="forbidden"', text)  # the reviewer may not commit
        self.assertNotIn('decision="allow"', text)  # neither its commit nor make lint widens the reviewer
        self.assertEqual(open(permissions.write_codex_rules(d, [b, a])).read().count('forbidden'), text.count('forbidden'))
        permissions.write_codex_rules(d, [a, role('codex', level=None)])  # a role without a level gets no allows
        self.assertNotIn('decision="allow"', open(path).read())

    def test_team_writes_rules_for_every_codex_agent_in_the_folder(self):
        tm = preset.Team('shop', '/p', roles=[role('codex', rules=[BLOCK_RUN]), Role('r', 'claude', 'r.md', 'b', None),
                                             Role('c2', 'codex', 'c.md', 'b', None, level='safe')])
        with unittest.mock.patch('assieme.permissions.write_codex_rules') as w, \
                unittest.mock.patch('assieme.worktree.exclude'), \
                unittest.mock.patch('assieme.worktree.folder', side_effect=lambda t, r: '/p/w' if r.name != 'c2' else '/p/c2'):
            team._codex_rules(tm, tm.roles[0], '/p/w')
        w.assert_called_once_with('/p/w', [tm.roles[0]])

    @unittest.skipUnless(shutil.which('codex'), 'Codex is not installed')
    def test_codex_reads_the_rules(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = permissions.write_codex_rules(d, [role('codex', rules=[BLOCK_RUN, ALLOW_LINT])])

        def decision(*cmd):
            r = subprocess.run(['codex', 'execpolicy', 'check', '--rules', path, '--', *cmd], capture_output=True,
                               text=True, timeout=30)
            return json.loads(r.stdout).get('decision')
        self.assertEqual(decision('git', 'push', 'origin', 'main'), 'forbidden')
        self.assertEqual(decision('/usr/bin/git', 'push'), 'forbidden')
        self.assertEqual(decision('bash', 'build/cloud/run.sh', '--fast'), 'forbidden')
        self.assertEqual(decision('make', 'lint'), 'allow')
        self.assertIsNone(decision('grep', 'run.sh', 'notes.md'))  # mentioning it is not running it


class SavedTeamTest(unittest.TestCase):
    def test_fields_survive_save_and_load(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        r = {'name': 'builder-1', 'agent': 'claude', 'job': 'x', 'checkout': 'branch', 'parent': None, 'brief': 'b',
             'kind': 'builder', 'owns': ['payments/retry.py'], 'level': 'trusted', 'rules': [BLOCK_RUN]}
        folder = newteam.save(d, [r], {}, 'shop', d)
        [got] = preset.load(folder).roles
        self.assertEqual((got.kind, got.owns, got.level, got.rules), ('builder', ['payments/retry.py'], 'trusted', [BLOCK_RUN]))

    def test_validate(self):
        base = {'name': 'a', 'agent': 'claude', 'brief': 'b'}
        newteam.validate([base | {'level': 'safe', 'rules': [ALLOW_LINT]}])
        for bad, msg in (({'level': 'root'}, 'Unknown permission level'),
                         ({'rules': [{'decision': 'allow', 'program': ['git', 'push']}]}, 'Pushing stays blocked'),
                         ({'owns': ['../x']}, 'not a path inside'),
                         ({'kind': 'boss'}, 'Unknown role')):
            with self.assertRaisesRegex(newteam.NewTeamError, msg):
                newteam.validate([base | bad])

    def test_owned_files_reach_the_brief(self):
        sent = []

        class Rt:
            def send(self, t, text):
                sent.append(text)

            def label(self, t, labels):
                pass
        tm = preset.Team('shop', '/p', roles=[role(owns=['a.py', 'b.py'])])
        with unittest.mock.patch('assieme.brief.text', return_value=''):
            team._brief(Rt(), 't', tm.roles[0], 'task', tm)
        self.assertIn('You own these files: a.py, b.py.', sent[0])

    def test_exclude_line(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        subprocess.run(['git', 'init', '-q', d], check=True)
        worktree.exclude(d, permissions.RULES_FILE)
        worktree.exclude(d, permissions.RULES_FILE)
        lines = open(os.path.join(d, '.git', 'info', 'exclude')).read().splitlines()
        self.assertEqual(lines.count('.codex/rules/assieme.rules'), 1)


def fake(reply):
    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, json.dumps({'result': reply}), '')
    return run


class AskTest(unittest.TestCase):
    which = staticmethod(lambda c: '/bin/claude' if c == 'claude' else None)

    def test_rule_draft(self):
        got = newteam.rule_draft('/p', 'Don’t run the cloud launcher',
                                 run=fake('{"decision": "block", "program": ["build/cloud/run.sh"]}'), which=self.which)
        self.assertEqual(got, {'decision': 'block', 'program': ['build/cloud/run.sh'], 'words': 'Don’t run the cloud launcher'})

    def test_rule_draft_refuses(self):
        for reply, msg in (('{"unclear": "That isn\'t a program."}', 'isn.t a program'),
                           ('{"decision": "allow", "program": ["git", "push"]}', 'Pushing stays blocked'),
                           ('no json', 'blocks or allows')):
            with self.assertRaisesRegex(newteam.NewTeamError, msg):
                newteam.rule_draft('/p', 'words', run=fake(reply), which=self.which)
        with self.assertRaisesRegex(newteam.NewTeamError, 'Write the rule'):
            newteam.rule_draft('/p', '  ', run=fake(''), which=self.which)

    def test_improve_brief(self):
        seen = {}

        def run(argv, **kw):
            seen.update(kw)
            return subprocess.CompletedProcess(argv, 0, json.dumps({'result': '{"brief": "Better."}'}), '')
        got = newteam.improve_brief('/p', {'kind': 'builder', 'job': 'j', 'owns': ['a.py'], 'parent': 'planner',
                                           'brief': 'Old.'}, run=run, which=self.which)
        self.assertEqual(got, {'brief': 'Better.\n'})
        self.assertIn('Old.', seen['input'])
        self.assertIn('a.py', seen['input'])
        self.assertEqual(seen['cwd'], '/p')
        with self.assertRaisesRegex(newteam.NewTeamError, "wasn't a brief"):
            newteam.improve_brief('/p', {}, run=fake('{}'), which=self.which)


class TableTest(unittest.TestCase):
    def test_secrets_say_what_they_cover(self):
        s = permissions.table()['items']['secrets']
        self.assertIn('commands that name them', s['note'])
        self.assertIn('keychain', s['notes']['codex'])
        for kind in ('gemini', 'opencode', 'cursor'):
            self.assertIn('file tools', s['notes'][kind])


class LabelsTest(unittest.TestCase):
    def test_codex_menu(self):
        items = [(1, 'Yes, proceed (y)'), (2, "Yes, and don't ask again for this command (a)"),
                 (3, 'No, and tell Codex what to do differently (esc)')]
        self.assertEqual(prompts.permission_labels(items), {1: 'Allow once', 2: 'Always allow for this agent', 3: 'Block'})

    def test_claude_bash_menu(self):
        items = [(1, 'Yes'), (2, 'Yes, and don’t ask again for npm test com…'), (3, 'No')]
        self.assertEqual(prompts.permission_labels(items), {1: 'Allow once', 2: 'Always allow for this agent', 3: 'Block'})

    def test_not_a_permission_menu(self):
        self.assertEqual(prompts.permission_labels([(1, 'Tabs'), (2, 'Spaces')]), {})


if __name__ == '__main__':
    unittest.main()


class OpenCodeTest(unittest.TestCase):
    def test_safe_asks_by_default_allows_commits_blocks_push_installs_and_secrets(self):
        p = permissions.opencode(role('opencode'), '/w')
        self.assertEqual(p['bash']['*'], 'ask')
        self.assertEqual(p['bash']['git commit *'], 'allow')
        for cmd in ('git push *', '/usr/bin/git push *', 'npm install *', 'curl *', 'security *'):
            self.assertEqual(p['bash'][cmd], 'deny', cmd)
        self.assertEqual(p['edit'], 'allow')
        self.assertEqual(p['webfetch'], 'deny')
        self.assertEqual(p['read']['~/.ssh/**'], 'deny')
        self.assertEqual(p['external_directory'], 'ask')

    def test_denies_come_after_allows(self):  # OpenCode lets the last matching pattern win
        bash = list(permissions.opencode(role('opencode', rules=[BLOCK_RUN, ALLOW_LINT]), '/w')['bash'].items())
        last_allow = max(i for i, (_, v) in enumerate(bash) if v == 'allow')
        self.assertTrue(all(v == 'deny' for _, v in bash[last_allow + 1:]))
        self.assertIn(('build/cloud/run.sh *', 'deny'), bash)
        self.assertIn(('make lint *', 'allow'), bash)

    def test_read_only_never_edits_and_trusted_goes_online(self):
        self.assertEqual(permissions.opencode(role('opencode', 'read-only'), '/w')['edit'], 'deny')
        t = permissions.opencode(role('opencode', 'trusted'), '/w')
        self.assertEqual(t['webfetch'], 'allow')
        self.assertEqual(t['bash']['npm install *'], 'allow')
        self.assertEqual(t['bash']['git push *'], 'deny')

    def test_no_level_no_rules(self):
        self.assertIsNone(permissions.opencode(role('opencode', None), '/w'))

    def test_the_agent_starts_with_them(self):
        argv, env = launch.BUILDERS['opencode'](role('opencode'), tempfile.mkdtemp())
        self.assertEqual(json.loads(env['OPENCODE_CONFIG_CONTENT'])['permission']['bash']['git push *'], 'deny')


class GeminiTest(unittest.TestCase):
    def test_policy_is_toml_with_denies_above_allows(self):
        import tomllib
        rules = tomllib.loads(permissions.gemini_policy(role('gemini', rules=[BLOCK_RUN]), '/w'))['rule']
        by = {(r.get('commandPrefix'), r.get('toolName')): r for r in rules}
        push = by[('git push', 'run_shell_command')]
        commit = by[('git commit', 'run_shell_command')]
        self.assertEqual((push['decision'], commit['decision']), ('deny', 'allow'))
        self.assertGreater(push['priority'], commit['priority'])
        self.assertEqual(by[('build/cloud/run.sh', 'run_shell_command')]['decision'], 'deny')
        self.assertTrue(any(r.get('toolName') == 'web_fetch' and r['decision'] == 'deny' for r in rules))
        home = os.path.expanduser('~')
        self.assertTrue(any(r['decision'] == 'deny' and '.ssh' in r.get('argsPattern', '') and home in r['argsPattern']
                            for r in rules))

    def test_trusted_may_go_online_but_never_push(self):
        import tomllib
        rules = tomllib.loads(permissions.gemini_policy(role('gemini', 'trusted'), '/w'))['rule']
        self.assertFalse(any(r.get('toolName') == 'web_fetch' and r['decision'] == 'deny' for r in rules))
        self.assertTrue(any(r.get('commandPrefix') == 'git push' and r['decision'] == 'deny' for r in rules))

    def test_the_agent_starts_with_its_policy_file_kept_out_of_git(self):
        d = tempfile.mkdtemp()
        subprocess.run(['git', 'init', '-q', d], check=True)
        argv, _ = launch.BUILDERS['gemini'](role('gemini'), d)
        path = argv[argv.index('--policy') + 1]
        self.assertTrue(path.startswith(d) and os.path.exists(path))
        self.assertEqual(subprocess.run(['git', '-C', d, 'status', '--porcelain', '--untracked-files=all'],
                                        capture_output=True, text=True).stdout.count('assieme.toml'), 0)

    def test_no_level_no_policy(self):
        self.assertIsNone(permissions.gemini_policy(role('gemini', None), '/w'))
        argv, _ = launch.BUILDERS['gemini'](role('gemini', None), tempfile.mkdtemp())
        self.assertNotIn('--policy', argv)


class CursorTest(unittest.TestCase):
    def test_programs_and_secret_paths(self):
        p = permissions.cursor(role('cursor', rules=[BLOCK_RUN]), '/w')
        for rule in ('Shell(git:push*)', 'Shell(curl)', 'Shell(npm:install*)', 'Shell(build/cloud/run.sh)', 'WebFetch(*)'):
            self.assertIn(rule, p['deny'])
        self.assertIn('Shell(git:commit*)', p['allow'])
        self.assertIn(f"Read({os.path.expanduser('~')}/.ssh/**)", p['deny'])

    def test_the_agent_starts_with_them_and_keeps_the_projects_own(self):
        d = tempfile.mkdtemp()
        subprocess.run(['git', 'init', '-q', d], check=True)
        os.makedirs(os.path.join(d, '.cursor'))
        with open(os.path.join(d, '.cursor', 'cli.json'), 'w') as f:
            json.dump({'permissions': {'deny': ['Shell(rm)'], 'allow': ['Shell(ls)']}, 'other': 1}, f)
        launch.BUILDERS['cursor'](role('cursor'), d)
        with open(os.path.join(d, '.cursor', 'cli.json')) as f:
            got = json.load(f)
        self.assertIn('Shell(rm)', got['permissions']['deny'])
        self.assertIn('Shell(git:push*)', got['permissions']['deny'])
        self.assertIn('Shell(ls)', got['permissions']['allow'])
        self.assertEqual(got['other'], 1)
        launch.BUILDERS['cursor'](role('cursor'), d)  # again: nothing doubled
        with open(os.path.join(d, '.cursor', 'cli.json')) as f:
            self.assertEqual(json.load(f)['permissions']['deny'].count('Shell(git:push*)'), 1)
