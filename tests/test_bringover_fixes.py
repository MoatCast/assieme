"""Step 30, fixes from the final review: each test reproduces one finding."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from assieme import bringover, launch, newteam, worktree
from assieme.preset import Role, Team
from tests.test_bringover import Home
from tests.test_review_kept import KeptReview
from tests.test_worktree_kept import KeptWorktreeTest, git


class BranchAtStart(KeptWorktreeTest):
    def test_ensure_refuses_folder_on_another_branch(self):  # Important 1
        git(self.kept, 'checkout', '-q', '-b', 'other')
        with self.assertRaisesRegex(worktree.WorktreeError, 'is on other now, not feature/x'):
            worktree.ensure(self.team, self.role)

    def test_ensure_refuses_detached_folder(self):
        git(self.kept, 'checkout', '-q', '--detach')
        with self.assertRaisesRegex(worktree.WorktreeError, 'is on no branch now'):
            worktree.ensure(self.team, self.role)


class ResumeOnlyInItsFolder(unittest.TestCase):  # Important 2
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)

    def argv(self, agent, folder):
        r = Role('b', agent, '', 'b', worktree=folder, branch='x', resume='abc-123')
        return launch.argv(r, block_push=False, which=lambda n: None, cwd=self.d)

    def test_no_resume_when_folder_is_gone(self):
        self.assertNotIn('--resume', self.argv('claude', os.path.join(self.d, 'gone')))
        self.assertNotIn('resume', self.argv('codex', os.path.join(self.d, 'gone')))

    def test_resume_when_folder_is_there(self):
        self.assertIn('--resume', self.argv('claude', self.d))

    def test_start_says_the_folder_is_gone(self):
        notes = newteam.gone_notes([Role('builder-1', 'claude', '', 'b', worktree='/nowhere/tokyo', branch='feature/r')])
        self.assertEqual(notes, ["tokyo's folder isn't there any more. Its branch feature/r is kept: start gives it a new copy."])


class DraftsCantSetKeptFields(unittest.TestCase):  # Important 3
    def role(self, **kw):
        return {'name': 'b', 'agent': 'claude', 'job': '', 'checkout': 'branch', 'parent': None, 'brief': 'b', **kw}

    def test_validate_rejects_odd_resume(self):
        for bad in ('--dangerously-skip-permissions', '-x', 'a b', 'a;b'):
            with self.assertRaises(newteam.NewTeamError):
                newteam.validate([self.role(resume=bad)])
        newteam.validate([self.role(resume='019a-xyz'), self.role(name='c', resume='abc123')])

    def test_draft_save_drops_kept_fields(self):
        from types import SimpleNamespace
        from assieme import mcptools
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        git_repo = os.path.join(d, 'p')
        os.makedirs(git_repo)
        subprocess.run(['git', '-C', git_repo, 'init', '-q'], check=True)
        subprocess.run(['git', '-C', git_repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q',
                        '--allow-empty', '-m', 'i'], check=True)
        me = SimpleNamespace(_drafts=lambda: os.path.join(d, 'drafts'), opener=lambda: True)
        with mock.patch.dict(os.environ, {'XDG_STATE_HOME': os.path.join(d, 'state')}):
            got = mcptools.Tools.draft_save(me, git_repo, [self.role(worktree='/x', branch='y', resume='z')])
        saved = json.load(open(os.path.join(d, 'drafts', f"{got['id']}.json")))
        self.assertFalse({'worktree', 'branch', 'resume'} & set(saved['roles'][0]))


class UniqueNames(Home):  # Important 4
    def test_same_name_twice_gets_its_branch(self):
        a = self.worktree('tmp/vibe-kanban/worktrees/a', 'vk/a')
        b = self.worktree('tmp/vibe-kanban/worktrees/b', 'vk/b')
        items = [bringover.Found('vibe-kanban', self.repo, 'Retry', a, 'vk/a', ahead=1),
                 bringover.Found('vibe-kanban', self.repo, 'Retry', b, 'vk/b', ahead=1)]
        with mock.patch.object(bringover, 'read_vibe_kanban', return_value=(items, {}, {}, None)):
            got = bringover.find(home=self.home, teams=os.path.join(self.home, 't'), sessions=[], procs=[], herdr_teams=[])
        rows = bringover.workspaces(got['projects'][0]['id'])['rows']
        self.assertEqual(sorted(r['name'] for r in rows), ['Retry (vk/a)', 'Retry (vk/b)'])
        d = bringover.draft(got['projects'][0]['id'], ['Retry (vk/a)', 'Retry (vk/b)'])
        self.assertEqual(len(d['roles']), 2)

    def test_draft_level_is_safe(self):
        a = self.worktree('conductor/workspaces/shop/tokyo', 'feature/t')
        got = bringover.find(home=self.home, teams=os.path.join(self.home, 't'), sessions=[], procs=[], herdr_teams=[])
        d = bringover.draft(got['projects'][0]['id'], ['tokyo'])
        self.assertEqual(d['roles'][0]['level'], 'safe')


class FasterScan(Home):  # Important 5
    def test_repos_from_history_asks_git_once_per_folder(self):
        calls = []
        real = bringover._main_checkout
        with mock.patch.object(bringover, '_main_checkout', side_effect=lambda f, d: calls.append(f) or real(f, d)):
            got = bringover.repos_from_history([{'cwd': self.repo}] * 50)
        self.assertEqual((got, len(calls)), ([self.repo], 1))

    def test_unreadable_repository_is_not_read_again(self):
        a = self.worktree('conductor/workspaces/shop/tokyo', 'feature/t')
        with mock.patch.object(bringover, 'scan', return_value=([], [self.repo])), \
                mock.patch.object(bringover, '_fill') as fill:
            bringover.find(home=self.home, teams=os.path.join(self.home, 't'), sessions=[], procs=[], herdr_teams=[])
        fill.assert_not_called()


class UndoTakesSettingsBack(Home):  # Important 6
    def test_unmerge_removes_only_assieme_entries(self):
        folder = self.worktree('conductor/workspaces/shop/tokyo', 'feature/t')
        os.makedirs(os.path.join(folder, '.gemini'))
        with open(os.path.join(folder, '.gemini', 'settings.json'), 'w') as f:
            json.dump({'theme': 'dark', 'mcpServers': {'mine': {'command': 'x'}},
                       'hooks': {'BeforeAgent': [{'hooks': [{'type': 'command', 'command': 'my-hook'}]}]}}, f)
        git(folder, 'add', '.gemini/settings.json')
        git(folder, 'commit', '-qm', 'settings')
        launch._merge(folder, '.gemini/settings.json', {
            'hooks': {'BeforeAgent': [{'hooks': [{'type': 'command', 'command': 'python -m assieme.hook working'}]}]},
            'mcpServers': {'assieme': {'command': 'py'}}})
        launch._merge(folder, '.agents/hooks.json', {'assieme': {'Stop': []}})
        launch.unmerge(folder)
        data = json.load(open(os.path.join(folder, '.gemini', 'settings.json')))
        self.assertEqual(data, {'theme': 'dark', 'mcpServers': {'mine': {'command': 'x'}},
                                'hooks': {'BeforeAgent': [{'hooks': [{'type': 'command', 'command': 'my-hook'}]}]}})
        self.assertEqual(git(folder, 'ls-files', '-v', '.gemini/settings.json')[:1], 'H')  # skip-worktree taken off
        self.assertEqual(git(folder, 'status', '--porcelain', '.gemini'), '')
        self.assertFalse(os.path.exists(os.path.join(folder, '.agents', 'hooks.json')))  # only ours: removed

    def test_unmerge_cursor_rules(self):
        folder = self.worktree('conductor/workspaces/shop/tokyo', 'feature/t')
        os.makedirs(os.path.join(folder, '.cursor'))
        json.dump({'permissions': {'allow': ['Shell(ls)'], 'deny': []}}, open(os.path.join(folder, '.cursor', 'cli.json'), 'w'))
        launch._cursor_permissions(folder, {'allow': ['Mcp(assieme:*)'], 'deny': ['Shell(git:push)']})
        launch.unmerge(folder)
        self.assertEqual(json.load(open(os.path.join(folder, '.cursor', 'cli.json'))),
                         {'permissions': {'allow': ['Shell(ls)'], 'deny': []}})
        self.assertFalse(os.path.exists(os.path.join(folder, launch.CURSOR_OURS)))


class NeverTheirBranch(KeptReview):  # Minor 8, re-graded Important
    def test_archive_never_deletes_a_branch_outside_assieme(self):
        tip = self.tip()
        shutil.rmtree(self.kept)  # the folder goes during the merge: the role no longer looks brought over
        git(self.repo, 'worktree', 'prune')
        problem = self.r._archive(self.repo, 'demo', 'builder-1', 'feature/x', self.kept, tip)
        self.assertEqual(git(self.repo, 'rev-parse', '--verify', 'feature/x'), tip)
        self.assertIn("isn't Assieme's", problem)


if __name__ == '__main__':
    unittest.main()
