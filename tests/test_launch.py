"""Agent command lines for Claude Code and Codex, with the status hook."""
import json
import tomllib
import unittest

from assieme import launch
from assieme.preset import Role

EVENTS = ['PermissionRequest', 'SessionStart', 'Stop', 'UserPromptSubmit']


def role(agent, model=None, checkout='branch', effort=None):
    return Role('builder-1', agent, 'b.md', 'SECRET BRIEF TEXT', model, checkout, effort=effort)


class LaunchTest(unittest.TestCase):
    def test_claude_hooks_map_events(self):
        a = launch.argv(role('claude'))
        self.assertEqual(a[0], 'claude')
        s = json.loads(a[a.index('--settings') + 1])
        self.assertEqual(sorted(s['hooks']), EVENTS)
        self.assertTrue(s['hooks']['Stop'][0]['hooks'][0]['command'].endswith('-m assieme.hook idle'))
        self.assertTrue(s['hooks']['UserPromptSubmit'][0]['hooks'][0]['command'].endswith('-m assieme.hook working'))
        self.assertTrue(s['hooks']['PermissionRequest'][0]['hooks'][0]['command'].endswith('-m assieme.hook blocked'))
        self.assertNotIn('Notification', s['hooks'])

    def test_codex_hooks_are_valid_toml(self):
        a = launch.argv(role('codex'))
        self.assertEqual(a[0], 'codex')
        values = [a[i + 1] for i, x in enumerate(a) if x == '-c' and a[i + 1].startswith('hooks.')]
        events = sorted(next(iter(tomllib.loads(v)['hooks'])) for v in values)
        self.assertEqual(events, EVENTS)
        stop = next(tomllib.loads(v) for v in values if 'Stop' in tomllib.loads(v)['hooks'])
        self.assertTrue(stop['hooks']['Stop'][0]['hooks'][0]['command'].endswith('-m assieme.hook idle'))

    def test_codex_skips_update_prompt(self):
        # A real run stopped at Codex's "Update available" screen; agents must start without it.
        a = launch.argv(role('codex'))
        values = [a[i + 1] for i, x in enumerate(a) if x == '-c']
        self.assertIn('check_for_update_on_startup=false', values)

    def test_hook_ignores_the_worktree_on_import_path(self):
        # python -m puts the current folder (the agent's worktree) first on the import path; -P stops that.
        self.assertIn(' -P -m assieme.hook idle', launch.hook_command('idle'))

    def test_read_only(self):
        a = launch.argv(role('claude', checkout='read-only'))
        i = a.index('--disallowedTools')
        self.assertEqual(a[i + 1:i + 4], ['Edit', 'Write', 'NotebookEdit'])
        c = launch.argv(role('codex', checkout='read-only'))
        self.assertEqual(c[c.index('--sandbox') + 1], 'read-only')
        self.assertNotIn('--disallowedTools', launch.argv(role('claude')))
        self.assertNotIn('--sandbox', launch.argv(role('codex')))

    def test_model_flag(self):
        a = launch.argv(role('claude', model='opus'))
        self.assertEqual(a[a.index('--model') + 1], 'opus')
        c = launch.argv(role('codex', model='gpt-x'))
        self.assertEqual(c[c.index('-m') + 1], 'gpt-x')
        self.assertNotIn('--model', launch.argv(role('claude')))
        self.assertNotIn('-m', launch.argv(role('codex')))

    def test_brief_not_on_command_line(self):
        for agent in ('claude', 'codex'):
            self.assertFalse(any('SECRET BRIEF' in x for x in launch.argv(role(agent))))

    def test_missing_clis(self):
        roles = [role('claude'), role('codex'), role('codex')]
        self.assertEqual(launch.missing_clis(roles, which=lambda c: None if c == 'codex' else '/bin/' + c), ['codex'])


class ModelEffortTest(unittest.TestCase):
    def test_claude_model_and_effort(self):
        a = launch.argv(role('claude', model='opus', effort='high'))
        self.assertEqual(a[a.index('--model') + 1], 'opus')
        self.assertEqual(a[a.index('--effort') + 1], 'high')

    def test_codex_model_and_effort(self):
        a = launch.argv(role('codex', model='gpt-6-luna', effort='xhigh'))
        self.assertEqual(a[a.index('-m') + 1], 'gpt-6-luna')
        values = [a[i + 1] for i, x in enumerate(a) if x == '-c']
        self.assertEqual(tomllib.loads(next(v for v in values if v.startswith('model_reasoning_effort'))),
                         {'model_reasoning_effort': 'xhigh'})

    def test_defaults_add_nothing(self):
        self.assertNotIn('--effort', launch.argv(role('claude')))
        self.assertFalse(any(x.startswith('model_reasoning_effort') for x in launch.argv(role('codex'))))
        self.assertNotIn('-m', launch.argv(role('codex')))

    def test_check_model_effort(self):
        ok = launch.check_model_effort
        self.assertIsNone(ok('claude', 'opus', 'max'))
        self.assertIsNone(ok('codex', 'gpt-6.1-sol', 'ultra'))
        self.assertIsNone(ok('claude', None, None))
        self.assertEqual(ok('claude', None, 'ultra'), 'Claude Code has no thinking effort "ultra".')
        self.assertEqual(ok('codex', 'gpt"; rm', None), 'Model names use letters, digits, dots and dashes.')
        self.assertEqual(ok('claude', 'a b', None), 'Model names use letters, digits, dots and dashes.')


class McpTest(unittest.TestCase):
    def test_claude_gets_the_server_and_needs_no_permission_for_it(self):
        a = launch.argv(role('claude'))
        self.assertEqual(json.loads(a[a.index('--mcp-config') + 1])['mcpServers']['assieme']['args'], ['-P', '-m', 'assieme', 'mcp'])
        self.assertEqual(json.loads(a[a.index('--settings') + 1])['permissions']['allow'], ['mcp__assieme'])

    def test_codex_gets_the_server_with_the_variables_that_name_the_agent(self):
        a = launch.argv(role('codex'))
        conf = tomllib.loads('\n'.join(a[i + 1] for i, x in enumerate(a) if x == '-c' and a[i + 1].startswith('mcp_servers.')))
        s = conf['mcp_servers']['assieme']
        self.assertEqual(s['args'], ['-P', '-m', 'assieme', 'mcp'])
        self.assertIn('HC_TERMINAL_ID', s['env_vars'])
