"""Step 30: readers for Claude Squad, Vibe Kanban and Conductor, against made-up files in a temporary home."""
import json
import os
import sqlite3
import unittest
import uuid

from assieme import bringover
from tests.test_bringover import Home, commit, git


class ClaudeSquad(Home):
    def write_state(self, instances):
        os.makedirs(os.path.join(self.home, '.claude-squad'), exist_ok=True)
        with open(os.path.join(self.home, '.claude-squad', 'state.json'), 'w') as f:
            json.dump({'help_screens_seen': 0, 'instances': instances}, f)

    def instance(self, title, program, status, folder, branch):
        return {'title': title, 'path': self.repo, 'branch': branch, 'status': status, 'program': program,
                'worktree': {'repo_path': self.repo, 'worktree_path': folder, 'session_name': title,
                             'branch_name': branch, 'base_commit_sha': 'abc'}}

    def test_claude_squad_instances(self):
        a = self.worktree('.claude-squad/worktrees/fix-login_1f', 'fix-login')
        b = os.path.join(self.home, '.claude-squad', 'worktrees', 'docs_2a')  # paused: its folder is gone
        git(self.repo, 'branch', 'docs')
        self.write_state([self.instance('fix-login', 'claude', 1, a, 'fix-login'),
                          self.instance('docs', 'codex --model gpt-5.5', 3, b, 'docs')])
        found, problem = bringover.read_claude_squad(self.home)
        self.assertIsNone(problem)
        self.assertEqual([(f.tool, f.name, f.folder, f.branch, f.kind, f.running_elsewhere, f.project) for f in found],
                         [('claude-squad', 'fix-login', a, 'fix-login', 'claude', True, self.repo),
                          ('claude-squad', 'docs', None, 'docs', 'codex', False, self.repo)])

    def test_claude_squad_other_program_noted(self):
        a = self.worktree('.claude-squad/worktrees/x_1', 'x')
        self.write_state([self.instance('x', 'aider --yes', 3, a, 'x')])
        [f], _ = bringover.read_claude_squad(self.home)
        self.assertIsNone(f.kind)
        self.assertIn('aider', f.notes[0])

    def test_claude_squad_none(self):
        self.assertEqual(bringover.read_claude_squad(self.home), ([], None))

    def test_claude_squad_unreadable(self):
        os.makedirs(os.path.join(self.home, '.claude-squad'))
        open(os.path.join(self.home, '.claude-squad', 'state.json'), 'w').write('{not json')
        self.assertEqual(bringover.read_claude_squad(self.home),
                         ([], "Couldn't read Claude Squad's sessions. Its worktrees are still listed under Other agent work."))


class VibeKanban(Home):
    def db(self, drop=None):
        folder = os.path.join(self.home, 'Library', 'Application Support', 'ai.bloop.vibe-kanban')
        os.makedirs(folder, exist_ok=True)
        self.db_path = os.path.join(folder, 'db.v2.sqlite')
        c = sqlite3.connect(self.db_path)
        tables = {
            'projects': 'id BLOB, name TEXT',
            'repos': 'id BLOB, path TEXT, name TEXT, setup_script TEXT, archive_script TEXT, dev_server_script TEXT',
            'tasks': 'id BLOB, project_id BLOB, title TEXT, description TEXT, status TEXT',
            'workspaces': 'id BLOB, task_id BLOB, container_ref TEXT, branch TEXT, archived INTEGER, '
                          'worktree_deleted INTEGER, name TEXT, created_at TEXT',
            'workspace_repos': 'workspace_id BLOB, repo_id BLOB',
            'sessions': 'id BLOB, workspace_id BLOB, executor TEXT, created_at TEXT',
            'execution_processes': 'id BLOB, session_id BLOB',
            'coding_agent_turns': 'id BLOB, execution_process_id BLOB, agent_session_id TEXT, created_at TEXT'}
        for t, cols in tables.items():
            if drop and t == drop[0]:
                cols = ', '.join(x for x in cols.split(', ') if not x.startswith(drop[1]))
            c.execute(f'CREATE TABLE {t} ({cols})')
        return c

    def fill(self, c):
        u = lambda: uuid.uuid4().bytes
        p, r = u(), u()
        c.execute('INSERT INTO projects VALUES (?,?)', (p, 'shop'))
        c.execute('INSERT INTO repos VALUES (?,?,?,?,?,?)', (r, self.repo, 'shop', 'npm ci', './x.sh', 'npm run dev'))
        folder = self.worktree('tmp/vibe-kanban/worktrees/a1b2', 'vk/a1b2-retry')
        rows = []
        for title, status, ws_name in (('Retry with backoff', 'inprogress', 'a1b2'), ('Old idea', 'cancelled', None),
                                       ('Write docs', 'todo', None)):
            t = u()
            c.execute('INSERT INTO tasks VALUES (?,?,?,?,?)', (t, p, title, f'{title} in detail', status))
            rows.append((t, ws_name))
        w, s, e = u(), u(), u()
        c.execute('INSERT INTO workspaces VALUES (?,?,?,?,?,?,?,?)',
                  (w, rows[0][0], folder, 'vk/a1b2-retry', 0, 0, None, '2026-11-12'))
        c.execute('INSERT INTO workspaces VALUES (?,?,?,?,?,?,?,?)',
                  (u(), rows[1][0], '/gone', 'vk/old', 1, 1, None, '2026-10-01'))  # archived: left out
        c.execute('INSERT INTO workspace_repos VALUES (?,?)', (w, r))
        c.execute('INSERT INTO sessions VALUES (?,?,?,?)', (s, w, 'CLAUDE_CODE', '2026-11-12'))
        c.execute('INSERT INTO execution_processes VALUES (?,?)', (e, s))
        c.execute('INSERT INTO coding_agent_turns VALUES (?,?,?,?)', (u(), e, 'old-id', '2026-11-12T01'))
        c.execute('INSERT INTO coding_agent_turns VALUES (?,?,?,?)', (u(), e, 'new-id', '2026-11-12T02'))
        c.commit()
        c.close()
        return folder

    def test_vibe_tasks_and_workspaces(self):
        folder = self.fill(self.db())
        found, tasks, scripts, problem = bringover.read_vibe_kanban(self.home)
        self.assertIsNone(problem)
        [f] = found
        self.assertEqual((f.tool, f.project, f.name, f.folder, f.branch, f.kind, f.session),
                         ('vibe-kanban', self.repo, 'Retry with backoff', folder, 'vk/a1b2-retry', 'claude', 'new-id'))
        self.assertEqual(sorted((t['title'], t['status'], t['branch']) for t in tasks[self.repo]),
                         [('Old idea', 'cancelled', None), ('Retry with backoff', 'doing', 'vk/a1b2-retry'),
                          ('Write docs', 'todo', None)])
        self.assertEqual(scripts, {self.repo: {'setup': 'npm ci', 'run': 'npm run dev', 'archive': './x.sh'}})

    def test_vibe_opens_read_only(self):
        self.fill(self.db())
        before = (os.stat(self.db_path).st_mtime_ns, open(self.db_path, 'rb').read())
        bringover.read_vibe_kanban(self.home)
        self.assertEqual((os.stat(self.db_path).st_mtime_ns, open(self.db_path, 'rb').read()), before)
        self.assertFalse(os.path.exists(self.db_path + '-journal'))

    def test_vibe_missing_columns(self):
        self.db(drop=('workspaces', 'container_ref')).close()
        self.assertEqual(bringover.read_vibe_kanban(self.home),
                         ([], {}, {}, "This Vibe Kanban version isn't supported yet. Its worktrees are still listed "
                                      "under Other agent work."))

    def test_vibe_none(self):
        self.assertEqual(bringover.read_vibe_kanban(self.home), ([], {}, {}, None))


class Conductor(Home):
    def test_conductor_layout_and_scripts(self):
        tokyo = self.worktree('conductor/workspaces/shop/tokyo', 'feature/retry')
        older = self.worktree('code/shop/.conductor/lisbon', 'fix/tax')
        with open(os.path.join(self.repo, 'conductor.json'), 'w') as f:
            json.dump({'scripts': {'setup': 'npm ci', 'run': 'npm run dev -- --port $CONDUCTOR_PORT',
                                   'archive': './x.sh'}}, f)
        commit(self.repo, 'conductor.json', open(os.path.join(self.repo, 'conductor.json')).read())
        found, scripts, problem = bringover.read_conductor(self.home)
        self.assertIsNone(problem)
        self.assertEqual(sorted((f.tool, f.name, f.folder, f.branch, f.project) for f in found),
                         [('conductor', 'lisbon', older, 'fix/tax', self.repo),
                          ('conductor', 'tokyo', tokyo, 'feature/retry', self.repo)])
        self.assertEqual(scripts, {self.repo: {'setup': 'npm ci', 'run': 'npm run dev -- --port $CONDUCTOR_PORT',
                                               'archive': './x.sh'}})

    def test_conductor_none(self):
        self.assertEqual(bringover.read_conductor(self.home), ([], {}, None))


if __name__ == '__main__':
    unittest.main()
