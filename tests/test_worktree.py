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
        with self.assertRaises(worktree.WorktreeError) as c:
            worktree.ensure(self.team, self.builder)
        self.assertEqual(str(c.exception), "builder-1's copy has changes that aren't saved yet. "
                                           'Save or remove them, then start again.')
        self.assertEqual(c.exception.detail, p)
        self.assertTrue(os.path.exists(f'{p}/x.txt'))

    def test_plain_folder_is_not_reused(self):
        os.makedirs(f'{self.repo}/.assieme/worktrees/builder-1')
        with self.assertRaises(worktree.WorktreeError) as c:
            worktree.ensure(self.team, self.builder)
        self.assertEqual(str(c.exception), "A folder is in the way of builder-1's copy. Move it, then start again.")
        self.assertEqual(c.exception.detail, f'{self.repo}/.assieme/worktrees/builder-1')

    def test_exclude_added_once(self):
        worktree.ensure(self.team, self.builder)
        worktree.ensure(self.team, self.coord)
        with open(f'{self.repo}/.git/info/exclude') as f:
            self.assertEqual(f.read().splitlines().count('.assieme/'), 1)
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')

    def test_not_a_repo(self):
        d = tempfile.mkdtemp()
        try:
            with self.assertRaises(worktree.WorktreeError) as c:
                worktree.check_repo(d)
            self.assertEqual(str(c.exception), f"{os.path.basename(d)} isn't set up to keep versions yet. "
                                               'Choose it in New team to set it up.')
            self.assertEqual(c.exception.detail, '')
        finally:
            shutil.rmtree(d)

    def test_a_repo_with_no_commits_says_so(self):
        d = tempfile.mkdtemp()
        try:
            git(d, 'init', '-q')
            with self.assertRaises(worktree.NoCommitsError) as c:
                worktree.check_repo(d)
            self.assertEqual(str(c.exception), f'{os.path.basename(d)} has no saved version yet. '
                                               'Choose it in New team to save a first version.')
            self.assertEqual(c.exception.detail, '')
        finally:
            shutil.rmtree(d)

    def test_branch_taken_elsewhere(self):
        git(self.repo, 'branch', 'assieme/demo/builder-1')
        with self.assertRaises(worktree.WorktreeError) as c:
            worktree.ensure(self.team, self.builder)
        self.assertEqual(str(c.exception), 'A copy named assieme/demo/builder-1 is left over from an earlier team. '
                                           'Rename this team, or remove the old copy.')
        self.assertEqual(c.exception.detail, 'git branch -D assieme/demo/builder-1')

    def test_git_failure_keeps_git_text_in_detail(self):
        os.makedirs(f'{self.repo}/.assieme')
        open(f'{self.repo}/.assieme/worktrees', 'w').write('in the way\n')  # git can't make a folder under a file
        with self.assertRaises(worktree.WorktreeError) as c:
            worktree.ensure(self.team, self.builder)
        self.assertEqual(str(c.exception), "Assieme couldn't make builder-1's copy.")
        self.assertTrue(c.exception.detail.startswith('git worktree add'), c.exception.detail)
        self.assertNotIn('git', str(c.exception).replace('Assieme', ''))

    def test_one_argument_error_has_no_detail(self):
        self.assertEqual(worktree.WorktreeError('plain').detail, '')
        self.assertEqual(str(worktree.WorktreeError('plain')), 'plain')
