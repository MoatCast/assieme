"""Step 30: a role can keep an existing folder, branch and conversation; a team keeps scripts."""
import os
import shutil
import tempfile
import unittest

from assieme import newteam, preset


class KeptFolderTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.teams = os.path.join(self.tmp, 't')
        self.role = {'name': 'builder-1', 'agent': 'claude', 'job': 'tokyo', 'brief': 'b', 'checkout': 'branch',
                     'worktree': '/w/tokyo', 'branch': 'feature/retry-backoff', 'resume': 'abc-123'}

    def test_role_keeps_worktree_branch_resume(self):
        newteam.save(os.path.join(self.tmp, 'p'), [self.role], {}, 'shop', self.teams,
                     scripts={'setup': 'npm ci', 'run': 'npm run dev'})
        tm = preset.load(os.path.join(self.teams, 'shop'), practices=False)
        r = tm.roles[0]
        self.assertEqual((r.worktree, r.branch, r.resume), ('/w/tokyo', 'feature/retry-backoff', 'abc-123'))
        self.assertEqual(tm.scripts, {'setup': 'npm ci', 'run': 'npm run dev'})

    def test_plain_role_has_none(self):
        plain = {k: v for k, v in self.role.items() if k not in ('worktree', 'branch', 'resume')}
        newteam.save(os.path.join(self.tmp, 'p'), [plain], {}, 'shop', self.teams)
        tm = preset.load(os.path.join(self.teams, 'shop'), practices=False)
        self.assertEqual((tm.roles[0].worktree, tm.roles[0].branch, tm.roles[0].resume), (None, None, None))
        self.assertEqual(tm.scripts, {})

    def test_role_dict_round_trip(self):
        folder = newteam.save(os.path.join(self.tmp, 'p'), [self.role], {}, 'shop', self.teams)
        d = newteam._role_dict(preset.load(folder, practices=False).roles[0])
        self.assertEqual((d['worktree'], d['branch'], d['resume']), ('/w/tokyo', 'feature/retry-backoff', 'abc-123'))

    def test_clear_resume(self):
        folder = newteam.save(os.path.join(self.tmp, 'p'), [self.role], {}, 'shop', self.teams, scripts={'run': 'x'})
        newteam.clear_resume(folder, 'builder-1')
        tm = preset.load(folder, practices=False)
        self.assertIsNone(tm.roles[0].resume)
        self.assertEqual(tm.roles[0].worktree, '/w/tokyo')
        self.assertEqual(tm.scripts, {'run': 'x'})

    def test_validate_rejects_relative_worktree(self):
        with self.assertRaises(newteam.NewTeamError) as e:
            newteam.validate([self.role | {'worktree': 'w/tokyo'}])
        self.assertIn('full path', str(e.exception))

    def test_validate_rejects_odd_branch(self):
        for bad in ('a..b', 'a b', '-x'):
            with self.assertRaises(newteam.NewTeamError):
                newteam.validate([self.role | {'branch': bad}])

    def test_unknown_script_rejected(self):
        with self.assertRaises(ValueError):
            newteam.save(os.path.join(self.tmp, 'p'), [self.role], {}, 'shop', self.teams, scripts={'deploy': 'x'})


if __name__ == '__main__':
    unittest.main()
