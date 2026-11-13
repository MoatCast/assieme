"""Step 30: Review, merge and discard for agents brought over in their own worktree. The other tool's folder and
branch are never removed."""
import os
import unittest

from assieme import preset
from tests.test_review import Base, git, write
from tests.test_worktree_kept import tree_hash


class KeptReview(Base):
    def setUp(self):
        super().setUp()
        self.kept = os.path.join(self.root, 'conductor', 'workspaces', 'shop', 'tokyo')
        git(self.repo, 'worktree', 'add', '-q', '-b', 'feature/x', self.kept)
        write(f'{self.kept}/c.py', 'c\n')
        git(self.kept, 'add', '.')
        git(self.kept, 'commit', '-qm', 'one')
        write(f'{self.kept}/d.py', 'd\n')
        git(self.kept, 'add', '.')
        git(self.kept, 'commit', '-qm', 'two')
        write(f'{self.teams}/demo/team.toml',
              f'name = "demo"\nproject = "{self.repo}"\n\n[[role]]\nname = "builder-1"\nagent = "claude"\n'
              f'worktree = "{self.kept}"\nbranch = "feature/x"\n')
        write(f'{self.teams}/demo/builder-1.md', 'b\n')

    def tip(self):
        return git(self.kept, 'rev-parse', 'HEAD')

    def test_waiting_lists_adopted_branch(self):
        rows = [w for w in self.r.waiting() if w['role'] == 'builder-1']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['branch'], rows[0]['ahead']), ('feature/x', 2))

    def test_get_uses_adopted_folder(self):
        project, base, branch, path = self.r.worktree('demo', 'builder-1')
        self.assertEqual((branch, path), ('feature/x', self.kept))

    def test_merge_adopted_branch(self):
        before = tree_hash(self.kept)
        got = self.r.merge('demo', 'builder-1', self.tip(), force=True)
        self.assertIn('Its folder stays in', got['problem'] or '')
        self.assertIn('Conductor owns it', got['problem'])
        self.assertTrue(os.path.isdir(self.kept))
        self.assertEqual(tree_hash(self.kept), before)
        self.assertEqual(git(self.repo, 'rev-parse', '--verify', 'feature/x'), self.tip())  # branch kept
        self.assertIn('d.py', git(self.repo, 'ls-tree', '--name-only', 'main'))
        role = preset.load(f'{self.teams}/demo', practices=False).roles[0]
        self.assertEqual((role.worktree, role.branch), (None, None))
        self.assertEqual([w for w in self.r.waiting() if w['role'] == 'builder-1'], [])

    def test_undo_merge_brings_adopted_back(self):
        got = self.r.merge('demo', 'builder-1', self.tip(), force=True)
        self.r.undo(got['undo'])
        role = preset.load(f'{self.teams}/demo', practices=False).roles[0]
        self.assertEqual((role.worktree, role.branch), (self.kept, 'feature/x'))
        self.assertNotIn('d.py', git(self.repo, 'ls-tree', '--name-only', 'main'))

    def test_discard_adopted_leaves_folder(self):
        before = tree_hash(self.kept)
        got = self.r.discard('demo', 'builder-1', self.tip())
        self.assertIn('Its folder stays in', got['problem'])
        self.assertEqual(tree_hash(self.kept), before)
        self.assertEqual(git(self.repo, 'rev-parse', '--verify', 'feature/x'), self.tip())
        role = preset.load(f'{self.teams}/demo', practices=False).roles[0]
        self.assertIsNone(role.worktree)


if __name__ == '__main__':
    unittest.main()
