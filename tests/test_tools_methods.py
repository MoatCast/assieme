"""Step 32: the brain methods: list and suggest, save one agent's choice (with Undo), restart with it."""
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from assieme import activity, agenttools, launch, newteam, preset
from assieme.preset import Role


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = os.path.join(self.tmp, 'home')
        os.makedirs(os.path.join(self.home))
        with open(os.path.join(self.home, '.claude.json'), 'w') as f:
            json.dump({'mcpServers': {'context7': {}, 'browser-mcp': {}}}, f)
        p = mock.patch.dict(os.environ, {'HOME': self.home, 'XDG_STATE_HOME': os.path.join(self.tmp, 'state')})
        p.start()
        self.addCleanup(p.stop)
        self.teams = os.path.join(self.tmp, 'teams')
        self.proj = os.path.join(self.tmp, 'proj')
        os.makedirs(self.proj)
        self.folder = newteam.save(self.proj, [{'name': 'reviewer', 'agent': 'claude', 'job': '', 'brief': 'b',
                                                'checkout': 'read-only', 'kind': 'reviewer'}], {}, 'shop', self.teams)

    def role(self):
        return preset.load(self.folder, practices=False).roles[0]


class SetToolsTest(Base):
    def test_writes_and_knows_the_inventory(self):
        agenttools.set_tools(self.teams, 'shop', 'reviewer', ['server:browser-mcp'], False)
        r = self.role()
        self.assertEqual((r.tools_off, r.tools_new), (['server:browser-mcp'], False))
        self.assertEqual(set(r.tools_known), {'server:context7', 'server:browser-mcp'})

    def test_bad_id_refused(self):
        with self.assertRaises(newteam.NewTeamError):
            agenttools.set_tools(self.teams, 'shop', 'reviewer', ['rm -rf /'], True)
        self.assertEqual(self.role().tools_off, [])

    def test_unknown_role_refused(self):
        with self.assertRaises(ValueError):
            agenttools.set_tools(self.teams, 'shop', 'nobody', [], True)

    def test_undo_restores(self):
        entry = agenttools.set_tools(self.teams, 'shop', 'reviewer', ['server:browser-mcp'], True)
        self.assertEqual(activity.undo(entry['id'], rt=None)['ok'], True)
        self.assertEqual((self.role().tools_off, self.role().tools_new), ([], True))

    def test_suggest_for_a_role_kind(self):
        self.assertEqual(agenttools.suggest('reviewer', 'claude', self.proj), ['server:browser-mcp'])


class RestartTest(Base):
    def test_restart_stops_then_starts_with_continue(self):
        rt = SimpleNamespace(killed=[], kill=lambda a: rt.killed.append(a),
                             list=lambda *a, **k: [{'address': 'x:1', 'state': 'live',
                                                    'labels': {'assieme.team': 'shop', 'assieme.role': 'reviewer'}}])
        started = {}

        def fake_start(tm, task, rt_, ask, only=None):
            started['only'] = only
            started['cont'] = next(r for r in tm.roles if r.name == only).cont
            return []
        with mock.patch('assieme.team.start', fake_start):
            got = agenttools.restart(self.teams, rt, 'shop', 'reviewer', thread=lambda target, daemon: SimpleNamespace(start=target))
        self.assertEqual(got['ok'], True)
        self.assertEqual(rt.killed, ['x:1'])
        self.assertEqual(started, {'only': 'reviewer', 'cont': True})

    def test_restart_of_a_stopped_role_just_starts(self):
        rt = SimpleNamespace(killed=[], kill=lambda a: rt.killed.append(a), list=lambda *a, **k: [])
        with mock.patch('assieme.team.start', lambda *a, **k: []):
            agenttools.restart(self.teams, rt, 'shop', 'reviewer', thread=lambda target, daemon: SimpleNamespace(start=target))
        self.assertEqual(rt.killed, [])


class ContinueFlagTest(unittest.TestCase):
    def argv(self, agent):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        r = Role('b', agent, '', 'b', cont=True)
        return launch.argv(r, block_push=False, which=lambda n: None, cwd=d, trusted=True)

    def test_continue_for_claude_and_codex_only(self):
        self.assertIn('--continue', self.argv('claude'))
        a = self.argv('codex')
        self.assertEqual(a[1:3], ['resume', '--last'])
        self.assertNotIn('--continue', self.argv('opencode'))


if __name__ == '__main__':
    unittest.main()
