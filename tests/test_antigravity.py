"""Antigravity (`agy`): its hooks, its per-session settings and the Assieme plugin that carries the team tools."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from assieme import antigravity, launch, permissions, setup
from assieme.preset import Role


def role(level='safe', rules=(), checkout='branch', model=None):
    return Role('builder-1', 'antigravity', 'b.md', 'brief', model, checkout, level=level, rules=list(rules))


def repo():
    d = tempfile.mkdtemp()
    subprocess.run(['git', 'init', '-q', d], check=True)
    return d


def settings_of(argv):
    return json.loads(argv[argv.index('--settings') + 1])


class KindTest(unittest.TestCase):
    def test_it_is_a_kind_run_as_agy(self):
        self.assertEqual((launch.CLIS['antigravity'], launch.NAMES['antigravity']), ('agy', 'Antigravity'))
        self.assertIn('antigravity', setup.AGENTS)
        self.assertIn('antigravity', launch.ASKS_TRUST)


class LaunchTest(unittest.TestCase):
    def test_hooks_go_in_the_agents_own_folder_and_stay_out_of_git(self):
        d = repo()
        argv, env = launch.BUILDERS['antigravity'](role(), d)
        with open(os.path.join(d, '.agents', 'hooks.json')) as f:
            hooks = json.load(f)['assieme']
        self.assertIn('assieme.hook idle', hooks['SessionStart'][0]['command'])
        self.assertIn('assieme.hook working', hooks['PreInvocation'][0]['command'])
        self.assertIn('assieme.hook idle', hooks['Stop'][0]['command'])
        status = subprocess.run(['git', '-C', d, 'status', '--porcelain', '--untracked-files=all'], capture_output=True, text=True).stdout
        self.assertNotIn('hooks.json', status)
        self.assertEqual(argv[0], 'agy')

    def test_level_and_rules_ride_in_session_settings(self):
        d = repo()
        argv, _ = launch.BUILDERS['antigravity'](role(rules=[{'decision': 'block', 'program': ['make', 'deploy'], 'words': ''}]), d)
        s = settings_of(argv)
        self.assertIn('command(git push)', s['permissions']['deny'])
        self.assertIn('command(make deploy)', s['permissions']['deny'])
        self.assertIn('command(git commit)', s['permissions']['allow'])
        self.assertEqual(s['toolPermission'], 'request-review')

    def test_read_only_is_plan_mode_and_a_trusted_folder_is_said_in_settings(self):
        d = repo()
        argv, _ = launch.BUILDERS['antigravity'](role('read-only', checkout='read-only'), d, trusted=True)
        self.assertEqual(argv[argv.index('--mode') + 1], 'plan')
        self.assertEqual(settings_of(argv)['trustedWorkspaces'], [d])

    def test_model_flag(self):
        argv, _ = launch.BUILDERS['antigravity'](role(level=None, model='gemini-4'), repo())
        self.assertEqual(argv[argv.index('--model') + 1], 'gemini-4')


class PermissionsTest(unittest.TestCase):
    def test_safe(self):
        p = permissions.antigravity(role(), '/w')
        home = os.path.expanduser('~')
        for rule in ('command(git push)', 'command(/usr/bin/git push)', 'command(npm install)', 'command(curl)',
                     'read_url(*)', f'read_file({home}/.ssh)'):
            self.assertIn(rule, p['deny'], rule)
        self.assertIn('command(git commit)', p['allow'])

    def test_trusted_goes_online_but_never_pushes(self):
        p = permissions.antigravity(role('trusted'), '/w')
        self.assertNotIn('read_url(*)', p['deny'])
        self.assertIn('command(git push)', p['deny'])

    def test_no_level(self):
        self.assertIsNone(permissions.antigravity(role(None), '/w'))


class PluginTest(unittest.TestCase):
    def test_the_plugin_folder_points_at_this_python(self):
        d = antigravity.build_plugin(tempfile.mkdtemp())
        with open(os.path.join(d, 'plugin.json')) as f:
            self.assertEqual(json.load(f)['name'], 'assieme')
        with open(os.path.join(d, 'mcp_config.json')) as f:
            server = json.load(f)['mcpServers']['assieme']
        self.assertEqual(server['command'], sys.executable)
        self.assertEqual(server['args'], ['-P', '-m', 'assieme', 'mcp'])

    def listing(self, entries, command=sys.executable):
        home = tempfile.mkdtemp()
        for e in entries:
            os.makedirs(e['path'].format(home=home), exist_ok=True)
            with open(os.path.join(e['path'].format(home=home), 'mcp_config.json'), 'w') as f:
                json.dump({'mcpServers': {'assieme': {'command': command, 'args': []}}}, f)
        out = json.dumps([e | {'path': e['path'].format(home=home)} for e in entries])
        return lambda *a, **k: subprocess.CompletedProcess(a[0], 0, out, '')

    def test_connected_when_installed_enabled_and_current(self):
        e = {'name': 'assieme', 'enabled': True, 'path': '{home}/p'}
        self.assertTrue(antigravity.connected('/opt/homebrew/bin/agy', run=self.listing([e])))
        self.assertFalse(antigravity.connected('/opt/homebrew/bin/agy', run=self.listing([e | {'enabled': False}])))
        self.assertFalse(antigravity.connected('/opt/homebrew/bin/agy', run=self.listing([])))
        self.assertFalse(antigravity.connected('/opt/homebrew/bin/agy', run=self.listing([e], command='/old/python')))

    def test_status_says_whether_it_is_connected(self):
        with mock.patch.object(setup, 'agent_path', side_effect=lambda n: '/opt/homebrew/bin/agy' if n == 'agy' else None), \
             mock.patch.object(antigravity, 'connected', return_value=False):
            got = setup.status(run=lambda *a, **k: subprocess.CompletedProcess(a[0], 0, '', ''))['agents']['antigravity']
        self.assertEqual(got, {'installed': True, 'signed_in': True, 'connected': False})

    def test_connect_runs_the_shown_command_in_a_setup_terminal(self):
        started = []

        class Rt:
            def list(self, labels):
                return []

            def start(self, argv, cwd, labels, name=None):
                started.append((argv, labels))
                return 't9'
        with mock.patch.object(setup, 'agent_path', return_value='/opt/homebrew/bin/agy'), \
             mock.patch.object(antigravity, 'build_plugin', return_value='/s/agy-plugin'):
            self.assertEqual(antigravity.connect(Rt()), {'address': 't9'})
        argv, labels = started[0]
        self.assertIn('/opt/homebrew/bin/agy plugin install /s/agy-plugin', argv[-1])
        self.assertEqual(labels, {setup.LABEL: 'antigravity-connect'})
