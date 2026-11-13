"""Step 31: each agent plays an instrument, chosen by its role and saved with it."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from assieme import mcptools, musicians, newteam, preset


def r(name, kind=None, **kw):
    return {'name': name, 'agent': 'claude', 'job': '', 'brief': 'b', 'checkout': 'branch', 'kind': kind, **kw}


TEAM = [r('planner', 'planner'), r('builder-1', 'builder'), r('builder-2', 'builder'), r('builder-3', 'builder'),
        r('builder-4', 'builder'), r('reviewer', 'reviewer'), r('tester', 'tester'), r('researcher'), r('writer')]


class RuleTest(unittest.TestCase):

    def test_rule(self):
        self.assertEqual(musicians.assign(TEAM), ['piano', 'violin', 'cello', 'bass', 'violin', 'reviewer', 'timpani',
                                                  'flute', 'clarinet'])

    def test_instruments(self):
        self.assertEqual(musicians.INSTRUMENTS, ['piano', 'reviewer', 'violin', 'cello', 'bass', 'timpani', 'flute',
                                                 'clarinet', 'trumpet'])

    def test_fourth_builder_second_palette(self):
        self.assertEqual([musicians.palette(TEAM, i) for i in range(len(TEAM))], [0, 0, 0, 0, 1, 0, 0, 0, 0])

    def test_saved_instrument_kept(self):
        team = [r('builder-1', 'builder', musician='trumpet'), r('builder-2', 'builder')]
        self.assertEqual(musicians.assign(team), ['trumpet', 'cello'])

    def test_added_agent_changes_no_one(self):  # Review Focus 2
        have = [{**x, 'musician': m} for x, m in zip(TEAM[:3], musicians.assign(TEAM[:3]))]
        got = musicians.assign(have + [r('builder-3', 'builder')])
        self.assertEqual(got[:3], ['piano', 'violin', 'cello'])
        self.assertEqual(got[3], 'bass')

    def test_works_on_roles_too(self):
        roles = [preset.Role('t', 'claude', '', '', kind='tester'), preset.Role('x', 'claude', '', '', musician='cello')]
        self.assertEqual(musicians.assign(roles), ['timpani', 'cello'])

    def test_unknown_saved_value_follows_the_rule(self):
        self.assertEqual(musicians.assign([r('tester', 'tester', musician='kazoo')]), ['timpani'])


class SavedTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.teams = os.path.join(self.tmp, 't')

    def save(self, roles):
        return newteam.save(os.path.join(self.tmp, 'p'), roles, {}, 'shop', self.teams)

    def test_round_trip(self):
        folder = self.save([r('builder-1', 'builder', musician='cello'), r('tester', 'tester')])
        got = preset.load(folder, practices=False).roles
        self.assertEqual([x.musician for x in got], ['cello', 'timpani'])  # the rule's choice is written at save
        self.assertEqual(newteam._role_dict(got[0])['musician'], 'cello')

    def test_old_team_gets_rule_without_rewrite(self):
        folder = self.save([r('tester', 'tester')])
        path = os.path.join(folder, 'team.toml')
        text = open(path).read().replace('musician = "timpani"\n', '')
        open(path, 'w').write(text)
        roles = preset.load(folder, practices=False).roles
        self.assertIsNone(roles[0].musician)
        self.assertEqual(musicians.assign(roles), ['timpani'])
        self.assertEqual(open(path).read(), text)

    def test_unknown_value_in_file_is_ignored(self):
        folder = self.save([r('tester', 'tester')])
        path = os.path.join(folder, 'team.toml')
        text = open(path).read().replace('"timpani"', '"kazoo"')
        open(path, 'w').write(text)
        self.assertIsNone(preset.load(folder, practices=False).roles[0].musician)

    def test_form_refuses_unknown(self):
        with self.assertRaises(newteam.NewTeamError):
            newteam.validate([r('tester', 'tester', musician='kazoo')])

    def test_bad_value_dropped_from_draft(self):
        repo = os.path.join(self.tmp, 'repo')
        os.makedirs(repo)
        subprocess.run(['git', '-C', repo, 'init', '-q'], check=True)
        subprocess.run(['git', '-C', repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q',
                        '--allow-empty', '-m', 'i'], check=True)
        me = SimpleNamespace(_drafts=lambda: os.path.join(self.tmp, 'drafts'), opener=lambda: True)
        with mock.patch.dict(os.environ, {'XDG_STATE_HOME': os.path.join(self.tmp, 'state')}):
            got = mcptools.Tools.draft_save(me, repo, [r('tester', 'tester', musician='kazoo'), r('b', 'builder')])
        saved = json.load(open(os.path.join(self.tmp, 'drafts', f"{got['id']}.json")))['roles']
        self.assertEqual([x['musician'] for x in saved], ['timpani', 'violin'])


if __name__ == '__main__':
    unittest.main()
