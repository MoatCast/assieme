"""Step 18a: Assieme as a Claude Code plugin, a Codex plugin and a Claude app extension, written at install time."""
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from assieme import actions, askhook, mcpserver, plugin

PY = '/opt/assieme/bin/python'


class BuildTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.dir))
        self.got = plugin.build(self.dir, PY)

    def read(self, *path):
        with open(os.path.join(self.dir, *path)) as f:
            return json.load(f)

    def test_the_marketplaces_list_the_plugin(self):
        self.assertEqual(self.got['market'], self.dir)
        claude = self.read('.claude-plugin', 'marketplace.json')
        codex = self.read('.agents', 'plugins', 'marketplace.json')
        self.assertEqual((claude['name'], claude['plugins'][0]['name'], claude['plugins'][0]['source']), ('assieme', 'assieme', './plugin'))
        self.assertEqual(codex['plugins'][0]['source'], {'source': 'local', 'path': './plugin'})

    def test_the_server_entry_runs_this_assieme_and_says_the_client_asks(self):
        for path in (('plugin', '.mcp.json'), ('plugin', 'codex-mcp.json')):
            s = self.read(*path)['mcpServers']['assieme']
            self.assertEqual((s['command'], s['args']), (PY, ['-P', '-m', 'assieme', 'mcp']), path)
            self.assertEqual(s['env'], {mcpserver.CLIENT_ASKS: '1'}, path)

    def test_claude_codes_hook_is_only_claude_codes(self):  # Codex reads hooks/hooks.json: it must not find one
        self.assertEqual(self.read('plugin', '.claude-plugin', 'plugin.json')['hooks'], './claude-hooks.json')
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'plugin', 'hooks', 'hooks.json')))
        hook = self.read('plugin', 'claude-hooks.json')['hooks']['PreToolUse'][0]
        self.assertEqual(hook['matcher'], 'mcp__.*assieme.*')
        self.assertEqual(hook['hooks'][0]['command'], f'{PY} -P -m assieme.askhook')

    def test_codex_prompts_for_every_change_tool(self):
        tools = self.read('plugin', 'codex-mcp.json')['mcpServers']['assieme']['tools']
        self.assertEqual({n for n, t in tools.items() if t['approval_mode'] == 'prompt'}, set(askhook.CHANGE_TOOLS) | {'app_action'})
        self.assertEqual(self.read('plugin', '.codex-plugin', 'plugin.json')['mcpServers'], './codex-mcp.json')

    def test_the_skill_is_the_guide(self):
        with open(os.path.join(self.dir, 'plugin', 'skills', 'use-assieme', 'SKILL.md')) as f:
            skill = f.read()
        self.assertTrue(skill.startswith('---\nname: use-assieme\ndescription: '))
        with open(os.path.join(os.path.dirname(mcpserver.__file__), 'guide', 'team-design.md')) as f:
            self.assertIn(f.read().strip(), skill)

    def test_the_extension(self):
        with zipfile.ZipFile(self.got['extension']) as z:
            m = json.loads(z.read('manifest.json'))
        self.assertEqual((m['name'], m['display_name']), ('assieme', 'Assieme'))
        cfg = m['server']['mcp_config']
        self.assertEqual((cfg['command'], cfg['args'], cfg['env']), (PY, ['-P', '-m', 'assieme', 'mcp'], {mcpserver.CLIENT_ASKS: '1'}))

    def test_building_again_replaces_it(self):
        again = plugin.build(self.dir, '/other/python')
        self.assertEqual(self.read('plugin', '.mcp.json')['mcpServers']['assieme']['command'], '/other/python')
        self.assertEqual(again['market'], self.dir)


class AskHookTest(unittest.TestCase):

    def decide(self, tool, tool_input=None):
        out = io.StringIO()
        askhook.main(io.StringIO(json.dumps({'tool_name': tool, 'tool_input': tool_input or {}})), out)
        return json.loads(out.getvalue())['hookSpecificOutput']['permissionDecision'] if out.getvalue() else None

    def test_every_change_tool_asks(self):
        for t in askhook.CHANGE_TOOLS:
            self.assertEqual(self.decide(f'mcp__plugin_assieme_assieme__{t}'), 'ask', t)

    def test_reads_are_left_alone(self):
        for t in ('list_teams', 'team_status', 'what_needs_you', 'read_terminal', 'recent_activity', 'app_actions', 'draft_team'):
            self.assertIsNone(self.decide(f'mcp__plugin_assieme_assieme__{t}'), t)

    def test_app_action_asks_only_for_a_change(self):
        self.assertEqual(self.decide('mcp__plugin_assieme_assieme__app_action', {'name': 'review.merge'}), 'ask')
        self.assertIsNone(self.decide('mcp__plugin_assieme_assieme__app_action', {'name': 'review.get'}))
        self.assertEqual(self.decide('mcp__plugin_assieme_assieme__app_action', {'name': 'made.up'}), 'ask')  # unknown: safe side

    def test_the_change_tools_are_the_servers(self):  # a new change tool in the server must ask here too
        import asyncio
        from mcp import Client

        async def names():
            async with Client(mcpserver.control_server()) as c:
                return {t.name: t.annotations for t in (await c.list_tools()).tools}
        tools = asyncio.run(names())
        acting = {n for n, a in tools.items() if a and a.read_only_hint is False and n != 'draft_team'}
        self.assertEqual(acting - {'app_action'}, set(askhook.CHANGE_TOOLS))

    def test_a_bad_input_asks(self):
        out = io.StringIO()
        askhook.main(io.StringIO('not json'), out)
        self.assertEqual(json.loads(out.getvalue())['hookSpecificOutput']['permissionDecision'], 'ask')

    def test_changes_in_the_catalogue_ask(self):
        some = next(n for n, a in actions.CATALOGUE.items() if a.changes)
        self.assertEqual(self.decide('mcp__assieme__app_action', {'name': some}), 'ask')


class GuideTest(unittest.TestCase):
    def test_the_guide_mentions_start_draft(self):
        with open(os.path.join(os.path.dirname(mcpserver.__file__), 'guide', 'team-design.md')) as f:
            guide = f.read()
        self.assertIn('start_draft', guide)
        self.assertNotIn('You never start a new team yourself', guide)


if __name__ == '__main__':
    unittest.main()
