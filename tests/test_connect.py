"""Step 18a: Settings → Your assistants installs Assieme into each app as that app expects, and removes it."""
import json
import os
import tempfile
import unittest

from assieme import connect


class Done:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class FakeMac:
    """Claude Code, Codex and the Claude app as their command lines and files behave."""

    def __init__(self, home):
        self.home = home
        self.ran = []
        self.plugins = {'claude': set(), 'codex': set()}
        self.old = {'claude', 'codex'}  # `mcp add` entries from the old Connect button
        self.app_open = False
        self.fail = set()  # commands that fail, by their first two words
        os.makedirs(os.path.join(home, 'Library', 'Application Support', 'Claude'))

    def codex_config(self):
        path = os.path.join(self.home, '.codex', 'config.toml')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write('model = "x"\n' + ''.join(f'\n[plugins."{p}"]\nenabled = true\n' for p in sorted(self.plugins['codex'])))

    def run(self, args, **kw):
        self.ran.append(args)
        exe = os.path.basename(args[0])
        if exe == 'pgrep':
            return Done(0 if self.app_open else 1)
        if exe == 'open':
            return Done()
        if (exe, args[1]) in self.fail:
            return Done(1, stderr=f'{exe} broke')
        words = args[1:]
        if words[:2] == ['mcp', 'get']:
            return Done(0 if exe in self.old else 1)
        if words[:2] == ['mcp', 'remove']:
            self.old.discard(exe)
            return Done()
        if words == ['plugin', 'list', '--json']:
            return Done(stdout=json.dumps([{'id': p, 'enabled': True} for p in sorted(self.plugins['claude'])]))
        if words[:2] == ['plugin', 'install'] or words[:2] == ['plugin', 'add']:
            self.plugins[exe].add(words[2])
        if words[:2] in (['plugin', 'uninstall'], ['plugin', 'remove']):
            self.plugins[exe].discard(words[2])
        if exe == 'codex':
            self.codex_config()
        return Done()

    def install_extension(self, ext_id='local.mcpb.assieme.assieme'):
        path = os.path.join(self.home, 'Library', 'Application Support', 'Claude', 'extensions-installations.json')
        with open(path, 'w') as f:
            json.dump({'extensions': {ext_id: {'id': ext_id, 'manifest': {'name': 'assieme'}}}}, f)


class ConnectTest(unittest.TestCase):

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.mac = FakeMac(d.name)
        self.which = lambda c: f'/bin/{c}' if c in ('claude', 'codex') else None

    def go(self, fn, *args):
        return fn(*args, run=self.mac.run, which=self.which, home=self.mac.home, python='/opt/py', market=os.path.join(self.mac.home, 'market'))

    def apps(self, got):
        return {a['app']: a for a in got['apps']}

    def test_status_lists_the_three_apps(self):
        got = self.apps(self.go(connect.status))
        self.assertEqual(list(got), ['claude-code', 'claude-app', 'codex'])
        self.assertEqual((got['claude-code']['label'], got['claude-code']['kind']), ('Claude Code', 'plugin'))
        self.assertEqual((got['claude-app']['label'], got['claude-app']['kind']), ('The Claude app', 'connector'))
        self.assertEqual((got['codex']['label'], got['codex']['kind']), ('Codex in the ChatGPT app', 'plugin'))
        self.assertFalse(any(a['installed'] for a in got.values()))
        self.assertTrue(all(a['available'] for a in got.values()))

    def test_install_claude_code(self):  # Review Focus 3: the old entry goes first
        got = self.apps(self.go(connect.install, 'claude-code'))
        self.assertTrue(got['claude-code']['installed'])
        cmds = [a[1:] for a in self.mac.ran if os.path.basename(a[0]) == 'claude']
        self.assertIn(['mcp', 'remove', '--scope', 'user', 'assieme'], cmds)
        self.assertIn(['plugin', 'marketplace', 'add', os.path.join(self.mac.home, 'market')], cmds)
        self.assertIn(['plugin', 'install', 'assieme@assieme'], cmds)
        self.assertNotIn('claude', self.mac.old)
        self.assertTrue(os.path.exists(os.path.join(self.mac.home, 'market', 'plugin', '.mcp.json')))

    def test_install_codex(self):  # Review Focus 3
        got = self.apps(self.go(connect.install, 'codex'))
        self.assertTrue(got['codex']['installed'])
        cmds = [a[1:] for a in self.mac.ran if os.path.basename(a[0]) == 'codex']
        self.assertIn(['mcp', 'remove', 'assieme'], cmds)
        self.assertIn(['plugin', 'add', 'assieme@assieme'], cmds)
        self.assertNotIn('codex', self.mac.old)

    def test_install_the_claude_app(self):  # Review Focus 5: its own install window, no file written behind it
        cfg = connect.desktop_config(self.mac.home)
        with open(cfg, 'w') as f:
            json.dump({'mcpServers': {'assieme': {'command': 'old'}, 'other': {'command': 'x'}}}, f)
        self.mac.app_open = True
        got = self.apps(self.go(connect.install, 'claude-app'))['claude-app']
        self.assertEqual(got['problem'], 'Finish in the Claude app’s install window.')
        opened = [a for a in self.mac.ran if a[0] == 'open']
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0][1].endswith('assieme.mcpb'))
        with open(cfg) as f:
            self.assertIn('assieme', json.load(f)['mcpServers'])  # the app is open: its file is left alone
        self.mac.install_extension()  # the person clicked Install, the app still open
        got = self.apps(self.go(connect.status))['claude-app']
        self.assertTrue(got['installed'])
        self.assertEqual(got.get('problem'), 'Quit the Claude app, then open Settings here again to remove its old Assieme entry.')

    def test_the_old_claude_app_entry_goes_once_it_is_closed(self):
        cfg = connect.desktop_config(self.mac.home)
        with open(cfg, 'w') as f:
            json.dump({'theme': 'dark', 'mcpServers': {'assieme': {'command': 'old'}, 'other': {'command': 'x'}}}, f)
        self.mac.install_extension()
        self.go(connect.status)  # closed: tidied on the way
        with open(cfg) as f:
            data = json.load(f)
        self.assertEqual((data['theme'], data['mcpServers']), ('dark', {'other': {'command': 'x'}}))

    def test_one_app_failing_leaves_the_others(self):  # Review Focus 2
        self.which = lambda c: '/bin/claude' if c == 'claude' else None
        got = self.apps(self.go(connect.install, 'codex'))
        self.assertEqual(got['codex']['problem'], 'Codex isn’t installed on this Mac.')
        self.assertFalse(got['codex']['available'])
        self.mac.fail.add(('claude', 'plugin'))
        got = self.apps(self.go(connect.install, 'claude-code'))
        self.assertEqual(got['claude-code']['problem'], 'Claude Code couldn’t install it: claude broke')
        self.assertFalse(got['claude-code']['installed'])

    def test_remove_each(self):
        self.go(connect.install, 'claude-code')
        self.go(connect.install, 'codex')
        got = self.apps(self.go(connect.remove, 'claude-code'))
        self.assertFalse(got['claude-code']['installed'])
        got = self.apps(self.go(connect.remove, 'codex'))
        self.assertFalse(got['codex']['installed'])
        self.mac.install_extension()
        got = self.apps(self.go(connect.remove, 'claude-app'))['claude-app']
        self.assertEqual(got['problem'], 'Remove it in the Claude app: Settings → Extensions → Assieme.')

    def test_an_older_test_extension_counts(self):  # any local Assieme extension, whatever its author
        self.mac.install_extension('local.mcpb.moatcast.assieme')
        self.assertTrue(self.apps(self.go(connect.status))['claude-app']['installed'])

    def test_an_unknown_app(self):
        with self.assertRaisesRegex(ValueError, 'No app named slack'):
            self.go(connect.install, 'slack')

    def test_handlers(self):
        self.assertEqual(set(connect.handlers()), {'assistants.status', 'assistants.install', 'assistants.remove'})
