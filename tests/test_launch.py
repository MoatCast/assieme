"""Agent command lines for Claude Code and Codex, with the status hook."""
import json
import os
import subprocess
import tempfile
import tomllib
import unittest

from assieme import launch
from assieme.preset import Role

EVENTS = ['PermissionRequest', 'SessionStart', 'Stop', 'UserPromptSubmit']


def role(agent, model=None, checkout='branch', effort=None):
    return Role('builder-1', agent, 'b.md', 'SECRET BRIEF TEXT', model, checkout, effort=effort)


def plain_argv(r):
    return launch.argv(r, block_push=False, which=lambda n: None)


class LaunchTest(unittest.TestCase):
    def test_claude_hooks_map_events(self):
        a = plain_argv(role('claude'))
        self.assertEqual(a[0], 'claude')
        s = json.loads(a[a.index('--settings') + 1])
        self.assertEqual(sorted(s['hooks']), EVENTS)
        self.assertTrue(s['hooks']['Stop'][0]['hooks'][0]['command'].endswith('-m assieme.hook idle'))
        self.assertTrue(s['hooks']['UserPromptSubmit'][0]['hooks'][0]['command'].endswith('-m assieme.hook working'))
        self.assertTrue(s['hooks']['PermissionRequest'][0]['hooks'][0]['command'].endswith('-m assieme.hook blocked'))
        self.assertNotIn('Notification', s['hooks'])

    def test_codex_hooks_are_valid_toml(self):
        a = plain_argv(role('codex'))
        self.assertEqual(a[0], 'codex')
        values = [a[i + 1] for i, x in enumerate(a) if x == '-c' and a[i + 1].startswith('hooks.')]
        events = sorted(next(iter(tomllib.loads(v)['hooks'])) for v in values)
        self.assertEqual(events, EVENTS)
        stop = next(tomllib.loads(v) for v in values if 'Stop' in tomllib.loads(v)['hooks'])
        self.assertTrue(stop['hooks']['Stop'][0]['hooks'][0]['command'].endswith('-m assieme.hook idle'))

    def test_codex_skips_update_prompt(self):
        # A real run stopped at Codex's "Update available" screen; agents must start without it.
        a = plain_argv(role('codex'))
        values = [a[i + 1] for i, x in enumerate(a) if x == '-c']
        self.assertIn('check_for_update_on_startup=false', values)

    def test_hook_ignores_the_worktree_on_import_path(self):
        # python -m puts the current folder (the agent's worktree) first on the import path; -P stops that.
        self.assertIn(' -P -m assieme.hook idle', launch.hook_command('idle'))

    def test_read_only(self):
        a = plain_argv(role('claude', checkout='read-only'))
        i = a.index('--disallowedTools')
        self.assertEqual(a[i + 1:i + 4], ['Edit', 'Write', 'NotebookEdit'])
        c = plain_argv(role('codex', checkout='read-only'))
        self.assertEqual(c[c.index('--sandbox') + 1], 'read-only')
        self.assertNotIn('--disallowedTools', plain_argv(role('claude')))
        self.assertNotIn('--sandbox', plain_argv(role('codex')))

    def test_model_flag(self):
        a = plain_argv(role('claude', model='opus'))
        self.assertEqual(a[a.index('--model') + 1], 'opus')
        c = plain_argv(role('codex', model='gpt-x'))
        self.assertEqual(c[c.index('-m') + 1], 'gpt-x')
        self.assertNotIn('--model', plain_argv(role('claude')))
        self.assertNotIn('-m', plain_argv(role('codex')))

    def test_brief_not_on_command_line(self):
        for agent in ('claude', 'codex'):
            self.assertFalse(any('SECRET BRIEF' in x for x in plain_argv(role(agent))))

    def test_missing_clis(self):
        roles = [role('claude'), role('codex'), role('codex')]
        self.assertEqual(launch.missing_clis(roles, which=lambda c: None if c == 'codex' else '/bin/' + c), ['codex'])


class ModelEffortTest(unittest.TestCase):
    def test_claude_model_and_effort(self):
        a = plain_argv(role('claude', model='opus', effort='high'))
        self.assertEqual(a[a.index('--model') + 1], 'opus')
        self.assertEqual(a[a.index('--effort') + 1], 'high')

    def test_codex_model_and_effort(self):
        a = plain_argv(role('codex', model='gpt-6-luna', effort='xhigh'))
        self.assertEqual(a[a.index('-m') + 1], 'gpt-6-luna')
        values = [a[i + 1] for i, x in enumerate(a) if x == '-c']
        self.assertEqual(tomllib.loads(next(v for v in values if v.startswith('model_reasoning_effort'))),
                         {'model_reasoning_effort': 'xhigh'})

    def test_defaults_add_nothing(self):
        self.assertNotIn('--effort', plain_argv(role('claude')))
        self.assertFalse(any(x.startswith('model_reasoning_effort') for x in plain_argv(role('codex'))))
        self.assertNotIn('-m', plain_argv(role('codex')))

    def test_check_model_effort(self):
        ok = launch.check_model_effort
        self.assertIsNone(ok('claude', 'opus', 'max'))
        self.assertIsNone(ok('codex', 'gpt-6.1-sol', 'ultra'))
        self.assertIsNone(ok('claude', None, None))
        self.assertEqual(ok('claude', None, 'ultra'), 'Claude Code has no thinking effort "ultra".')
        self.assertEqual(ok('codex', 'gpt"; rm', None), 'Model names use letters, digits, dots and dashes.')
        self.assertEqual(ok('claude', 'a b', None), 'Model names use letters, digits, dots and dashes.')


class BlockPushTest(unittest.TestCase):
    def test_agents_cannot_push_by_default(self):
        for agent in ('claude', 'codex'):
            a = launch.argv(role(agent), which=lambda n: f'/x/{n}')
            self.assertEqual(a[:1], ['/usr/bin/env'])
            self.assertIn('GIT_CONFIG_KEY_0=url.assieme-push-blocked://.pushInsteadOf', a)
            self.assertEqual(a[a.index(f'/x/{agent}') - 1].split('=')[0], 'GIT_CONFIG_VALUE_0')

    def test_turning_safe_by_default_off_removes_the_block(self):
        a = launch.argv(role('claude'), block_push=False, which=lambda n: f'/x/{n}')
        self.assertEqual(a[0], '/x/claude')

    def test_a_missing_agent_keeps_its_plain_name(self):
        self.assertEqual(launch.argv(role('codex'), block_push=False, which=lambda n: None)[0], 'codex')

    def test_known_limit_a_remote_with_its_own_push_address_still_pushes(self):
        # Documented in README.md and SECURITY.md: git doesn't apply pushInsteadOf to an explicit remote.<name>.pushurl.
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')
            git = lambda *a, cwd=root, e=env: subprocess.run(['git', *a], cwd=cwd, env=e, capture_output=True, text=True)
            git('init', '-q', '--bare', 'origin.git')
            git('clone', '-q', 'origin.git', 'work')
            work = os.path.join(root, 'work')
            git('config', 'remote.origin.pushurl', os.path.join(root, 'origin.git'), cwd=work)
            git('-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-q', '--allow-empty', '-m', 'one', cwd=work)
            self.assertEqual(git('push', '-q', 'origin', 'HEAD', cwd=work, e=env | launch.BLOCK_PUSH).returncode, 0)

    def test_the_setting_really_stops_a_push_and_keeps_fetch(self):
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')
            git = lambda *a, cwd=root, e=env: subprocess.run(['git', *a], cwd=cwd, env=e, capture_output=True, text=True)
            git('init', '-q', '--bare', 'origin.git')
            git('clone', '-q', 'origin.git', 'work')
            work = os.path.join(root, 'work')
            git('-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-q', '--allow-empty', '-m', 'one', cwd=work)
            blocked = env | launch.BLOCK_PUSH
            push = git('push', '-q', 'origin', 'HEAD', cwd=work, e=blocked)
            self.assertNotEqual(push.returncode, 0)
            self.assertEqual(git('rev-parse', '--verify', '-q', 'HEAD', cwd=os.path.join(root, 'origin.git')).returncode, 1)
            self.assertEqual(git('fetch', '-q', 'origin', cwd=work, e=blocked).returncode, 0)
            self.assertEqual(git('push', '-q', 'origin', 'HEAD', cwd=work).returncode, 0)  # without the setting it works


class McpTest(unittest.TestCase):
    def test_claude_gets_the_server_and_needs_no_permission_for_it(self):
        a = plain_argv(role('claude'))
        self.assertEqual(json.loads(a[a.index('--mcp-config') + 1])['mcpServers']['assieme']['args'], ['-P', '-m', 'assieme', 'mcp'])
        self.assertEqual(json.loads(a[a.index('--settings') + 1])['permissions']['allow'], ['mcp__assieme'])

    def test_codex_gets_the_server_with_the_variables_that_name_the_agent(self):
        a = plain_argv(role('codex'))
        conf = tomllib.loads('\n'.join(a[i + 1] for i, x in enumerate(a) if x == '-c' and a[i + 1].startswith('mcp_servers.')))
        s = conf['mcp_servers']['assieme']
        self.assertEqual(s['args'], ['-P', '-m', 'assieme', 'mcp'])
        self.assertIn('HC_TERMINAL_ID', s['env_vars'])
