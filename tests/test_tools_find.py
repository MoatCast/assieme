"""Step 32: what each agent kind has installed, as names and descriptions only."""
import json
import os
import shutil
import tempfile
import unittest

from assieme import tools

SECRET = 'sk-SECRET123'


def skill(folder, name, description):
    os.makedirs(os.path.join(folder, name), exist_ok=True)
    with open(os.path.join(folder, name, 'SKILL.md'), 'w') as f:
        f.write(f'---\nname: {name}\ndescription: {description}\n---\n\nBody.\n')


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)


def server(**extra):
    return {'command': 'npx', 'args': ['-y', 'pkg', '--api-key', SECRET], 'env': {'TOKEN': 'abc-token'}, **extra}


class Home(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home)
        self.proj = os.path.join(self.home, 'code', 'shop')
        os.makedirs(self.proj)

    def h(self, *p):
        return os.path.join(self.home, *p)

    def ids(self, kind, **kw):
        return {i['id'] for i in tools.find(kind, self.proj, self.home, **kw)['items']}

    def item(self, kind, id_, **kw):
        return next(i for i in tools.find(kind, self.proj, self.home, **kw)['items'] if i['id'] == id_)


class ClaudeTest(Home):
    def setUp(self):
        super().setUp()
        write(self.h('.claude.json'), json.dumps({
            'mcpServers': {'context7': server(), 'assieme': server()},
            'projects': {self.proj: {'mcpServers': {'local-db': server()}}}}))
        write(os.path.join(self.proj, '.mcp.json'), json.dumps({'mcpServers': {'context7': server(), 'browser-mcp': server()}}))
        install = self.h('.claude', 'plugins', 'cache', 'planban', 'planban', '1.0')
        write(os.path.join(install, '.claude-plugin', 'plugin.json'), json.dumps({'name': 'planban', 'description': 'Roadmap board'}))
        write(self.h('.claude', 'plugins', 'installed_plugins.json'), json.dumps(
            {'version': 2, 'plugins': {'planban@planban': [{'installPath': install}], 'off@x': [{'installPath': '/nope'}]}}))
        write(self.h('.claude', 'settings.json'), json.dumps({'enabledPlugins': {'planban@planban': True, 'off@x': False}}))
        skill(self.h('.claude', 'skills'), 'literature-review', 'Reviews papers')

    def test_claude_lists_servers_plugins_skills(self):
        ids = self.ids('claude')
        self.assertTrue({'server:context7', 'server:local-db', 'server:browser-mcp', 'plugin:planban@planban',
                         'skill:*', 'skill:literature-review'} <= ids, ids)
        self.assertEqual(self.item('claude', 'plugin:planban@planban')['description'], 'Roadmap board')

    def test_same_name_user_and_project_is_one_item(self):  # Review Focus 1
        items = tools.find('claude', self.proj, self.home)['items']
        self.assertEqual(sum(1 for i in items if i['id'] == 'server:context7'), 1)

    def test_disabled_plugin_not_listed(self):
        self.assertNotIn('plugin:off@x', self.ids('claude'))

    def test_assieme_server_not_listed(self):
        self.assertNotIn('server:assieme', self.ids('claude'))

    def test_single_skills_locked_all_skills_not(self):
        one = self.item('claude', 'skill:literature-review')
        self.assertTrue(one['locked'])
        self.assertEqual(one['why'], tools.LOCKED[('claude', 'skill')])
        self.assertFalse(self.item('claude', 'skill:*')['locked'])
        self.assertEqual(self.item('claude', 'skill:literature-review')['description'], 'Reviews papers')

    def test_unreadable_file_is_a_notice(self):
        write(os.path.join(self.proj, '.mcp.json'), '{not json')
        got = tools.find('claude', self.proj, self.home)
        self.assertIn('server:context7', {i['id'] for i in got['items']})
        self.assertEqual(len(got['notices']), 1)

    def test_items_have_only_safe_fields(self):
        for i in tools.find('claude', self.proj, self.home)['items']:
            self.assertEqual(set(i), {'id', 'type', 'name', 'description', 'locked', 'why'})


class SecretsTest(Home):
    def test_secrets_never_come_out(self):  # Review Focus 3
        write(self.h('.claude.json'), json.dumps({'mcpServers': {'a': server()}}))
        write(self.h('.codex', 'config.toml'), f'[mcp_servers.a]\ncommand = "npx"\nargs = ["--key", "{SECRET}"]\n'
                                               '[mcp_servers.a.env]\nTOKEN = "abc-token"\n')
        write(self.h('.gemini', 'settings.json'), json.dumps({'mcpServers': {'a': server()}}))
        write(self.h('.config', 'opencode', 'opencode.json'), json.dumps({'mcp': {'a': {'type': 'local', 'command': ['x', SECRET]}}}))
        write(self.h('.cursor', 'mcp.json'), json.dumps({'mcpServers': {'a': server()}}))
        for kind in ('claude', 'codex', 'gemini', 'opencode', 'cursor', 'antigravity'):
            out = json.dumps(tools.find(kind, self.proj, self.home))
            self.assertNotIn('SECRET123', out, kind)
            self.assertNotIn('abc-token', out, kind)
            if kind != 'antigravity':
                self.assertIn('server:a', out, kind)


class CodexTest(Home):
    def test_codex(self):
        write(self.h('.codex', 'config.toml'), '[mcp_servers.context7]\ncommand = "npx"\n'
                                               '[mcp_servers.old]\ncommand = "x"\nenabled = false\n'
                                               '[plugins."ponytail@ponytail"]\nenabled = true\n')
        skill(self.h('.codex', 'skills'), 'gsap-core', 'GSAP basics')
        skill(self.h('.codex', 'plugins', 'cache', 'ponytail', 'ponytail', '5.3.3', 'skills'), 'ponytail-audit', 'Audit')
        ids = self.ids('codex')
        self.assertTrue({'server:context7', 'skill:gsap-core', 'skill:ponytail:ponytail-audit'} <= ids, ids)
        self.assertNotIn('server:old', ids)
        self.assertFalse(any(i.startswith('plugin:') for i in ids))
        paths = tools.skill_paths('codex', self.proj, self.home)
        self.assertTrue(paths['skill:ponytail:ponytail-audit'].endswith('ponytail-audit/SKILL.md'))


class GeminiTest(Home):
    def setUp(self):
        super().setUp()
        write(self.h('.gemini', 'settings.json'), json.dumps({'mcpServers': {'github': server()}}))
        write(self.h('.gemini', 'extensions', 'review-kit', 'gemini-extension.json'), json.dumps({'name': 'review-kit', 'description': 'Reviews'}))
        skill(self.h('.agents', 'skills'), 'context7-mcp', 'Docs lookup')

    def test_gemini(self):
        self.assertTrue({'server:github', 'extension:review-kit', 'skill:context7-mcp'} <= self.ids('gemini'))
        self.assertFalse(self.item('gemini', 'skill:context7-mcp')['locked'])

    def test_shared_folder_locks_skills(self):
        it = self.item('gemini', 'skill:context7-mcp', own_copy=False)
        self.assertTrue(it['locked'])
        self.assertEqual(it['why'], tools.LOCKED[('gemini', 'skill-shared')])


class OpenCodeTest(Home):
    def test_opencode_jsonc(self):
        write(self.h('.config', 'opencode', 'opencode.jsonc'),
              '{\n  // comment\n  "mcp": {"web": {"type": "remote", "url": "https://x"}, "gone": {"type": "local", "command": ["x"], "enabled": false}}\n}\n')
        self.assertEqual(self.ids('opencode'), {'server:web'})


class CursorTest(Home):
    def test_cursor_user_servers_locked_with_sentence(self):
        write(self.h('.cursor', 'mcp.json'), json.dumps({'mcpServers': {'home-one': server()}}))
        write(os.path.join(self.proj, '.cursor', 'mcp.json'), json.dumps({'mcpServers': {'proj-one': server(), 'assieme': server()}}))
        home = self.item('cursor', 'server:home-one')
        self.assertTrue(home['locked'])
        self.assertEqual(home['why'], tools.LOCKED[('cursor', 'server-home')])
        self.assertFalse(self.item('cursor', 'server:proj-one')['locked'])
        self.assertTrue(self.item('cursor', 'server:proj-one', own_copy=False)['locked'])
        self.assertNotIn('server:assieme', self.ids('cursor'))


class AntigravityTest(Home):
    def test_antigravity_says_so(self):
        got = tools.find('antigravity', self.proj, self.home)
        self.assertEqual(got['items'], [])
        self.assertEqual(got['notices'], [tools.LOCKED[('antigravity', 'all')]])


if __name__ == '__main__':
    unittest.main()
