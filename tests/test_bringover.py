"""Step 30: Bring your team over. Every project here is made up and lives in a temporary folder."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from assieme import bringover


def git(cwd, *args):
    return subprocess.run(['git', '-C', cwd, '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def commit(folder, name, text='x\n'):
    with open(os.path.join(folder, name), 'w') as f:
        f.write(text)
    git(folder, 'add', '.')
    git(folder, 'commit', '-qm', f'add {name}')


class Home(unittest.TestCase):
    """A temporary home with a project `shop` and helpers to add worktrees where each tool keeps them."""

    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        self.repo = os.path.join(self.home, 'code', 'shop')
        os.makedirs(self.repo)
        git(self.repo, 'init', '-q', '-b', 'main')
        commit(self.repo, 'README.md')

    def worktree(self, path, branch, commits=1):
        path = os.path.join(self.home, path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        git(self.repo, 'worktree', 'add', '-q', '-b', branch, path)
        for i in range(commits):
            commit(path, f'{branch.replace("/", "-")}-{i}.py')
        return path


class Scan(Home):
    def session(self, folder, kind='claude', session='s-1', model='claude-opus-5-5'):
        return {'kind': kind, 'cwd': folder, 'session': session, 'model': model, 'at': 100.0}

    def test_scan_finds_worktree_with_commits(self):
        tokyo = self.worktree('conductor/workspaces/shop/tokyo', 'feature/retry', commits=2)
        found, unreadable = bringover.scan([self.repo], [], [], home=self.home)
        self.assertEqual(unreadable, [])
        [f] = found
        self.assertEqual((f.tool, f.project, f.name, f.folder, f.branch, f.ahead, f.dirty),
                         ('conductor', self.repo, 'tokyo', tokyo, 'feature/retry', 2, False))

    def test_scan_skips_clean_and_main_and_assieme_copies(self):
        self.worktree('conductor/workspaces/shop/clean', 'feature/clean', commits=0)
        self.worktree('code/shop/.assieme/worktrees/builder', 'assieme/shop/builder')
        found, _ = bringover.scan([self.repo], [], [], home=self.home)
        self.assertEqual(found, [])

    def test_scan_counts_uncommitted_work(self):
        osaka = self.worktree('conductor/workspaces/shop/osaka', 'feature/o', commits=0)
        open(os.path.join(osaka, 'wip.py'), 'w').write('x')
        [f] = bringover.scan([self.repo], [], [], home=self.home)[0]
        self.assertEqual((f.ahead, f.dirty), (0, True))

    def test_scan_other_needs_agent_evidence(self):
        mine = self.worktree('code/shop-feature', 'feature/mine')  # the person's own worktree: no agent ran there
        self.assertEqual(bringover.scan([self.repo], [], [], home=self.home)[0], [])
        [f] = bringover.scan([self.repo], [self.session(mine, 'codex', 'c-9', 'gpt-5.5')], [], home=self.home)[0]
        self.assertEqual((f.tool, f.kind, f.session, f.model), ('other', 'codex', 'c-9', 'gpt-5.5'))

    def test_scan_kind_and_session_from_history(self):
        tokyo = self.worktree('conductor/workspaces/shop/tokyo', 'feature/retry')
        older = self.session(tokyo, session='old') | {'at': 50.0}
        [f] = bringover.scan([self.repo], [older, self.session(tokyo)], [], home=self.home)[0]
        self.assertEqual((f.kind, f.session, f.model), ('claude', 's-1', 'claude-opus-5-5'))

    def test_scan_running_elsewhere(self):
        tokyo = self.worktree('conductor/workspaces/shop/tokyo', 'feature/retry')
        procs = [{'pid': 10, 'kind': 'claude', 'cwd': os.path.join(tokyo, 'src')}]
        [f] = bringover.scan([self.repo], [], procs, home=self.home)[0]
        self.assertTrue(f.running_elsewhere)
        self.assertEqual(f.kind, 'claude')

    def test_scan_timeout(self):
        real = subprocess.run

        def slow(args, **kw):
            if 'worktree' in args:
                raise subprocess.TimeoutExpired(args, kw.get('timeout'))
            return real(args, **kw)
        with mock.patch.object(bringover.subprocess, 'run', side_effect=slow):
            found, unreadable = bringover.scan([self.repo], [], [], home=self.home)
        self.assertEqual((found, unreadable), ([], [self.repo]))

    def test_scan_not_a_repository(self):
        found, unreadable = bringover.scan([os.path.join(self.home, 'nowhere')], [], [], home=self.home)
        self.assertEqual((found, unreadable), ([], [os.path.join(self.home, 'nowhere')]))

    def test_tool_of(self):
        h = self.home
        self.assertEqual(bringover.tool_of(f'{h}/conductor/workspaces/r/tokyo', h), 'conductor')
        self.assertEqual(bringover.tool_of(f'{h}/code/r/.conductor/tokyo', h), 'conductor')
        self.assertEqual(bringover.tool_of(f'{h}/.claude-squad/worktrees/x_1f', h), 'claude-squad')
        self.assertEqual(bringover.tool_of('/var/folders/ab/T/vibe-kanban/worktrees/a', h), 'vibe-kanban')
        self.assertEqual(bringover.tool_of(f'{h}/code/x', h), 'other')


class History(Home):
    def test_history_reads_claude_and_codex(self):
        now = datetime(2026, 11, 13, tzinfo=timezone.utc)
        claude = os.path.join(self.home, '.claude', 'projects', '-x-tokyo')
        os.makedirs(claude)
        with open(os.path.join(claude, 'abc-123.jsonl'), 'w') as f:
            f.write(json.dumps({'type': 'user', 'cwd': '/x/tokyo'}) + '\n')
            f.write(json.dumps({'type': 'assistant', 'cwd': '/x/tokyo', 'message': {'model': 'claude-opus-5-5'}}) + '\n')
        codex = os.path.join(self.home, '.codex', 'sessions', '2026', '11', '12')
        os.makedirs(codex)
        with open(os.path.join(codex, 'rollout-1.jsonl'), 'w') as f:
            f.write(json.dumps({'type': 'session_meta', 'payload': {'id': '019a', 'cwd': '/x/lisbon'}}) + '\n')
            f.write(json.dumps({'type': 'turn_context', 'payload': {'model': 'gpt-5.5'}}) + '\n')
        stamp = now.timestamp() - 3600
        for p in (os.path.join(claude, 'abc-123.jsonl'), os.path.join(codex, 'rollout-1.jsonl')):
            os.utime(p, (stamp, stamp))
        got = sorted(bringover.history(os.path.join(self.home, '.claude', 'projects'),
                                       os.path.join(self.home, '.codex', 'sessions'), now), key=lambda s: s['kind'])
        self.assertEqual([(s['kind'], s['cwd'], s['session'], s['model']) for s in got],
                         [('claude', '/x/tokyo', 'abc-123', 'claude-opus-5-5'), ('codex', '/x/lisbon', '019a', 'gpt-5.5')])

    def test_history_skips_old_sessions(self):
        now = datetime(2026, 11, 13, tzinfo=timezone.utc)
        claude = os.path.join(self.home, 'p', '-x')
        os.makedirs(claude)
        path = os.path.join(claude, 'old.jsonl')
        open(path, 'w').write(json.dumps({'cwd': '/x'}) + '\n')
        stamp = now.timestamp() - 40 * 86400
        os.utime(path, (stamp, stamp))
        self.assertEqual(bringover.history(os.path.join(self.home, 'p'), os.path.join(self.home, 'none'), now), [])

    def test_repos_from_history(self):
        tokyo = self.worktree('conductor/workspaces/shop/tokyo', 'feature/retry')
        sessions = [{'cwd': tokyo}, {'cwd': os.path.join(self.repo, 'src')}, {'cwd': '/nowhere/at/all'}]
        os.makedirs(os.path.join(self.repo, 'src'))
        self.assertEqual(bringover.repos_from_history(sessions), [self.repo])


if __name__ == '__main__':
    unittest.main()
