"""Review and merge, with the real git command in temporary repositories. Nothing here reaches a real remote: pull
requests use a fake runner or a local bare repository."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import types
import unittest

from assieme import review, serve, team as team_mod
from assieme.review import Review, ReviewError, test_command as command_for, test_result as result_of


def git(cwd, *args):
    return subprocess.run(['git', '-C', cwd, *args], capture_output=True, text=True, check=True).stdout.strip()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)


class FakeRt:
    def __init__(self):
        self.rows, self.killed, self.sent = [], [], []

    def list(self, labels, all=False):
        return self.rows

    def kill(self, t):
        self.killed.append(t)
        for r in self.rows:
            if r['address'] == t:
                r['state'] = 'finished'

    def send(self, t, text):
        self.sent.append((t, text))

    def agent(self, role, status='idle', address='t1'):
        self.rows.append({'address': address, 'state': 'live', 'status': {'state': status}, 'created_ms': 1,
                          'labels': {team_mod.TEAM: 'demo', team_mod.ROLE: role}})


def sync_thread(target, daemon=True):
    return types.SimpleNamespace(start=target)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.repo = os.path.join(self.root, 'shop')
        os.makedirs(self.repo)
        git(self.repo, 'init', '-q', '-b', 'main')
        git(self.repo, 'config', 'user.name', 't')
        git(self.repo, 'config', 'user.email', 't@t')
        write(f'{self.repo}/a.py', 'one\ntwo\nthree\n')
        write(f'{self.repo}/b.py', 'b\n')
        write(f'{self.repo}/.git/info/exclude', '.assieme/\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-qm', 'init')
        self.teams = os.path.join(self.root, 'teams')
        write(f'{self.teams}/demo/team.toml', f'name = "demo"\nproject = "{self.repo}"\n')
        self.rt = FakeRt()
        self.log = os.path.join(self.root, 'actions.jsonl')
        self.r = Review(self.rt, self.teams, thread=sync_thread, log=self.log, which=lambda c: None,
                        claude_dir=f'{self.root}/claude', codex_dir=f'{self.root}/codex')

    def wt(self, role):
        return f'{self.repo}/.assieme/worktrees/{role}'

    def branch(self, role, files=None, message='work'):
        """An agent's worktree and branch, with a commit when `files` is given."""
        if not os.path.isdir(self.wt(role)):
            git(self.repo, 'worktree', 'add', '-q', '-b', f'assieme/demo/{role}', self.wt(role))
        for name, text in (files or {}).items():
            write(f'{self.wt(role)}/{name}', text)
        if files:
            git(self.wt(role), 'add', '.')
            git(self.wt(role), 'commit', '-qm', message)
        return git(self.wt(role), 'rev-parse', 'HEAD')

    def main_tip(self):
        return git(self.repo, 'rev-parse', 'refs/heads/main')

    def check(self, data, id_):
        return next(c for c in data['checks'] if c['id'] == id_)


class Reading(Base):
    def test_handlers_reach_the_brain(self):
        self.branch('builder-1', {'a.py': 'A\n'})
        got = serve.handle({'id': 1, 'method': 'review.get', 'params': {'team': 'demo', 'role': 'builder-1'}}, self.r.handlers())
        self.assertEqual(got['result']['role'], 'builder-1')
        got = serve.handle({'id': 1, 'method': 'review.get', 'params': {'team': 'demo', 'role': 'nobody'}}, self.r.handlers())
        self.assertIn('no branch', got['error']['message'])

    def test_waiting_lists_only_branches_ahead_of_main(self):
        self.branch('builder-1', {'a.py': 'one\nTWO\nthree\n'})
        self.branch('builder-2')
        got = self.r.waiting()
        self.assertEqual([(w['team'], w['project'], w['role'], w['ahead']) for w in got], [('demo', 'shop', 'builder-1', 1)])

    def test_waiting_finds_a_command_line_team_through_its_terminal(self):
        shutil.rmtree(f'{self.teams}/demo')
        self.branch('builder-1', {'a.py': 'x\n'})
        self.rt.rows.append({'address': 't1', 'state': 'finished', 'cwd': self.wt('builder-1'),
                             'labels': {team_mod.TEAM: 'demo', team_mod.ROLE: 'builder-1'}})
        self.assertEqual(self.r.waiting()[0]['role'], 'builder-1')

    def test_get_gives_files_counts_and_diff(self):
        tip = self.branch('builder-1', {'a.py': 'one\nTWO\nthree\n', 'new.py': 'n\nm\n'})
        got = self.r.get('demo', 'builder-1')
        self.assertEqual((got['tip'], got['base'], got['branch'], got['ahead']), (tip, 'main', 'assieme/demo/builder-1', 1))
        self.assertEqual([(f['path'], f['add'], f['del']) for f in got['files']], [('a.py', 1, 1), ('new.py', 2, 0)])
        self.assertIn('+TWO', got['diff'])

    def test_a_rename_lists_the_new_path(self):
        self.branch('builder-1')
        git(self.wt('builder-1'), 'mv', 'a.py', 'c.py')
        git(self.wt('builder-1'), 'commit', '-qm', 'rename')
        self.assertEqual([f['path'] for f in self.r.get('demo', 'builder-1')['files']], ['c.py'])

    def test_a_binary_file_is_marked(self):
        self.branch('builder-1')
        with open(f"{self.wt('builder-1')}/x.bin", 'wb') as f:
            f.write(b'\0\1\2')
        git(self.wt('builder-1'), 'add', '.')
        git(self.wt('builder-1'), 'commit', '-qm', 'bin')
        self.assertTrue(self.r.get('demo', 'builder-1')['files'][0]['binary'])

    def test_too_big_a_diff_is_left_out(self):
        self.branch('builder-1', {'big.py': 'x\n' * (review.MAX_DIFF // 2 + 10)})
        got = self.r.get('demo', 'builder-1')
        self.assertTrue(got['too_big'])
        self.assertEqual(got['diff'], '')
        self.assertEqual(len(got['files']), 1)

    def test_base_falls_back_from_main_to_master_to_current(self):
        self.assertEqual(self.r._base(self.repo), 'main')
        git(self.repo, 'branch', '-m', 'main', 'master')
        self.assertEqual(self.r._base(self.repo), 'master')
        git(self.repo, 'branch', '-m', 'master', 'trunk')
        self.assertEqual(self.r._base(self.repo), 'trunk')

    def test_base_prefers_origin_head(self):
        git(self.repo, 'branch', 'develop')
        git(self.repo, 'update-ref', 'refs/remotes/origin/develop', 'develop')
        git(self.repo, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/develop')
        self.assertEqual(self.r._base(self.repo), 'develop')

    def test_names_are_checked(self):
        for bad in ('../x', 'a/b', '', None):
            with self.assertRaises(ReviewError):
                self.r.get('demo', bad)
        with self.assertRaises(ReviewError):
            self.r.get('../demo', 'builder-1')

    def test_no_branch_says_so(self):
        with self.assertRaisesRegex(ReviewError, 'no branch'):
            self.r.get('demo', 'nobody')

    def test_conflicts_are_named(self):
        self.branch('builder-1', {'a.py': 'one\nMINE\nthree\n'})
        self.assertEqual(self.check(self.r.get('demo', 'builder-1'), 'conflicts')['state'], 'ok')
        write(f'{self.repo}/a.py', 'one\nTHEIRS\nthree\n')
        git(self.repo, 'commit', '-qam', 'theirs')
        c = self.check(self.r.get('demo', 'builder-1'), 'conflicts')
        self.assertEqual(c['state'], 'bad')
        self.assertIn('a.py', c['text'])

    def test_overlap_with_another_agent_is_named(self):
        self.branch('builder-1', {'a.py': 'A\n'})
        self.assertEqual(self.check(self.r.get('demo', 'builder-1'), 'own-files')['text'], 'Only touched its own files')
        self.branch('builder-2', {'a.py': 'B\n', 'b.py': 'B\n'})
        c = self.check(self.r.get('demo', 'builder-1'), 'own-files')
        self.assertEqual((c['state'], c['text']), ('warn', 'builder-2 also changed a.py'))

    def test_overlap_finds_non_ascii_names(self):
        self.branch('builder-1', {'caf\u00e9.py': 'A\n'})
        self.branch('builder-2', {'caf\u00e9.py': 'B\n'})
        self.assertEqual(self.check(self.r.get('demo', 'builder-1'), 'own-files')['text'], 'builder-2 also changed caf\u00e9.py')

    def test_uncommitted_changes_only_appear_when_true(self):
        self.branch('builder-1', {'a.py': 'A\n'})
        self.assertEqual([c['id'] for c in self.r.get('demo', 'builder-1')['checks']], ['tests', 'conflicts', 'own-files'])
        write(f"{self.wt('builder-1')}/a.py", 'dirty\n')
        self.assertEqual(self.check(self.r.get('demo', 'builder-1'), 'uncommitted')['state'], 'bad')

    def test_agent_state(self):
        self.branch('builder-1', {'a.py': 'A\n'})
        self.assertFalse(self.r.get('demo', 'builder-1')['agent']['live'])
        self.rt.agent('builder-1', 'working')
        agent = self.r.get('demo', 'builder-1')['agent']
        self.assertEqual((agent['live'], agent['working'], agent['address']), (True, True, 't1'))


class Tests(Base):
    def test_detects_the_command_from_the_folder(self):
        d = os.path.join(self.root, 'p')
        os.makedirs(d)
        self.assertIsNone(command_for(d))
        for name, cmd in (('Makefile', None), ('go.mod', ['go', 'test', './...']), ('Cargo.toml', ['cargo', 'test']),
                          ('pyproject.toml', ['python3', '-m', 'pytest', '-q']), ('uv.lock', ['uv', 'run', 'pytest', '-q'])):
            write(f'{d}/{name}', 'all:\n\techo\n')
            self.assertEqual(command_for(d), cmd, name)

    def test_makefile_with_a_test_target(self):
        d = os.path.join(self.root, 'p')
        write(f'{d}/Makefile', 'test:\n\tpytest\n')
        self.assertEqual(command_for(d), ['make', 'test'])

    def test_package_json_needs_a_real_script_and_its_modules(self):
        d = os.path.join(self.root, 'p')
        write(f'{d}/package.json', '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}')
        os.makedirs(f'{d}/node_modules')
        self.assertIsNone(command_for(d))
        write(f'{d}/package.json', '{"scripts": {"test": "vitest"}}')
        self.assertEqual(command_for(d), ['npm', 'test', '--silent'])
        os.rmdir(f'{d}/node_modules')
        self.assertIsNone(command_for(d))
        write(f'{d}/package.json', 'not json')
        self.assertIsNone(command_for(d))

    def test_results_in_words(self):
        py = ['python3', '-m', 'pytest', '-q']
        self.assertEqual(result_of(py, 0, '212 passed in 3s')['text'], 'Tests pass, 212 of 212')
        self.assertEqual(result_of(['go'], 0, 'ok')['text'], 'Tests pass')
        bad = result_of(py, 1, 'E boom\n3 failed, 209 passed in 3s')
        self.assertEqual((bad['state'], bad['text']), ('bad', 'Tests fail, 3 of 212'))
        self.assertIn('boom', bad['detail'])
        self.assertEqual(result_of(py, 1, 'No module named pytest')['state'], 'none')
        self.assertEqual(result_of(py, 5, 'no tests ran')['text'], 'No tests found')
        self.assertEqual(result_of(['npm'], 0, 'Test Files  2 passed\nTests  212 passed')['text'], 'Tests pass, 212 of 212')
        self.assertEqual(result_of(['cargo', 'test'], 0, 'test result: ok. 5 passed; 0 failed\ntest result: ok. 7 passed; 0 failed')['text'],
                         'Tests pass, 12 of 12')

    def test_no_command_shows_couldnt_run(self):
        self.branch('builder-1', {'a.py': 'A\n'})
        self.assertEqual(self.check(self.r.get('demo', 'builder-1'), 'tests')['text'], "Couldn't run the tests")

    def test_runs_once_per_commit_in_the_worktree(self):
        calls = []

        def run(cmd, **kw):
            if cmd[0] == 'python3':
                calls.append((cmd, kw['cwd'], kw['env']['CI']))
                return types.SimpleNamespace(returncode=0, stdout='2 passed', stderr='')
            return subprocess.run(cmd, **kw)
        self.r.run = run
        self.branch('builder-1', {'a.py': 'A\n', 'pytest.ini': '[pytest]\n'})
        got = self.r.get('demo', 'builder-1')
        self.assertEqual(self.check(got, 'tests')['text'], 'Tests pass, 2 of 2')
        self.r.get('demo', 'builder-1')
        tip = self.r.get('demo', 'builder-1')['tip']
        self.assertEqual(calls, [(['python3', '-m', 'pytest', '-q'], f'{self.repo}/.assieme/test-runs/{tip}', '1')])
        self.branch('builder-1', {'a.py': 'B\n'})
        self.r.get('demo', 'builder-1')
        self.assertEqual(len(calls), 2)

    def test_what_tests_generate_stays_out_of_the_agents_worktree(self):
        seen = {}

        def run(cmd, **kw):
            if cmd[0] == 'python3':
                seen['dep'] = os.path.islink(f"{kw['cwd']}/.venv")
                write(f"{kw['cwd']}/coverage.xml", 'generated\n')
                seen['commit'] = git(kw['cwd'], 'rev-parse', 'HEAD')
                return types.SimpleNamespace(returncode=0, stdout='1 passed', stderr='')
            return subprocess.run(cmd, **kw)
        self.r.run = run
        tip = self.branch('builder-1', {'pytest.ini': '[pytest]\n'})
        os.makedirs(f"{self.wt('builder-1')}/.venv")
        write(f'{self.repo}/.git/info/exclude', '.assieme/\n.venv\n')
        got = self.r.get('demo', 'builder-1')
        self.assertEqual((seen['commit'], seen['dep']), (tip, True))
        self.assertEqual(self.check(got, 'tests')['state'], 'ok')
        self.assertFalse(os.path.exists(f"{self.wt('builder-1')}/coverage.xml"))
        self.assertNotIn('uncommitted', [c['id'] for c in self.r.get('demo', 'builder-1')['checks']])
        self.assertEqual(os.listdir(f'{self.repo}/.assieme/test-runs'), [])
        self.assertEqual(git(self.repo, 'worktree', 'list').count('\n'), 1)

    def test_a_real_failure_mentioning_a_missing_command_is_still_a_failure(self):
        py = ['python3', '-m', 'pytest', '-q']
        self.assertEqual(result_of(py, 1, 'sh: foo: command not found\n1 failed, 2 passed')['state'], 'bad')
        self.assertEqual(result_of(py, 0, 'sh: foo: command not found\n3 passed')['state'], 'ok')
        self.assertEqual(result_of(py, 1, 'sh: pytest: command not found')['state'], 'none')

    def test_opening_review_again_retries_a_failed_run_only(self):
        runs, codes = [], [1, 0]

        def run(cmd, **kw):
            if cmd[0] == 'python3':
                runs.append(cmd)
                return types.SimpleNamespace(returncode=codes[len(runs) - 1], stdout='', stderr='')
            return subprocess.run(cmd, **kw)
        self.r.run = run
        self.branch('builder-1', {'pytest.ini': '[pytest]\n'})
        self.assertEqual(self.check(self.r.get('demo', 'builder-1'), 'tests')['state'], 'bad')
        self.assertEqual(self.check(self.r.get('demo', 'builder-1'), 'tests')['state'], 'bad')
        self.assertEqual(len(runs), 1)
        self.assertEqual(self.check(self.r.get('demo', 'builder-1', retest=True), 'tests')['state'], 'ok')
        self.r.get('demo', 'builder-1', retest=True)
        self.assertEqual(len(runs), 2)

    def test_shows_running_until_the_thread_finishes(self):
        waiting = []
        self.r.thread = lambda target, daemon=True: types.SimpleNamespace(start=lambda: waiting.append(target))
        real = self.r.run
        self.r.run = lambda cmd, **kw: (types.SimpleNamespace(returncode=1, stdout='1 failed', stderr='')
                                        if cmd[0] == 'python3' else real(cmd, **kw))
        self.branch('builder-1', {'pytest.ini': '[pytest]\n'})
        self.assertEqual(self.check(self.r.get('demo', 'builder-1'), 'tests')['state'], 'running')
        waiting[0]()
        self.assertEqual(self.check(self.r.get('demo', 'builder-1'), 'tests')['state'], 'bad')

    def test_a_timeout_and_a_missing_program_are_reported(self):
        real = self.r.run

        def timeout(cmd, **kw):
            if cmd[0] == 'python3':
                raise subprocess.TimeoutExpired(cmd, 1)
            return real(cmd, **kw)
        self.r.run = timeout
        self.branch('builder-1', {'pytest.ini': '[pytest]\n'})
        self.assertIn('15 minutes', self.check(self.r.get('demo', 'builder-1'), 'tests')['text'])
        self.r.tests.clear()

        def missing(cmd, **kw):
            if cmd[0] == 'python3':
                raise FileNotFoundError(cmd[0])
            return real(cmd, **kw)
        self.r.run = missing
        self.assertEqual(self.check(self.r.get('demo', 'builder-1'), 'tests')['state'], 'none')


class Notes(Base):
    def notes(self, *lines, author='reviewer'):
        """What a Claude Code reviewer said, in a transcript as Claude Code keeps it."""
        os.makedirs(self.wt(author), exist_ok=True)
        slug = re.sub(r'[^A-Za-z0-9]', '-', self.wt(author))
        text = '\n'.join(f'ASSIEME-NOTE {x}' for x in lines)
        write(f'{self.root}/claude/{slug}/s.jsonl',
              json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': f'Verdict: changes needed.\n{text}'}]}}) + '\n'
              + json.dumps({'type': 'user', 'message': {'content': 'ASSIEME-NOTE ' + self.line(text='from the brief')}}) + '\n')

    def line(self, **over):
        return json.dumps({'branch': 'builder-1', 'file': 'a.py', 'line': 2, 'text': 'cap it', **over})

    def test_reads_notes_and_skips_bad_lines(self):
        self.branch('builder-1', {'a.py': 'A\n'})
        self.notes('not json', '[1]', self.line(), self.line(line='two'), self.line(text='  '), '{"branch": "builder-1"}',
                   self.line(branch='builder-2', text='other'), self.line(branch='assieme/demo/builder-1', text='long name'))
        got = self.r.get('demo', 'builder-1')['notes']
        self.assertEqual([(n['author'], n['file'], n['line'], n['text']) for n in got],
                         [('reviewer', 'a.py', 2, 'cap it'), ('reviewer', 'a.py', 2, 'long name')])

    def test_reads_a_codex_reviewers_notes_only_from_its_own_folder(self):
        self.branch('builder-1', {'a.py': 'A\n'})
        os.makedirs(self.wt('reviewer'))

        def session(name, cwd):
            write(f'{self.root}/codex/2026/{name}.jsonl',
                  json.dumps({'type': 'session_meta', 'payload': {'cwd': cwd}}) + '\n'
                  + json.dumps({'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [
                      {'type': 'output_text', 'text': f'ASSIEME-NOTE {self.line(text=name)}'}]}}) + '\n')
        session('mine', self.wt('reviewer'))
        session('another-project', self.root)
        self.assertEqual([n['text'] for n in self.r.get('demo', 'builder-1')['notes']], ['mine'])

    def test_ignore_hides_a_note(self):
        self.branch('builder-1', {'a.py': 'A\n'})
        self.notes(self.line())
        note = self.r.get('demo', 'builder-1')['notes'][0]
        self.r.note('demo', 'builder-1', note['id'], 'ignore')
        self.assertEqual(self.r.get('demo', 'builder-1')['notes'], [])
        self.assertEqual(self.rt.sent, [])

    def test_fix_sends_it_to_the_agent_and_hides_it(self):
        self.branch('builder-1', {'a.py': 'A\n'})
        self.rt.agent('builder-1')
        self.notes(self.line())
        note = self.r.get('demo', 'builder-1')['notes'][0]
        self.r.note('demo', 'builder-1', note['id'], 'fix')
        self.assertIn('reviewer on a.py line 2: cap it', self.rt.sent[0][1])
        self.assertEqual(self.r.get('demo', 'builder-1')['notes'], [])

    def test_a_changed_note_and_unknown_action_refuse(self):
        self.branch('builder-1', {'a.py': 'A\n'})
        self.notes(self.line())
        with self.assertRaisesRegex(ReviewError, 'changed'):
            self.r.note('demo', 'builder-1', 'gone', 'ignore')
        note = self.r.get('demo', 'builder-1')['notes'][0]
        with self.assertRaisesRegex(ReviewError, 'Unknown'):
            self.r.note('demo', 'builder-1', note['id'], 'delete')

    def test_a_merged_branchs_notes_stay_gone_for_the_next_branch(self):
        tip = self.branch('builder-1', {'a.py': 'A\n'})
        self.notes(self.line())
        self.r.merge('demo', 'builder-1', tip)
        self.branch('builder-1', {'z.py': 'z\n'})
        self.assertEqual(self.r.get('demo', 'builder-1')['notes'], [])


class Merging(Base):
    def test_merges_archives_and_stops_the_agent(self):
        tip = self.branch('builder-1', {'a.py': 'one\nTWO\nthree\n'})
        self.rt.agent('builder-1')
        got = self.r.merge('demo', 'builder-1', tip)
        self.assertEqual((got['role'], got['base'], got['problem']), ('builder-1', 'main', None))
        self.assertIn('TWO', open(f'{self.repo}/a.py').read())
        self.assertEqual(git(self.repo, 'log', '-1', '--format=%P').split()[1], tip)
        self.assertEqual(self.rt.killed, ['t1'])
        self.assertFalse(os.path.exists(self.wt('builder-1')))
        self.assertEqual(git(self.repo, 'branch', '--list', 'assieme/demo/builder-1'), '')
        self.assertIn(tip, git(self.repo, 'for-each-ref', '--format=%(objectname)', 'refs/assieme/archive/demo/builder-1/'))
        self.assertEqual(self.r.waiting(), [])

    def test_the_log_has_what_activity_reads(self):
        tip = self.branch('builder-1', {'a.py': 'A\n'})
        got = self.r.merge('demo', 'builder-1', tip)
        entry = json.loads(open(self.log).read().splitlines()[-1])
        self.assertEqual((entry['id'], entry['by'], entry['team'], entry['undo']), (got['undo'], 'you', 'demo', True))
        self.assertEqual(entry['title'], 'Merged builder-1 into main')
        self.assertEqual(entry['reason'], 'Its worktree is archived.')

    def test_a_role_can_start_again_after_a_merge(self):
        from assieme import worktree
        from assieme.preset import Role, Team
        tip = self.branch('builder-1', {'a.py': 'A\n'})
        self.r.merge('demo', 'builder-1', tip)
        path = worktree.ensure(Team('demo', self.repo, {}, []), Role('builder-1', 'codex', 'b.md', 'brief'))
        self.assertEqual(git(path, 'branch', '--show-current'), 'assieme/demo/builder-1')

    def test_merges_without_touching_files_when_main_isnt_checked_out(self):
        tip = self.branch('builder-1', {'a.py': 'one\nTWO\nthree\n'})
        git(self.repo, 'switch', '-qc', 'elsewhere')
        write(f'{self.repo}/b.py', 'my own edit\n')
        got = self.r.merge('demo', 'builder-1', tip)
        self.assertEqual(git(self.repo, 'show', 'main:a.py').splitlines()[1], 'TWO')
        self.assertEqual(open(f'{self.repo}/a.py').read(), 'one\ntwo\nthree\n')
        self.assertEqual(open(f'{self.repo}/b.py').read(), 'my own edit\n')
        self.assertEqual(git(self.repo, 'log', '-1', '--format=%P', 'main').split()[1], tip)
        self.r.undo(got['undo'])
        self.assertEqual(git(self.repo, 'show', 'main:a.py').splitlines()[1], 'two')

    def test_refuses_when_the_branch_moved_since_looking(self):
        tip = self.branch('builder-1', {'a.py': 'A\n'})
        self.branch('builder-1', {'a.py': 'B\n'})
        before = self.main_tip()
        with self.assertRaisesRegex(ReviewError, 'Look again'):
            self.r.merge('demo', 'builder-1', tip)
        self.assertEqual(self.main_tip(), before)

    def test_refuses_uncommitted_changes_and_keeps_them(self):
        tip = self.branch('builder-1', {'a.py': 'A\n'})
        write(f"{self.wt('builder-1')}/a.py", 'dirty\n')
        with self.assertRaisesRegex(ReviewError, 'uncommitted'):
            self.r.merge('demo', 'builder-1', tip, force=True)
        self.assertEqual(open(f"{self.wt('builder-1')}/a.py").read(), 'dirty\n')

    def test_refuses_conflicts_even_when_forced(self):
        tip = self.branch('builder-1', {'a.py': 'MINE\n'})
        write(f'{self.repo}/a.py', 'THEIRS\n')
        git(self.repo, 'commit', '-qam', 'theirs')
        with self.assertRaisesRegex(ReviewError, 'conflicts'):
            self.r.merge('demo', 'builder-1', tip, force=True)

    def test_failing_tests_and_a_working_agent_need_force(self):
        tip = self.branch('builder-1', {'a.py': 'A\n'})
        self.r.tests[(self.repo, tip)] = {'state': 'bad', 'text': 'x', 'detail': ''}
        with self.assertRaisesRegex(ReviewError, 'tests'):
            self.r.merge('demo', 'builder-1', tip)
        self.r.tests.clear()
        self.rt.agent('builder-1', 'working')
        with self.assertRaisesRegex(ReviewError, 'still working'):
            self.r.merge('demo', 'builder-1', tip)
        self.assertIsNone(self.r.merge('demo', 'builder-1', tip, force=True)['problem'])

    def test_nothing_new_refuses(self):
        tip = self.branch('builder-1')
        with self.assertRaisesRegex(ReviewError, 'nothing new'):
            self.r.merge('demo', 'builder-1', tip)

    def test_overlapping_local_changes_refuse_and_change_nothing(self):
        tip = self.branch('builder-1', {'a.py': 'one\nTWO\nthree\n'})
        write(f'{self.repo}/a.py', 'my own edit\n')
        before = self.main_tip()
        with self.assertRaises(ReviewError) as e:
            self.r.merge('demo', 'builder-1', tip)
        self.assertEqual(str(e.exception), review.LOCAL_CHANGES)
        self.assertEqual(self.main_tip(), before)
        self.assertEqual(open(f'{self.repo}/a.py').read(), 'my own edit\n')
        self.assertTrue(os.path.isdir(self.wt('builder-1')))

    def test_the_persons_unfinished_merge_is_left_alone(self):
        tip = self.branch('builder-1', {'b.py': 'agent\n'})
        git(self.repo, 'switch', '-qc', 'theirs')
        write(f'{self.repo}/a.py', 'THEIRS\n')
        git(self.repo, 'commit', '-qam', 'theirs')
        git(self.repo, 'switch', '-q', 'main')
        write(f'{self.repo}/a.py', 'MINE\n')
        git(self.repo, 'commit', '-qam', 'mine')
        subprocess.run(['git', '-C', self.repo, 'merge', 'theirs'], capture_output=True)
        head = git(self.repo, 'rev-parse', 'MERGE_HEAD')
        with self.assertRaisesRegex(ReviewError, 'middle of a merge'):
            self.r.merge('demo', 'builder-1', tip)
        self.assertEqual(git(self.repo, 'rev-parse', 'MERGE_HEAD'), head)

    def test_a_failure_while_reading_notes_happens_before_main_moves(self):
        tip = self.branch('builder-1', {'a.py': 'A\n'})
        before = self.main_tip()
        self.r._notes = lambda *a: (_ for _ in ()).throw(OSError('gone'))
        with self.assertRaises(OSError):
            self.r.merge('demo', 'builder-1', tip)
        self.assertEqual(self.main_tip(), before)

    def test_unrelated_local_changes_survive_merge_and_undo(self):
        tip = self.branch('builder-1', {'a.py': 'one\nTWO\nthree\n'})
        write(f'{self.repo}/b.py', 'my own edit\n')
        got = self.r.merge('demo', 'builder-1', tip)
        self.assertEqual(open(f'{self.repo}/b.py').read(), 'my own edit\n')
        self.r.undo(got['undo'])
        self.assertEqual(open(f'{self.repo}/b.py').read(), 'my own edit\n')

    def test_a_failed_archive_keeps_the_merge_and_says_so(self):
        tip = self.branch('builder-1', {'a.py': 'A\n'})
        real = self.r.run
        self.r.run = lambda cmd, **kw: (types.SimpleNamespace(returncode=1, stdout='', stderr='locked')
                                        if 'worktree' in cmd and 'remove' in cmd else real(cmd, **kw))
        got = self.r.merge('demo', 'builder-1', tip)
        self.assertIn('locked', got['problem'])
        self.assertEqual(git(self.repo, 'show', 'main:a.py'), 'A')
        self.assertTrue(os.path.isdir(self.wt('builder-1')))
        self.r.run = real
        self.r.undo(got['undo'])
        self.assertEqual(git(self.repo, 'show', 'main:a.py').splitlines()[0], 'one')


class Undoing(Base):
    def merged(self):
        self.tip = self.branch('builder-1', {'a.py': 'one\nTWO\nthree\n'})
        self.old = self.main_tip()
        return self.r.merge('demo', 'builder-1', self.tip)['undo']

    def test_puts_everything_back(self):
        uid = self.merged()
        got = self.r.undo(uid)
        self.assertEqual(got, {'role': 'builder-1', 'base': 'main'})
        self.assertEqual(self.main_tip(), self.old)
        self.assertEqual(open(f'{self.repo}/a.py').read(), 'one\ntwo\nthree\n')
        self.assertEqual(git(self.wt('builder-1'), 'rev-parse', 'HEAD'), self.tip)
        self.assertEqual(self.r.waiting()[0]['role'], 'builder-1')
        self.assertEqual(self.r.get('demo', 'builder-1')['tip'], self.tip)
        with self.assertRaisesRegex(ReviewError, 'no longer'):
            self.r.undo(uid)

    line = Notes.line

    def test_notes_come_back(self):
        self.branch('builder-1', {'a.py': 'one\nTWO\nthree\n'})
        Notes.notes(self, json.dumps({'branch': 'builder-1', 'file': 'a.py', 'line': 2, 'text': 'hm'}))
        uid = self.r.merge('demo', 'builder-1', self.r.get('demo', 'builder-1')['tip'])['undo']
        self.r.undo(uid)
        self.assertEqual(len(self.r.get('demo', 'builder-1')['notes']), 1)

    def test_refuses_when_main_moved_on(self):
        uid = self.merged()
        write(f'{self.repo}/b.py', 'later\n')
        git(self.repo, 'commit', '-qam', 'later')
        tip = self.main_tip()
        with self.assertRaisesRegex(ReviewError, 'moved on'):
            self.r.undo(uid)
        self.assertEqual(self.main_tip(), tip)

    def test_refuses_when_the_merge_reached_origin(self):
        uid = self.merged()
        git(self.repo, 'update-ref', 'refs/remotes/origin/main', 'main')
        with self.assertRaisesRegex(ReviewError, 'origin'):
            self.r.undo(uid)

    def test_refuses_when_the_agent_started_again(self):
        uid = self.merged()
        self.branch('builder-1')
        with self.assertRaisesRegex(ReviewError, 'started again'):
            self.r.undo(uid)

    def test_refuses_when_the_checkout_would_lose_changes(self):
        uid = self.merged()
        write(f'{self.repo}/a.py', 'my own edit\n')
        with self.assertRaisesRegex(ReviewError, 'uncommitted'):
            self.r.undo(uid)
        self.assertEqual(open(f'{self.repo}/a.py').read(), 'my own edit\n')
        self.assertNotEqual(self.main_tip(), self.old)


class Discarding(Base):
    def test_removes_work_and_keeps_a_copy(self):
        tip = self.branch('builder-1', {'a.py': 'A\n'})
        write(f"{self.wt('builder-1')}/a.py", 'dirty\n')
        self.rt.agent('builder-1')
        before = self.main_tip()
        self.assertIsNone(self.r.discard('demo', 'builder-1', tip)['problem'])
        self.assertEqual(self.main_tip(), before)
        self.assertFalse(os.path.exists(self.wt('builder-1')))
        self.assertEqual(git(self.repo, 'branch', '--list', 'assieme/demo/builder-1'), '')
        self.assertEqual(self.rt.killed, ['t1'])
        self.assertIn(tip, git(self.repo, 'for-each-ref', '--format=%(objectname)', 'refs/assieme/archive/'))

    def test_refuses_when_the_branch_moved(self):
        tip = self.branch('builder-1', {'a.py': 'A\n'})
        self.branch('builder-1', {'a.py': 'B\n'})
        with self.assertRaisesRegex(ReviewError, 'Look again'):
            self.r.discard('demo', 'builder-1', tip)
        self.assertTrue(os.path.isdir(self.wt('builder-1')))


class PullRequests(Base):
    def setUp(self):
        super().setUp()
        self.branch('builder-1', {'a.py': 'A\n'})

    def fake(self, gh):
        """Git runs for real, except that push and gh are recorded."""
        self.calls = []

        def run(cmd, **kw):
            if cmd[0] == 'gh':
                self.calls.append(cmd)
                return gh
            if 'push' in cmd:
                self.calls.append(cmd)
                return types.SimpleNamespace(returncode=0, stdout='', stderr='')
            return subprocess.run(cmd, **kw)
        self.r.run = run

    def test_no_origin_says_so(self):
        with self.assertRaisesRegex(ReviewError, 'no remote'):
            self.r.pull_request('demo', 'builder-1')

    def test_pushes_to_a_local_bare_repository(self):
        bare = os.path.join(self.root, 'bare.git')
        subprocess.run(['git', 'init', '-q', '--bare', bare], check=True)
        git(self.repo, 'remote', 'add', 'origin', bare)
        got = self.r.pull_request('demo', 'builder-1')
        self.assertIsNone(got['url'])
        self.assertIn('pushed', got['detail'])
        self.assertEqual(git(bare, 'rev-parse', 'refs/heads/assieme/demo/builder-1'), git(self.wt('builder-1'), 'rev-parse', 'HEAD'))

    def test_a_failed_push_says_why(self):
        git(self.repo, 'remote', 'add', 'origin', os.path.join(self.root, 'nowhere.git'))
        with self.assertRaisesRegex(ReviewError, "Couldn't push"):
            self.r.pull_request('demo', 'builder-1')

    def test_gh_creates_the_pull_request(self):
        git(self.repo, 'remote', 'add', 'origin', 'git@example.invalid:me/shop.git')
        self.fake(types.SimpleNamespace(returncode=0, stdout='https://github.com/me/shop/pull/7\n', stderr=''))
        self.r.which = lambda c: '/x/gh' if c == 'gh' else None
        self.assertEqual(self.r.pull_request('demo', 'builder-1')['url'], 'https://github.com/me/shop/pull/7')
        self.assertEqual(self.calls[1], ['gh', 'pr', 'create', '--fill', '--base', 'main', '--head', 'assieme/demo/builder-1'])

    def test_the_log_only_says_opened_when_it_opened(self):
        git(self.repo, 'remote', 'add', 'origin', 'git@example.invalid:me/shop.git')
        self.fake(types.SimpleNamespace(returncode=1, stdout='', stderr='not logged in'))
        self.r.which = lambda c: '/x/gh' if c == 'gh' else None
        with self.assertRaises(ReviewError):
            self.r.pull_request('demo', 'builder-1')
        self.assertIn("didn't open", json.loads(open(self.log).read().splitlines()[-1])['title'])

    def test_an_existing_pull_request_is_reused(self):
        git(self.repo, 'remote', 'add', 'origin', 'git@example.invalid:me/shop.git')
        self.fake(types.SimpleNamespace(returncode=1, stdout='',
                                        stderr='a pull request already exists:\nhttps://github.com/me/shop/pull/7\n'))
        self.r.which = lambda c: '/x/gh' if c == 'gh' else None
        self.assertEqual(self.r.pull_request('demo', 'builder-1')['url'], 'https://github.com/me/shop/pull/7')

    def test_gh_failing_says_the_branch_is_pushed(self):
        git(self.repo, 'remote', 'add', 'origin', 'git@example.invalid:me/shop.git')
        self.fake(types.SimpleNamespace(returncode=1, stdout='', stderr='not logged in'))
        self.r.which = lambda c: '/x/gh' if c == 'gh' else None
        with self.assertRaisesRegex(ReviewError, 'pushed.*not logged in'):
            self.r.pull_request('demo', 'builder-1')

    def test_no_gh_opens_the_compare_page_on_github(self):
        git(self.repo, 'remote', 'add', 'origin', 'git@github.com:me/shop.git')
        self.fake(None)
        self.assertEqual(self.r.pull_request('demo', 'builder-1')['url'],
                         'https://github.com/me/shop/compare/main...assieme/demo/builder-1?expand=1')


class Asking(Base):
    def test_sends_to_a_live_agent(self):
        self.branch('builder-1', {'a.py': 'A\n'})
        self.rt.agent('builder-1')
        self.assertEqual(self.r.ask('demo', 'builder-1', ' cap at 6s '), {'restarted': False})
        self.assertEqual(self.rt.sent, [('t1', 'cap at 6s')])

    def test_empty_text_refuses(self):
        self.branch('builder-1', {'a.py': 'A\n'})
        with self.assertRaisesRegex(ReviewError, 'what should change'):
            self.r.ask('demo', 'builder-1', '  ')

    def test_a_stopped_agent_without_a_saved_team_says_why(self):
        self.branch('builder-1', {'a.py': 'A\n'})
        with self.assertRaisesRegex(ReviewError, "couldn't start again"):
            self.r.ask('demo', 'builder-1', 'x')

    def test_a_stopped_agent_starts_again_with_the_request(self):
        self.branch('builder-1', {'a.py': 'A\n'})
        write(f'{self.teams}/demo/builder-1.md', 'brief\n')
        with open(f'{self.teams}/demo/team.toml', 'a') as f:
            f.write('[[role]]\nname = "builder-1"\nagent = "claude"\n')
        seen = {}

        def start(tm, task, rt, ask, only=None):
            seen.update(task=task, only=only, trust=ask('?'))
            return [team_mod.Started('builder-1', 't9', 'running')]
        real, team_mod.start = team_mod.start, start
        self.addCleanup(setattr, team_mod, 'start', real)
        self.assertEqual(self.r.ask('demo', 'builder-1', 'cap'), {'restarted': True})
        self.assertEqual(seen, {'task': 'cap', 'only': 'builder-1', 'trust': True})


class Editor(Base):
    def setUp(self):
        super().setUp()
        self.branch('builder-1', {'a.py': 'A\n'})
        self.ran = []
        real = self.r.run

        def run(cmd, **kw):
            if cmd[0] in ('code', 'zed', 'open'):
                self.ran.append(cmd)
                return types.SimpleNamespace(returncode=0, stdout='', stderr='')
            return real(cmd, **kw)
        self.r.run = run

    def test_prefers_an_editor_at_the_line(self):
        self.r.which = lambda c: '/x' if c == 'code' else None
        self.r.open_editor('demo', 'builder-1', 'a.py', 3)
        self.assertEqual(self.ran, [['code', '--goto', f"{self.wt('builder-1')}/a.py:3"]])

    def test_falls_back_to_the_macs_text_editor(self):
        self.r.open_editor('demo', 'builder-1', 'a.py')
        self.assertEqual(self.ran, [['open', '-t', f"{self.wt('builder-1')}/a.py"]])

    def test_refuses_files_outside_the_diff(self):
        for bad in ('b.py', '../../../etc/passwd'):
            with self.assertRaisesRegex(ReviewError, "isn't in this diff"):
                self.r.open_editor('demo', 'builder-1', bad)
        self.assertEqual(self.ran, [])

    def test_a_deleted_file_says_so(self):
        git(self.wt('builder-1'), 'rm', '-q', 'b.py')
        git(self.wt('builder-1'), 'commit', '-qm', 'rm')
        with self.assertRaisesRegex(ReviewError, 'deleted'):
            self.r.open_editor('demo', 'builder-1', 'b.py')


if __name__ == '__main__':
    unittest.main()
