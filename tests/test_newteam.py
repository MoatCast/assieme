"""New team in the app: presets, checks, saving, background start and Help me choose."""
import os
import shutil
import tempfile
import time
import unittest

from assieme import newteam

SAVED = '''name = "{name}"
project = "/tmp/x"

[[role]]
name = "builder"
agent = "claude"
job = "Builds"
'''


class PresetsTest(unittest.TestCase):
    def setUp(self):
        self.teams = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.teams)

    def saved(self, name, mtime):
        d = os.path.join(self.teams, name)
        os.makedirs(d)
        with open(os.path.join(d, 'team.toml'), 'w') as f:
            f.write(SAVED.format(name=name))
        with open(os.path.join(d, 'builder.md'), 'w') as f:
            f.write('Build it.\n')
        os.utime(d, (mtime, mtime))

    def test_builtin_presets(self):
        p = newteam.presets(teams=self.teams)
        self.assertEqual([x['id'] for x in p['presets']], ['one-agent', 'builder-reviewer', 'full'])
        self.assertEqual([x['label'] for x in p['presets']],
                         ['One agent', 'Builder + reviewer', 'Planner, 3 builders, reviewer, tester'])
        full = p['presets'][2]['roles']
        self.assertEqual([r['name'] for r in full],
                         ['planner', 'builder-1', 'builder-2', 'builder-3', 'reviewer', 'tester'])
        self.assertTrue(all(r['job'] and r['brief'] and 'Team practices' not in r['brief'] for r in full))
        self.assertEqual(set(full[0]), {'name', 'agent', 'job', 'checkout', 'parent', 'brief'})
        self.assertIsNone(full[0]['parent'])
        self.assertEqual(full[4]['checkout'], 'read-only')
        self.assertEqual([g['id'] for g in p['practices']['groups']][0], 'messages')
        self.assertIsNone(p['last'])

    def test_last_is_newest_saved_team(self):
        now = time.time()
        self.saved('older', now - 100)
        self.saved('newer', now)
        last = newteam.presets(teams=self.teams)['last']
        self.assertEqual((last['id'], last['label']), ('newer', 'My last team'))
        self.assertEqual(last['roles'][0]['brief'].strip(), 'Build it.')

    def test_broken_saved_team_is_skipped(self):
        os.makedirs(os.path.join(self.teams, 'broken'))
        self.assertIsNone(newteam.presets(teams=self.teams)['last'])
