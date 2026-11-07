"""Worktrees: one per role inside the project, using the real git command in a temporary repository."""
import os
import shutil
import subprocess
import tempfile
import unittest

from assieme import worktree
from assieme.preset import Role, Team


def git(cwd, *args):
    return subprocess.run(['git', '-C', cwd, *args], capture_output=True, text=True, check=True).stdout.strip()


class WorktreeTest(unittest.TestCase):
    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp())
        git(self.repo, 'init', '-q')
        open(os.path.join(self.repo, 'README.md'), 'w').write('x\n')
        git(self.repo, 'add', '.')
        git(self.repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init')
        self.builder = Role('builder-1', 'codex', 'b.md', 'brief')
        self.coord = Role('coordinator', 'claude', 'c.md', 'brief', checkout='read-only')
        self.team = Team('demo', self.repo, {}, [self.coord, self.builder])

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_creates_branch_worktree(self):
        p = worktree.ensure(self.team, self.builder)
        self.assertEqual(p, f'{self.repo}/.assieme/worktrees/builder-1')
        self.assertEqual(git(p, 'branch', '--show-current'), 'assieme/demo/builder-1')

    def test_read_only_is_detached(self):
        p = worktree.ensure(self.team, self.coord)
        self.assertEqual(git(p, 'branch', '--show-current'), '')

    def test_reuses_clean_worktree(self):
        self.assertEqual(worktree.ensure(self.team, self.builder), worktree.ensure(self.team, self.builder))

    def test_refuses_dirty_worktree(self):
        p = worktree.ensure(self.team, self.builder)
        open(f'{p}/x.txt', 'w').write('x')
        with self.assertRaisesRegex(worktree.WorktreeError, 'uncommitted'):
            worktree.ensure(self.team, self.builder)
        self.assertTrue(os.path.exists(f'{p}/x.txt'))

    def test_plain_folder_is_not_reused(self):
        os.makedirs(f'{self.repo}/.assieme/worktrees/builder-1')
        with self.assertRaisesRegex(worktree.WorktreeError, 'not a git worktree'):
            worktree.ensure(self.team, self.builder)

    def test_exclude_added_once(self):
        worktree.ensure(self.team, self.builder)
        worktree.ensure(self.team, self.coord)
        with open(f'{self.repo}/.git/info/exclude') as f:
            self.assertEqual(f.read().splitlines().count('.assieme/'), 1)
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')

    def test_not_a_repo(self):
        d = tempfile.mkdtemp()
        try:
            with self.assertRaisesRegex(worktree.WorktreeError, 'git init'):
                worktree.check_repo(d)
        finally:
            shutil.rmtree(d)

    def test_branch_taken_elsewhere(self):
        git(self.repo, 'branch', 'assieme/demo/builder-1')
        with self.assertRaisesRegex(worktree.WorktreeError, 'assieme/demo/builder-1'):
            worktree.ensure(self.team, self.builder)
