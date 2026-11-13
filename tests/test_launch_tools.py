"""Step 32: each kind starts with only its chosen skills, plugins and MCP servers."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from assieme import launch
from assieme.preset import Role

SECRET = 'sk-SECRET123'


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)


def server():
    return {'command': 'npx', 'args': ['--api-key', SECRET]}


class LaunchToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = os.path.join(self.tmp, 'home')
        self.cwd = os.path.join(self.tmp, 'copy')
        subprocess.run(['git', 'init', '-q', self.cwd], check=True)
        p = mock.patch.dict(os.environ, {'HOME': self.home, 'XDG_STATE_HOME': os.path.join(self.tmp, 'state')})
        p.start()
        self.addCleanup(p.stop)
        write(os.path.join(self.home, '.claude.json'), json.dumps({'mcpServers': {'context7': server(), 'paper': server()}}))
        write(os.path.join(self.home, '.claude', 'settings.json'), json.dumps({'enabledPlugins': {'planban@planban': True}}))
        os.makedirs(os.path.join(self.home, '.claude', 'skills', 'pdf'))
        write(os.path.join(self.home, '.claude', 'skills', 'pdf', 'SKILL.md'), '---\ndescription: PDFs\n---\n')
        write(os.path.join(self.home, '.codex', 'config.toml'), '[mcp_servers.context7]\ncommand = "npx"\n')
        write(os.path.join(self.home, '.codex', 'skills', 'gsap-core', 'SKILL.md'), '---\ndescription: GSAP\n---\n')
        write(os.path.join(self.home, '.gemini', 'settings.json'), json.dumps({'mcpServers': {'github': server(), 'paper': server()}}))
        write(os.path.join(self.home, '.gemini', 'extensions', 'kit', 'gemini-extension.json'), '{"name": "kit"}')
        write(os.path.join(self.home, '.gemini', 'skills', 'pdf', 'SKILL.md'), '---\ndescription: PDFs\n---\n')
        write(os.path.join(self.home, '.config', 'opencode', 'opencode.json'), json.dumps({'mcp': {'web': {'type': 'remote', 'url': 'x'}}}))
        write(os.path.join(self.cwd, '.cursor', 'mcp.json'), json.dumps({'mcpServers': {'proj-db': server(), 'proj-docs': server()}}))

    def argv(self, agent, checkout='branch', **kw):
        r = Role('b', agent, '', 'b', checkout=checkout, **kw)
        return launch.argv(r, block_push=False, which=lambda n: None, cwd=self.cwd, trusted=True)

    def settings(self, a):
        return json.loads(a[a.index('--settings') + 1])

    def test_defaults_unchanged(self):
        for agent in ('claude', 'codex', 'gemini', 'opencode', 'cursor', 'antigravity'):
            text = ' '.join(self.argv(agent))
            for new in ('deniedMcpServers', '--allowed-mcp-server-names', 'enabled=false', 'skills.config',
                        '--disable-slash-commands', 'enabledPlugins', ' -e '):
                self.assertNotIn(new, text, agent)
        self.assertEqual(set(json.load(open(os.path.join(self.cwd, '.cursor', 'mcp.json')))['mcpServers']),
                         {'proj-db', 'proj-docs', 'assieme'})

    def test_claude_denied_servers_plugins_and_all_skills(self):  # Review Focus 3: nothing secret on argv
        a = self.argv('claude', tools_off=['server:paper', 'plugin:planban@planban', 'skill:*'])
        s = self.settings(a)
        self.assertEqual(s['deniedMcpServers'], [{'serverName': 'paper'}])
        self.assertEqual(s['enabledPlugins'], {'planban@planban': False})
        self.assertIn('--disable-slash-commands', a)
        self.assertNotIn('SECRET123', ' '.join(a))
        self.assertNotIn('--strict-mcp-config', a)

    def test_codex_turns_off_named_server_and_skill(self):
        a = self.argv('codex', tools_off=['server:context7', 'skill:gsap-core'])
        self.assertIn('mcp_servers.context7.enabled=false', a)
        sk = next(x for x in a if x.startswith('skills.config='))
        self.assertIn('gsap-core/SKILL.md', sk)
        self.assertIn('enabled=false', sk)

    def test_gemini_allows_only_chosen_plus_assieme(self):
        a = self.argv('gemini', tools_off=['server:paper', 'extension:kit', 'skill:pdf'])
        i = a.index('--allowed-mcp-server-names')
        self.assertEqual(a[i + 1:i + 3], ['github', 'assieme'])
        self.assertEqual(a[a.index('-e') + 1], 'none')
        g = json.load(open(os.path.join(self.cwd, '.gemini', 'settings.json')))
        self.assertEqual(g['skills']['disabled'], ['pdf'])

    def test_gemini_shared_folder_skills_untouched(self):
        self.argv('gemini', checkout='read-only', tools_off=['skill:pdf'])
        g = json.load(open(os.path.join(self.cwd, '.gemini', 'settings.json')))
        self.assertNotIn('skills', g)

    def test_opencode_servers_locked_config_untouched(self):  # Task 10: OpenCode 2 drops an "off only" entry
        a = self.argv('opencode', tools_off=['server:web'])
        env = dict(x.split('=', 1) for x in a if x.startswith('OPENCODE_CONFIG_CONTENT='))
        cfg = json.loads(env['OPENCODE_CONFIG_CONTENT'])
        self.assertEqual(set(cfg['mcp']), {'assieme'})
        self.assertTrue(cfg['mcp']['assieme']['enabled'])

    def test_cursor_own_copy_takes_servers_out(self):
        a = self.argv('cursor', tools_off=['server:proj-docs'])
        self.assertEqual(set(json.load(open(os.path.join(self.cwd, '.cursor', 'mcp.json')))['mcpServers']),
                         {'proj-db', 'assieme'})
        self.assertNotIn('--approve-mcps', a)
        a = self.argv('cursor', tools_off=['server:proj-db'])
        self.assertIn('--approve-mcps', a)  # only Assieme's is left

    def test_cursor_shared_folder_untouched(self):
        self.argv('cursor', checkout='read-only', tools_off=['server:proj-docs'])
        self.assertIn('proj-docs', json.load(open(os.path.join(self.cwd, '.cursor', 'mcp.json')))['mcpServers'])

    def test_assieme_server_always_on(self):
        a = self.argv('claude', tools_off=['server:assieme', 'server:paper'])
        self.assertEqual(self.settings(a)['deniedMcpServers'], [{'serverName': 'paper'}])
        self.assertIn('assieme', ' '.join(a))

    def test_new_off_when_switch_off(self):
        a = self.argv('claude', tools_new=False, tools_known=['server:context7'])
        self.assertEqual(self.settings(a)['deniedMcpServers'], [{'serverName': 'paper'}])


if __name__ == '__main__':
    unittest.main()
