"""Step 30: an agent brought over from another tool keeps working in that tool's worktree."""
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest

from assieme import worktree
from assieme.preset import Role, Team


def git(cwd, *args):
    return subprocess.run(['git', '-C', cwd, '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def tree_hash(path):
    h = hashlib.sha256()
    for root, dirs, files in sorted(os.walk(path)):
        dirs.sort()
        for n in sorted(files):
            p = os.path.join(root, n)
            h.update(p.encode() + open(p, 'rb').read())
    return h.hexdigest()


class KeptWorktreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, 'shop')
        os.makedirs(self.repo)
        git(self.repo, 'init', '-q', '-b', 'main')
        open(os.path.join(self.repo, 'README.md'), 'w').write('x\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-qm', 'init')
        self.kept = os.path.join(self.tmp, 'conductor', 'workspaces', 'shop', 'tokyo')
        git(self.repo, 'worktree', 'add', '-q', '-b', 'feature/x', self.kept)
        open(os.path.join(self.kept, 'a.py'), 'w').write('a\n')
        git(self.kept, 'add', '.')
        git(self.kept, 'commit', '-qm', 'work')
        self.role = Role('builder-1', 'claude', '', 'b', worktree=self.kept, branch='feature/x')
        self.team = Team('shop', self.repo, {}, [self.role])

    def test_ensure_adopted_returns_folder_as_is(self):
        open(os.path.join(self.kept, 'wip.py'), 'w').write('not committed\n')  # work in progress stays
        before, status = tree_hash(self.kept), git(self.kept, 'status', '--porcelain')
        self.assertEqual(worktree.ensure(self.team, self.role), self.kept)
        self.assertEqual((tree_hash(self.kept), git(self.kept, 'status', '--porcelain')), (before, status))
        self.assertFalse(os.path.exists(worktree.folder(self.team, self.role)))

    def test_ensure_adopted_wrong_repo(self):
        other = os.path.join(self.tmp, 'other')
        os.makedirs(other)
        git(other, 'init', '-q')
        self.role.worktree = other
        with self.assertRaisesRegex(worktree.WorktreeError, "isn't a copy of this project"):
            worktree.ensure(self.team, self.role)

    def test_ensure_adopted_folder_gone_uses_branch(self):
        tip = git(self.repo, 'rev-parse', 'feature/x')
        shutil.rmtree(self.kept)
        git(self.repo, 'worktree', 'prune')
        path = worktree.ensure(self.team, self.role)
        self.assertEqual(path, worktree.folder(self.team, self.role))
        self.assertEqual(git(path, 'branch', '--show-current'), 'assieme/shop/builder-1')
        self.assertEqual(git(path, 'rev-parse', 'HEAD'), tip)
        self.assertEqual(self.role.worktree, self.kept)  # the saved team is not changed here

    def test_ensure_adopted_gone_twice_reuses_copy(self):
        shutil.rmtree(self.kept)
        git(self.repo, 'worktree', 'prune')
        first = worktree.ensure(self.team, self.role)
        self.assertEqual(worktree.ensure(self.team, self.role), first)

    def test_branch_of(self):
        self.assertEqual(worktree.branch_of(self.team, self.role), 'feature/x')
        self.assertEqual(worktree.branch_of(self.team, Role('b', 'claude', '', 'b')), 'assieme/shop/b')

    def test_branch_of_gone_folder_is_assieme_branch(self):
        shutil.rmtree(self.kept)
        git(self.repo, 'worktree', 'prune')
        self.assertEqual(worktree.branch_of(self.team, self.role), 'assieme/shop/builder-1')


if __name__ == '__main__':
    unittest.main()
