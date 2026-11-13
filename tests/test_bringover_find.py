"""Step 30: what the sheet lists, what each workspace becomes, and the New team draft."""
import json
import os
import unittest
from unittest import mock

from assieme import bringover
from tests.test_bringover import Home, git
from tests.test_bringover_readers import ClaudeSquad, VibeKanban


class Find(Home):
    def setUp(self):
        super().setUp()
        self.tokyo = self.worktree('conductor/workspaces/shop/tokyo', 'feature/retry', commits=4)
        self.lisbon = self.worktree('conductor/workspaces/shop/lisbon', 'fix/tax', commits=1)
        self.osaka = self.worktree('conductor/workspaces/shop/osaka', 'chore/deps', commits=0)
        with open(os.path.join(self.repo, 'conductor.json'), 'w') as f:
            json.dump({'scripts': {'setup': 'npm ci', 'run': 'npm run dev -- --port $CONDUCTOR_PORT'}}, f)
        self.sessions = [{'kind': 'claude', 'cwd': self.tokyo, 'session': 'abc', 'model': 'opus', 'at': 2.0},
                         {'kind': 'codex', 'cwd': self.lisbon, 'session': '019a', 'model': None, 'at': 1.0}]

    def find(self, **kw):
        kw.setdefault('sessions', self.sessions)
        kw.setdefault('procs', [])
        kw.setdefault('herdr_teams', [])
        return bringover.find(home=self.home, teams=os.path.join(self.home, 'teams'), **kw)

    def test_find_groups_by_tool(self):
        got = self.find()
        [p] = got['projects']
        self.assertEqual((p['id'], p['tool'], p['label'], p['running']),
                         (f'conductor:{self.repo}', 'conductor', 'shop', None))
        self.assertEqual(p['summary'], '3 workspaces · Claude Code, Codex · 2 with unmerged work')
        self.assertEqual(got['looked_for'], ['Conductor', 'Vibe Kanban', 'Claude Squad', 'herdr'])

    def test_find_dedupes_reader_and_scan(self):
        got = self.find()
        self.assertEqual(sum(p['tool'] == 'other' for p in got['projects']), 0)
        rows = bringover.workspaces(got['projects'][0]['id'])['rows']
        self.assertEqual(sorted(r['name'] for r in rows), ['lisbon', 'osaka', 'tokyo'])

    def test_find_running_elsewhere(self):
        got = self.find(procs=[{'pid': 1, 'kind': 'claude', 'cwd': self.tokyo}])
        self.assertEqual(got['projects'][0]['running'], 'Still running in Conductor: stop it there first.')

    def test_find_chosen_folder_without_history(self):
        other = self.worktree('code/elsewhere-wt', 'feature/z')
        got = self.find(sessions=[], folder=self.repo)
        self.assertIn(f'conductor:{self.repo}', [p['id'] for p in got['projects']])

    def test_find_herdr_teams_included(self):
        got = self.find(herdr_teams=[{'id': 'saved:default:w1', 'label': 'docs-site', 'where': 'Saved in herdr',
                                      'agents': 2, 'running': False}])
        h = next(p for p in got['projects'] if p['tool'] == 'herdr')
        self.assertEqual((h['id'], h['label'], h['summary']), ('herdr:saved:default:w1', 'docs-site', 'Saved in herdr · 2 agents'))

    def test_find_skips_folders_already_brought_over(self):
        os.makedirs(os.path.join(self.home, 'teams', 'shop'))
        with open(os.path.join(self.home, 'teams', 'shop', 'team.toml'), 'w') as f:
            f.write(f'name = "shop"\nproject = "{self.repo}"\n[[role]]\nname = "builder-1"\nagent = "claude"\n'
                    f'worktree = "{self.tokyo}"\nbranch = "feature/retry"\n')
        rows = bringover.workspaces(self.find()['projects'][0]['id'])['rows']
        self.assertNotIn('tokyo', [r['name'] for r in rows])

    def test_workspaces_rows_and_scripts(self):
        w = bringover.workspaces(self.find()['projects'][0]['id'])
        rows = {r['name']: r for r in w['rows']}
        self.assertEqual(rows['tokyo']['becomes'], 'Becomes builder-1 · Claude Code, opus · 4 commits not merged')
        self.assertEqual(rows['lisbon']['becomes'], 'Becomes builder-2 · Codex · 1 commit not merged')
        self.assertFalse(rows['osaka']['ticked'])
        self.assertEqual(rows['osaka']['note'], 'Left out: nothing new on its branch. Tick it to bring it anyway.')
        self.assertEqual(w['scripts'], [{'which': 'setup', 'line': 'npm ci', 'becomes': 'runs in each new agent’s copy'},
                                        {'which': 'run', 'line': 'npm run dev -- --port ${PORT}',
                                         'becomes': 'becomes the live preview'}])
        self.assertEqual((w['label'], w['tool']), ('shop', 'Conductor'))

    def test_draft_roles_adopt_in_place(self):
        d = bringover.draft(self.find()['projects'][0]['id'], ['tokyo', 'lisbon'])
        self.assertEqual((d['from'], d['tool'], d['project'], d['label']), ('bringover', 'Conductor', self.repo, 'shop'))
        self.assertEqual([(r['name'], r['agent'], r['job'], r['worktree'], r['branch'], r.get('resume')) for r in d['roles']],
                         [('builder-1', 'claude', 'tokyo', self.tokyo, 'feature/retry', 'abc'),
                          ('builder-2', 'codex', 'lisbon', self.lisbon, 'fix/tax', '019a')])
        self.assertEqual(d['scripts'], {'setup': 'npm ci', 'run': 'npm run dev -- --port ${PORT}'})
        self.assertTrue(all(r['brief'] for r in d['roles']))

    def test_draft_unknown_kind_defaults_claude_with_note(self):
        d = bringover.draft(self.find(sessions=[])['projects'][0]['id'], ['tokyo'])
        self.assertEqual(d['roles'][0]['agent'], 'claude')
        self.assertTrue(any('Claude Code' in n['text'] and 'builder-1' in n['agents'] for n in d['notes']))

    def test_draft_permissions_note(self):
        d = bringover.draft(self.find()['projects'][0]['id'], ['tokyo'])
        self.assertTrue(any("safe permissions" in n['text'] for n in d['notes']))

    def test_draft_needs_a_workspace(self):
        with self.assertRaisesRegex(ValueError, 'Tick at least one workspace'):
            bringover.draft(self.find()['projects'][0]['id'], [])

    def test_import_returns_checked_draft(self):
        from types import SimpleNamespace
        from assieme import mcptools
        with mock.patch.dict(os.environ, {'XDG_STATE_HOME': os.path.join(self.home, 'state')}):
            d = mcptools.Tools.bringover_import(SimpleNamespace(), self.find()['projects'][0]['id'], ['tokyo'])
        self.assertEqual((d['from'], len(d['id']), d['why']), ('bringover', 12, ''))
        self.assertIn('practices', d)

    def test_import_herdr_goes_through_herdr(self):
        from types import SimpleNamespace
        from assieme import mcptools
        me = SimpleNamespace(herdr_import=lambda source: {'from': 'herdr', 'source': source})
        self.assertEqual(mcptools.Tools.bringover_import(me, 'herdr:saved:default:w1'),
                         {'from': 'herdr', 'source': 'saved:default:w1'})

    def test_unknown_id(self):
        with self.assertRaisesRegex(ValueError, 'Look again'):
            bringover.workspaces('conductor:/nowhere')


class FromSquad(ClaudeSquad):
    def test_branch_only_gets_fresh_copy_note_and_branch_changed(self):
        a = self.worktree('.claude-squad/worktrees/fix_1f', 'fix-login')
        git(self.repo, 'branch', 'docs')
        git(a, 'checkout', '-q', '-b', 'fix-login-2')
        from tests.test_bringover import commit
        commit(a, 'n.py')
        self.write_state([self.instance('fix', 'claude', 3, a, 'fix-login'),
                          self.instance('docs', 'codex', 3, os.path.join(self.home, 'gone'), 'docs')])
        got = bringover.find(home=self.home, teams=os.path.join(self.home, 't'), sessions=[], procs=[], herdr_teams=[])
        pid = next(p['id'] for p in got['projects'] if p['tool'] == 'claude-squad')
        d = bringover.draft(pid, ['fix', 'docs'])
        fix, docs = d['roles']
        self.assertEqual((fix['worktree'], fix['branch']), (a, 'fix-login-2'))
        self.assertNotIn('worktree', docs)
        self.assertEqual(docs['branch'], 'docs')
        texts = ' '.join(n['text'] for n in d['notes'])
        self.assertIn('fresh copy', texts)
        self.assertIn('fix-login-2', texts)


class FromVibe(VibeKanban):
    def test_draft_cards_and_brief(self):
        self.fill(self.db())
        got = bringover.find(home=self.home, teams=os.path.join(self.home, 't'), sessions=[], procs=[], herdr_teams=[])
        pid = next(p['id'] for p in got['projects'] if p['tool'] == 'vibe-kanban')
        d = bringover.draft(pid, ['Retry with backoff'])
        self.assertIn('Retry with backoff in detail', d['roles'][0]['brief'])
        self.assertEqual(sorted((c['title'], c['status'], c['role']) for c in d['cards']),
                         [('Retry with backoff', 'doing', 'builder-1'), ('Write docs', 'todo', None)])
        self.assertTrue(any('cancelled' in n['text'] for n in d['notes']))


class Starting(Home):
    """What happens at Start for a brought-over team, and its Undo."""

    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {'XDG_STATE_HOME': os.path.join(self.home, 'state')})
        p.start()
        self.addCleanup(p.stop)
        self.tokyo = self.worktree('conductor/workspaces/shop/tokyo', 'feature/retry')
        self.lisbon = self.worktree('conductor/workspaces/shop/lisbon', 'fix/tax')
        self.roles = [{'name': 'builder-1', 'agent': 'claude', 'worktree': self.tokyo, 'branch': 'feature/retry'},
                      {'name': 'builder-2', 'agent': 'codex', 'worktree': self.lisbon, 'branch': 'fix/tax'}]

    def test_start_skips_agent_running_elsewhere(self):
        kept, skipped = bringover.not_running(self.roles, 'Claude Squad',
                                              procs=[{'pid': 1, 'kind': 'claude', 'cwd': self.tokyo}])
        self.assertEqual([r['name'] for r in kept], ['builder-2'])
        self.assertEqual(skipped, [{'role': 'builder-1', 'reason': 'Still running in Claude Squad: stop it there first.'}])

    def test_started_adds_cards_logs_and_remembers_run(self):
        from assieme import activity, tasks
        remembered = []
        cards = [{'title': 'Retry with backoff', 'status': 'doing', 'role': 'builder-1'},
                 {'title': 'Write docs', 'status': 'todo', 'role': None}]
        tasks.fresh('shop', '')
        bringover.started('shop', self.repo, self.roles, cards, {'run': 'npm run dev'},
                          {'tool': 'Conductor', 'label': 'shop'}, lambda p, c: remembered.append((p, c)))
        board = tasks.board('shop')
        self.assertEqual(sorted((c['title'], c['status'], c['role']) for c in board['cards']),
                         [('Retry with backoff', 'doing', 'builder-1'), ('Write docs', 'todo', None)])
        self.assertEqual(remembered, [(self.repo, 'npm run dev')])
        [row] = [r for r in activity.list_rows('shop')['rows'] if 'Brought over' in r['title']]
        self.assertEqual(row['title'], 'Brought over shop from Conductor')
        self.assertIn('Stop', row['undo']['confirm'])

    def test_undo_bringover_leaves_branches_and_folders(self):
        from assieme import activity, newteam
        from tests.test_worktree_kept import tree_hash
        squad = os.path.join(self.home, '.claude-squad')
        os.makedirs(squad)
        open(os.path.join(squad, 'state.json'), 'w').write('{"instances": []}')
        squad_before = open(os.path.join(squad, 'state.json'), 'rb').read()
        os.makedirs(os.path.join(self.tokyo, '.codex', 'rules'))
        open(os.path.join(self.tokyo, '.codex', 'rules', 'assieme.rules'), 'w').write('x')  # Assieme wrote this
        before = tree_hash(self.lisbon)
        folder = os.path.join(newteam.teams_dir(), 'shop')
        newteam.save(self.repo, [r | {'brief': 'b', 'job': 'j', 'checkout': 'branch'} for r in self.roles], {}, 'shop',
                     newteam.teams_dir())
        bringover.started('shop', self.repo, self.roles, [], {}, {'tool': 'Conductor', 'label': 'shop'}, lambda p, c: None)
        [row] = [r for r in activity.list_rows('shop')['rows'] if 'Brought over' in r['title']]
        stopped = []
        rt = mock.Mock()
        with mock.patch.object(activity.team, 'stop', side_effect=lambda rt, name: stopped.append(name) or 0):
            self.assertEqual(activity.undo(row['id'], rt), {'ok': True, 'detail': ''})
        self.assertEqual(stopped, ['shop'])
        self.assertFalse(os.path.exists(folder))
        self.assertFalse(os.path.exists(os.path.join(self.tokyo, '.codex', 'rules', 'assieme.rules')))
        self.assertEqual(tree_hash(self.lisbon), before)
        self.assertEqual(git(self.repo, 'rev-parse', '--verify', '--quiet', 'feature/retry') != '', True)
        self.assertEqual(open(os.path.join(squad, 'state.json'), 'rb').read(), squad_before)


if __name__ == '__main__':
    unittest.main()
