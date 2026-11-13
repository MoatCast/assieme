"""Step 32: each role saves which skills, plugins and MCP servers are off, and whether new ones are added."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from assieme import mcptools, newteam, preset


def role(**kw):
    return {'name': 'builder-1', 'agent': 'claude', 'job': '', 'brief': 'b', 'checkout': 'branch', **kw}


class SavedChoiceTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.teams = os.path.join(self.tmp, 't')

    def save(self, r):
        return newteam.save(os.path.join(self.tmp, 'p'), [r], {}, 'shop', self.teams)

    def test_round_trip(self):
        folder = self.save(role(tools_off=['server:context7'], tools_new=False,
                                tools_known=['server:context7', 'skill:pdf']))
        r = preset.load(folder, practices=False).roles[0]
        self.assertEqual((r.tools_off, r.tools_new, r.tools_known),
                         (['server:context7'], False, ['server:context7', 'skill:pdf']))
        d = newteam._role_dict(r)
        self.assertEqual((d['tools_off'], d['tools_new'], d['tools_known']),
                         (['server:context7'], False, ['server:context7', 'skill:pdf']))

    def test_old_team_has_everything_on(self):
        r = preset.load(self.save(role()), practices=False).roles[0]
        self.assertEqual((r.tools_off, r.tools_new, r.tools_known), ([], True, []))

    def test_defaults_not_written(self):
        folder = self.save(role())
        self.assertNotIn('tools_', open(os.path.join(folder, 'team.toml')).read())

    def test_preset_from_a_saved_team_keeps_the_choice(self):
        self.save(role(tools_off=['server:context7']))
        roles = [r for p in [*newteam.presets(teams=self.teams)['presets'], newteam.presets(teams=self.teams).get('last') or {}]
                 for r in (p.get('roles') or [])]
        self.assertIn(['server:context7'], [r.get('tools_off') for r in roles])

    def test_bad_id_refused(self):
        with self.assertRaises(newteam.NewTeamError) as e:
            newteam.validate([role(tools_off=['rm -rf /'])])
        self.assertEqual(e.exception.field, 'role:0')
        for bad in (['server:'], ['tool:x'], 'server:x', [3]):
            with self.assertRaises(newteam.NewTeamError):
                newteam.validate([role(tools_off=bad)])
        with self.assertRaises(newteam.NewTeamError):
            newteam.validate([role(tools_new='yes')])
        newteam.validate([role(tools_off=['server:a', 'plugin:planban@planban', 'skill:*', 'extension:x'],
                               tools_new=False, tools_known=['skill:pdf'])])

    def test_draft_keeps_valid_ids_only(self):
        d = self.tmp
        repo = os.path.join(d, 'repo')
        os.makedirs(repo)
        subprocess.run(['git', '-C', repo, 'init', '-q'], check=True)
        subprocess.run(['git', '-C', repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q',
                        '--allow-empty', '-m', 'i'], check=True)
        me = SimpleNamespace(_drafts=lambda: os.path.join(d, 'drafts'), opener=lambda: True)
        with mock.patch.dict(os.environ, {'XDG_STATE_HOME': os.path.join(d, 'state')}):
            got = mcptools.Tools.draft_save(me, repo, [role(tools_off=['server:a', 'nonsense', 7], tools_new=False)])
        saved = json.load(open(os.path.join(d, 'drafts', f"{got['id']}.json")))['roles'][0]
        self.assertEqual((saved['tools_off'], saved['tools_new']), (['server:a'], False))


if __name__ == '__main__':
    unittest.main()
