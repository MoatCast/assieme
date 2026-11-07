"""Agent command lines for Claude Code and Codex, with the status hook."""
import json
import tomllib
import unittest

from assieme import launch
from assieme.preset import Role

EVENTS = ['PermissionRequest', 'SessionStart', 'Stop', 'UserPromptSubmit']


def role(agent, model=None, checkout='branch'):
    return Role('builder-1', agent, 'b.md', 'SECRET BRIEF TEXT', model, checkout)


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
        values = [a[i + 1] for i, x in enumerate(a) if x == '-c']
        events = sorted(next(iter(tomllib.loads(v)['hooks'])) for v in values)
        self.assertEqual(events, EVENTS)
        stop = next(tomllib.loads(v) for v in values if 'Stop' in tomllib.loads(v)['hooks'])
        self.assertTrue(stop['hooks']['Stop'][0]['hooks'][0]['command'].endswith('-m assieme.hook idle'))

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
