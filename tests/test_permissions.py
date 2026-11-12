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
        path = permissions.write_codex_rules(d, role('codex', rules=[BLOCK_RUN]))
        text = open(path).read()
        self.assertIn('prefix_rule(pattern=["git", "push"], decision="forbidden"', text)
        self.assertIn('prefix_rule(pattern=["./build/cloud/run.sh"], decision="forbidden"', text)
        self.assertIn('prefix_rule(pattern=["git", "commit"], decision="allow"', text)
        permissions.write_codex_rules(d, role('codex', level=None))
        self.assertFalse(os.path.exists(path))

    @unittest.skipUnless(shutil.which('codex'), 'Codex is not installed')
    def test_codex_reads_the_rules(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = permissions.write_codex_rules(d, role('codex', rules=[BLOCK_RUN, ALLOW_LINT]))

        def decision(*cmd):
            r = subprocess.run(['codex', 'execpolicy', 'check', '--rules', path, '--', *cmd], capture_output=True,
                               text=True, timeout=30)
            return json.loads(r.stdout).get('decision')
        self.assertEqual(decision('git', 'push', 'origin', 'main'), 'forbidden')
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


class LabelsTest(unittest.TestCase):
    def test_codex_menu(self):
        items = [(1, 'Yes, proceed (y)'), (2, "Yes, and don't ask again for this command (a)"),
                 (3, 'No, and tell Codex what to do differently (esc)')]
        self.assertEqual(prompts.permission_labels(items), {1: 'Allow once', 2: 'Always allow', 3: 'Block'})

    def test_claude_bash_menu(self):
        items = [(1, 'Yes'), (2, 'Yes, and don’t ask again for npm test com…'), (3, 'No')]
        self.assertEqual(prompts.permission_labels(items), {1: 'Allow once', 2: 'Always allow', 3: 'Block'})

    def test_not_a_permission_menu(self):
        self.assertEqual(prompts.permission_labels([(1, 'Tabs'), (2, 'Spaces')]), {})


if __name__ == '__main__':
    unittest.main()
