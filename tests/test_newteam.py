"""New team in the app: presets, checks, saving, background start and Help me choose."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from assieme import live, newteam, preset, settings, team

SAVED = '''name = "{name}"
project = "/tmp/x"

[[role]]
name = "builder"
agent = "claude"
job = "Builds"
'''


class PresetsTest(unittest.TestCase):
    def setUp(self):
        self.teams = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.teams)

    def saved(self, name, mtime):
        d = os.path.join(self.teams, name)
        os.makedirs(d)
        with open(os.path.join(d, 'team.toml'), 'w') as f:
            f.write(SAVED.format(name=name))
        with open(os.path.join(d, 'builder.md'), 'w') as f:
            f.write('Build it.\n')
        os.utime(d, (mtime, mtime))

    def test_builtin_presets(self):
        p = newteam.presets(teams=self.teams)
        self.assertEqual([x['id'] for x in p['presets']], ['one-agent', 'builder-reviewer', 'full'])
        self.assertEqual([x['label'] for x in p['presets']],
                         ['One agent', 'Builder + reviewer', 'Planner, 3 builders, reviewer, tester'])
        full = p['presets'][2]['roles']
        self.assertEqual([r['name'] for r in full],
                         ['planner', 'builder-1', 'builder-2', 'builder-3', 'reviewer', 'tester'])
        self.assertTrue(all(r['job'] and r['brief'] and 'Team practices' not in r['brief'] for r in full))
        self.assertEqual(set(full[0]), {'name', 'agent', 'job', 'checkout', 'parent', 'brief', 'model', 'effort', 'group'})
        self.assertIsNone(full[0]['parent'])
        self.assertEqual(full[4]['checkout'], 'read-only')
        self.assertEqual([g['id'] for g in p['practices']['groups']][0], 'messages')
        self.assertIsNone(p['last'])

    def test_last_is_newest_saved_team(self):
        now = time.time()
        self.saved('older', now - 100)
        self.saved('newer', now)
        last = newteam.presets(teams=self.teams)['last']
        self.assertEqual((last['id'], last['label']), ('newer', 'My last team'))
        self.assertEqual(last['roles'][0]['brief'].strip(), 'Build it.')

    def test_broken_saved_team_is_skipped(self):
        os.makedirs(os.path.join(self.teams, 'broken'))
        self.assertIsNone(newteam.presets(teams=self.teams)['last'])


def git_repo(path):
    subprocess.run(['git', 'init', '-q', path], check=True)
    subprocess.run(['git', '-C', path, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q',
                    '--allow-empty', '-m', 'init'], check=True)
    return path


def role(name='builder', **kw):
    return dict(dict(name=name, agent='claude', job='Builds', checkout='branch', parent=None, brief='Build it.'), **kw)


class CheckSaveTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.repo = git_repo(os.path.join(self.d, 'shop'))
        self.teams = os.path.join(self.d, 'teams')

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_project_check(self):
        self.assertEqual(newteam.project_check(self.repo), {'ok': True})
        plain = os.path.join(self.d, 'plain')
        os.makedirs(plain)
        self.assertEqual(newteam.project_check(plain), {'ok': False, 'reason': newteam.NEEDS_SAVE, 'setup': True})
        self.assertEqual(newteam.NEEDS_SAVE, "This folder has no saved version yet. Agents work from a saved version of your project, so it needs one.")

    def test_project_check_needs_a_first_commit(self):
        empty = os.path.join(self.d, 'empty')
        os.makedirs(empty)
        subprocess.run(['git', '-C', empty, 'init', '-q'], check=True)
        self.assertEqual(newteam.project_check(empty), {'ok': False, 'reason': newteam.NEEDS_SAVE, 'setup': True})

    def test_a_missing_folder(self):
        self.assertEqual(newteam.project_check(os.path.join(self.d, 'nope')), {'ok': False, 'reason': "This folder doesn't exist."})

    def test_missing_developer_tools_come_first(self):
        with mock.patch.object(newteam.setup, 'git_ready', return_value=False):
            got = newteam.project_check(os.path.join(self.d, 'nope'))
        self.assertEqual((got['ok'], got['tools']), (False, True))
        self.assertEqual(got['reason'], "This needs Apple's developer tools first.")

    def test_on_a_remote_machine_missing_git_is_said_plainly(self):
        with mock.patch.object(newteam.setup, 'git_ready', return_value=False), \
                mock.patch.dict(os.environ, {'ASSIEME_HC_SOCKET': '/tmp/x.sock'}):
            got = newteam.project_check(os.path.join(self.d, 'nope'))
        self.assertEqual(got, {'ok': False, 'reason': 'This machine needs git. Install it there, then choose the folder again.'})

    def test_home_is_refused_however_it_is_written(self):
        link = os.path.join(self.d, 'homelink')
        os.symlink(self.repo, link)
        refused = {'ok': False, 'reason': newteam.TOO_LARGE, 'too_large': True}
        with mock.patch.dict(os.environ, {'HOME': self.repo}), \
                mock.patch.object(newteam.setup, 'folder_preview', side_effect=AssertionError('walked')), \
                mock.patch.object(newteam.worktree, 'check_repo', side_effect=AssertionError('walked')):
            for p in (self.repo, self.repo + '/', link, '~', '~/'):
                self.assertEqual(newteam.project_check(p), refused, p)
            self.assertEqual(newteam.project_check('/'), refused)
        self.assertEqual(newteam.TOO_LARGE, "This folder is very large. Choose the folder for just this project.")

    def test_a_huge_folder_without_a_repository_is_too_large(self):
        plain = os.path.join(self.d, 'plain')
        os.makedirs(plain)
        with mock.patch.object(newteam.setup, 'folder_preview', side_effect=newteam.setup.TooLarge('big')):
            self.assertEqual(newteam.project_check(plain), {'ok': False, 'reason': newteam.TOO_LARGE, 'too_large': True})

    def test_uncommitted_files_are_counted_without_the_assieme_folder(self):
        os.makedirs(os.path.join(self.repo, '.assieme', 'worktrees'))
        open(os.path.join(self.repo, '.assieme', 'worktrees', 'x'), 'w').close()
        self.assertEqual(newteam.project_check(self.repo), {'ok': True})
        for n in ('a b.txt', 'caf\u00e9.md'):
            open(os.path.join(self.repo, n), 'w').close()
        os.makedirs(os.path.join(self.repo, 'sub'))
        open(os.path.join(self.repo, 'sub', 'c.txt'), 'w').close()
        self.assertEqual(newteam.project_check(self.repo), {'ok': True, 'uncommitted': 3})

    def test_files_a_save_leaves_out_are_not_uncommitted(self):
        os.makedirs(os.path.join(self.repo, 'node_modules', 'x'))
        for n in ('.env', '.DS_Store', 'node_modules/x/y.js'):
            open(os.path.join(self.repo, n), 'w').close()
        self.assertEqual(newteam.project_check(self.repo), {'ok': True})
        open(os.path.join(self.repo, 'sub.txt'), 'w').close()
        self.assertEqual(newteam.project_check(self.repo), {'ok': True, 'uncommitted': 1})

    def test_a_merge_in_progress_is_not_offered_a_save(self):
        g = lambda *a: subprocess.run(['git', '-C', self.repo, '-c', 'user.name=t', '-c', 'user.email=t@t', *a], capture_output=True, check=True)
        g('checkout', '-q', '-b', 'other')
        open(os.path.join(self.repo, 'a.txt'), 'w').write('theirs')
        g('add', '-A'), g('commit', '-q', '-m', 'o')
        g('checkout', '-q', '-')
        open(os.path.join(self.repo, 'a.txt'), 'w').write('ours')
        g('add', '-A'), g('commit', '-q', '-m', 'm')
        subprocess.run(['git', '-C', self.repo, 'merge', 'other'], capture_output=True)
        got = newteam.project_check(self.repo)
        self.assertEqual(got, {'ok': False, 'reason': 'A git merge or similar is in progress in this folder. Finish it first, then come back.'})

    def test_a_tilde_path_is_the_homes_folder_everywhere(self):
        with mock.patch.dict(os.environ, {'HOME': self.d}):
            self.assertEqual(newteam.setup.folder_preview('~/shop')['files'], 0)
            open(os.path.join(self.repo, 'a.txt'), 'w').close()
            self.assertEqual(newteam.setup.folder_preview('~/shop')['files'], 1)
            newteam.setup.folder_save('~/shop')
            self.assertEqual(newteam.project_check('~/shop'), {'ok': True})
            newteam.setup.folder_create('~/fresh')
        self.assertTrue(os.path.isdir(os.path.join(self.d, 'fresh')))
        self.assertFalse(os.path.exists('~'))

    def test_a_slow_listing_is_too_large_not_hidden(self):
        with mock.patch.object(newteam.setup, 'uncommitted', side_effect=newteam.setup.TooLarge('slow')):
            self.assertEqual(newteam.project_check(self.repo), {'ok': False, 'reason': newteam.TOO_LARGE, 'too_large': True})

    def assertField(self, roles, field, message):
        with self.assertRaises(newteam.NewTeamError) as e:
            newteam.validate(roles)
        self.assertEqual((e.exception.field, e.exception.message), (field, message))

    def test_validate_names(self):
        bad = 'Names use lower-case letters, digits and dashes, and must differ.'
        self.assertField([role('Builder')], 'role:0', bad)
        self.assertField([role('a b')], 'role:0', bad)
        self.assertField([role('x'), role('x')], 'role:1', bad)
        self.assertEqual(str(newteam.NewTeamError('role:1', bad)), f'role:1: {bad}')
        newteam.validate([role('builder-1'), role('reviewer', checkout='read-only', parent='builder-1')])

    def test_validate_refuses_empty_brief_and_no_roles(self):
        self.assertField([], 'roles', 'Add at least one agent.')
        self.assertField([role(brief='  ')], 'role:0', 'Every agent needs a brief.')
        with self.assertRaises(newteam.NewTeamError) as e:
            newteam.validate([role(agent='gpt')])
        self.assertEqual(e.exception.field, 'role:0')
        with self.assertRaises(newteam.NewTeamError) as e:
            newteam.validate([role(parent='nobody')])
        self.assertEqual(e.exception.field, 'role:0')

    def test_team_name_sanitised(self):
        self.assertEqual(newteam.team_name('/x/My App!', set()), 'my-app')
        self.assertEqual(newteam.team_name('/x/!!!', set()), 'team')
        self.assertEqual(newteam.team_name('/x/shop', {'shop', 'shop-2'}), 'shop-3')

    def test_save_round_trips_awkward_text(self):
        roles = [role(agent='codex', job='Fix "the" \\ bug\nnow', brief='Use "quotes" and \\ and\n\nlines.')]
        tm = preset.load(newteam.save(self.repo, roles, {'reviews': False}, 'shop', self.teams))
        self.assertEqual(tm.name, 'shop')
        self.assertEqual(tm.project, self.repo)
        self.assertEqual(tm.roles[0].job, roles[0]['job'])
        self.assertTrue(tm.roles[0].brief.startswith(roles[0]['brief']))
        self.assertEqual(tm.practices, {'reviews': False})

    def test_save_round_trips_emoji(self):
        roles = [role(job='fix 🐛')]
        tm = preset.load(newteam.save(self.repo, roles, {}, 'shop', self.teams))
        self.assertEqual(tm.roles[0].job, 'fix 🐛')

    def test_save_round_trips_model_and_effort(self):
        roles = [role(model='opus', effort='high'), role('other')]
        tm = preset.load(newteam.save(self.repo, roles, {}, 'shop', self.teams))
        self.assertEqual([(r.model, r.effort) for r in tm.roles], [('opus', 'high'), (None, None)])

    def test_validate_model_and_effort(self):
        self.assertField([role(model='a b')], 'role:0', 'Model names use letters, digits, dots and dashes.')
        self.assertField([role(effort='ultra')], 'role:0', 'Claude Code has no thinking effort "ultra".')
        newteam.validate([role(agent='codex', model='gpt-6.1-sol', effort='ultra')])

    def test_save_replaces_old_folder(self):
        newteam.save(self.repo, [role('old')], {}, 'shop', self.teams)
        folder = newteam.save(self.repo, [role('new')], {}, 'shop', self.teams)
        self.assertEqual(sorted(os.listdir(folder)), ['new.md', 'team.toml'])


class Manual:
    """A stand-in for threading.Thread: targets run only when the test says so."""

    def __init__(self):
        self.pending = []

    def __call__(self, target, daemon=None):
        self.pending.append(target)
        return self

    def start(self):
        pass

    def run_all(self):
        while self.pending:
            self.pending.pop(0)()


class Rt:
    def __init__(self, live=()):
        self.live = live

    def list(self, labels, all=False):
        return [{'address': f't{i}', 'state': 'live', 'labels': {team.TEAM: n, team.ROLE: 'builder'}}
                for i, n in enumerate(self.live)]


ROLES = [role('builder'), role('reviewer', checkout='read-only', parent='builder')]
ALL = lambda c: '/bin/' + c


class StarterTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.repo = git_repo(os.path.join(self.d, 'repo'))
        self.teams = os.path.join(self.d, 'teams')
        self.manual, self.clock = Manual(), [1000.0]
        self.calls = []
        p = mock.patch('assieme.newteam.team.start', side_effect=self.fake_start)
        p.start()
        self.addCleanup(p.stop)
        self.result = lambda tm: [team.Started(r.name, f't{i}', 'running') for i, r in enumerate(tm.roles)]

    def tearDown(self):
        shutil.rmtree(self.d)

    def fake_start(self, tm, task, rt, ask):
        self.calls.append((tm.name, task, ask('Trust?')))
        return self.result(tm)

    def starter(self, rt=None, which=ALL, scheduled=frozenset()):
        return newteam.Starter(rt or Rt(), self.teams, thread=self.manual, now=lambda: self.clock[0], which=which,
                               scheduled=lambda: set(scheduled))

    def test_start_expands_a_tilde_path(self):
        s = self.starter()
        with mock.patch.dict(os.environ, {'HOME': self.d}):
            self.assertEqual(s.start('~/repo', 'Fix login', ROLES, {}), {'team': 'repo'})
        self.assertEqual(s.records()[0]['project'], self.repo)

    def test_start_returns_before_agents_and_reports(self):
        s = self.starter()
        self.assertEqual(s.start(self.repo, 'Fix login', ROLES, {}), {'team': 'repo'})
        rec = s.records()[0]
        self.assertEqual((rec['team'], rec['project'], rec['done'], rec['error']), ('repo', self.repo, False, None))
        self.assertEqual([r['state'] for r in rec['roles']], ['starting', 'starting'])
        self.assertTrue(os.path.isdir(os.path.join(self.repo, '.assieme', 'worktrees', 'builder')))
        self.manual.run_all()
        rec = s.records()[0]
        self.assertTrue(rec['done'])
        self.assertEqual([(r['role'], r['state']) for r in rec['roles']], [('builder', 'running'), ('reviewer', 'running')])
        self.assertEqual(self.calls, [('repo', 'Fix login', True)])

    def test_a_steward_chosen_in_new_team_starts_with_the_team(self):
        s = self.starter()
        s.start(self.repo, 'Fix login', ROLES, {'steward': True})
        self.assertEqual([r['role'] for r in s.records()[0]['roles']], ['builder', 'reviewer', 'steward'])
        self.assertIn('steward', newteam.roster('repo', self.teams))

    def test_early_errors_raise(self):
        plain = os.path.join(self.d, 'plain')
        os.makedirs(plain)
        with self.assertRaises(newteam.NewTeamError) as e:
            self.starter().start(plain, 't', ROLES, {})
        self.assertEqual(e.exception.field, 'project')
        with self.assertRaises(newteam.NewTeamError) as e:
            self.starter(which=lambda c: None if c == 'codex' else '/bin/' + c).start(
                self.repo, 't', [role(agent='codex')], {})
        self.assertEqual(e.exception.field, 'start')
        self.assertTrue(e.exception.message.startswith('not installed: codex'))
        self.starter().start(self.repo, 't', ROLES, {})
        self.manual.run_all()
        with open(os.path.join(self.repo, '.assieme', 'worktrees', 'builder', 'dirty.txt'), 'w') as f:
            f.write('x')
        with self.assertRaises(newteam.NewTeamError) as e:
            self.starter().start(self.repo, 't', ROLES, {})
        self.assertEqual(e.exception.field, 'start')
        self.assertIn('builder', e.exception.message)
        self.assertEqual(len(self.calls), 1)

    def test_thread_error_recorded(self):
        def boom(tm):
            raise team.TeamError('boom')
        self.result = boom
        s = self.starter()
        s.start(self.repo, 't', ROLES, {})
        self.manual.run_all()
        rec = s.records()[0]
        self.assertEqual((rec['done'], rec['error']), (True, 'boom'))

    def test_second_start_while_starting_refused(self):
        s = self.starter()
        s.start(self.repo, 't', ROLES, {})
        with self.assertRaises(newteam.NewTeamError) as e:
            s.start(self.repo, 't', ROLES, {})
        self.assertEqual((e.exception.field, e.exception.message), ('start', 'This team is still starting.'))

    def test_records_expire_after_ten_minutes(self):
        s = self.starter()
        s.start(self.repo, 't', ROLES, {})
        self.manual.run_all()
        self.clock[0] += 599
        self.assertEqual(len(s.records()), 1)
        self.clock[0] += 2
        self.assertEqual(s.records(), [])

    def test_name_skips_live_team(self):
        self.assertEqual(self.starter(Rt(live=['repo'])).start(self.repo, 't', ROLES, {}), {'team': 'repo-2'})

    def test_validation_runs_first(self):
        with self.assertRaises(newteam.NewTeamError) as e:
            self.starter().start(self.repo, 't', [], {})
        self.assertEqual(e.exception.field, 'roles')


GOOD = {'roles': [{'name': 'builder', 'kind': 'builder', 'agent': 'claude', 'job': 'Fix it'},
                  {'name': 'reviewer', 'kind': 'reviewer', 'agent': 'claude', 'job': 'Check it'}],
        'why': 'Small change.'}
ASK = dict(project='/p', size='feature', review='every', budget='lean')
_FIVE = ['low', 'medium', 'high', 'xhigh', 'max']
MODELS = {'claude': {'models': [{'id': m, 'label': m, 'efforts': _FIVE} for m in ('opus', 'haiku')], 'efforts': _FIVE},
          'codex': {'models': [{'id': 'gpt-big', 'label': 'gpt-big', 'about': 'Frontier.', 'efforts': _FIVE + ['ultra']},
                               {'id': 'gpt-small', 'label': 'gpt-small', 'about': 'Fast.', 'efforts': ['low', 'medium']}],
                    'efforts': ['low', 'medium']}}


class SuggestTest(unittest.TestCase):
    def run_with(self, stdout='', code=0, stderr='', which=ALL, models=None):
        self.calls = []

        def run(argv, **kw):
            self.calls.append((argv, kw))
            return subprocess.CompletedProcess(argv, code, stdout, stderr)
        return newteam.suggest(**ASK, run=run, which=which, models=models or MODELS)

    def fails(self, message, **kw):
        with self.assertRaises(newteam.NewTeamError) as e:
            self.run_with(**kw)
        self.assertEqual(e.exception.field, 'suggest')
        self.assertEqual(e.exception.message, message)

    def test_valid_reply(self):
        out = self.run_with(json.dumps(GOOD))
        self.assertEqual(out['why'], 'Small change.')
        b, r = out['roles']
        self.assertEqual((b['name'], b['agent'], b['job'], b['checkout'], b['parent']), ('builder', 'claude', 'Fix it', 'branch', None))
        self.assertEqual(r['checkout'], 'read-only')
        self.assertEqual(b['brief'], newteam.kind_brief('builder'))
        newteam.validate(out['roles'])

    def picks(self, agent, **choice):
        g = {'roles': [dict(GOOD['roles'][0], agent=agent, **choice)], 'why': ''}
        out = self.run_with(json.dumps(g))
        newteam.validate(out['roles'])
        return out['roles'][0]['model'], out['roles'][0]['effort']

    def test_model_and_effort_kept_only_if_this_machine_runs_them(self):
        self.assertEqual(self.picks('claude'), (None, None))
        self.assertEqual(self.picks('claude', model='haiku', effort='low'), ('haiku', 'low'))
        self.assertEqual(self.picks('claude', effort='high'), (None, 'high'))
        self.assertEqual(self.picks('codex', model='gpt-big', effort='ultra'), ('gpt-big', 'ultra'))
        self.assertEqual(self.picks('claude', model='gpt-big', effort='ultra'), (None, None))  # another agent's model
        self.assertEqual(self.picks('claude', model='haiku-9', effort='max'), (None, 'max'))
        self.assertEqual(self.picks('codex', model='gpt-small', effort='high'), ('gpt-small', None))
        self.assertEqual(self.picks('codex', effort='high'), (None, None))  # not every Codex model has it
        self.assertEqual(self.picks('claude', model=['x'], effort={'a': 1}), (None, None))

    def test_prompt_lists_only_installed_agents_models(self):
        self.run_with(json.dumps(GOOD))
        prompt = self.calls[0][1]['input']
        self.assertIn('claude models, strongest and most expensive first: opus, haiku. Efforts: low, medium, high, xhigh, max', prompt)
        self.assertIn('codex model gpt-small (Fast.). Efforts: low, medium', prompt)
        self.assertIn('"model": "...", "effort": "..."', prompt)
        self.run_with(json.dumps(GOOD), which=lambda c: '/bin/claude' if c == 'claude' else None)
        self.assertNotIn('gpt-', self.calls[0][1]['input'])
        empty = dict(MODELS, codex={'models': [], 'efforts': []})
        self.run_with(json.dumps(GOOD), models=empty)
        self.assertIn('codex: its own default only', self.calls[0][1]['input'])

    def test_planner_is_parent(self):
        g = {'roles': [{'name': 'planner', 'kind': 'planner', 'agent': 'claude', 'job': 'Plans'}] + GOOD['roles'], 'why': ''}
        self.assertEqual([r['parent'] for r in self.run_with(json.dumps(g))['roles']], [None, 'planner', 'planner'])

    def test_claude_wrapper_and_fence(self):
        text = 'Here is the team:\n```json\n' + json.dumps(GOOD) + '\n```'
        out = self.run_with(json.dumps({'type': 'result', 'result': text}))
        self.assertEqual([r['name'] for r in out['roles']], ['builder', 'reviewer'])

    def test_bad_replies(self):
        pre = "Couldn't get a suggestion: "
        self.fails(pre + "the answer wasn't a team.", stdout='hello')
        chef = {'roles': [{'name': 'c', 'kind': 'chef', 'agent': 'claude', 'job': 'x'}], 'why': ''}
        self.fails(pre + "unknown role kind 'chef'.", stdout=json.dumps(chef))
        self.fails(pre + 'no agents in the answer.', stdout=json.dumps({'roles': [], 'why': ''}))
        many = {'roles': [dict(GOOD['roles'][0], name=f'b{i}') for i in range(9)], 'why': ''}
        self.fails(pre + 'more than 8 agents.', stdout=json.dumps(many))
        codex = {'roles': [dict(GOOD['roles'][0], agent='codex')], 'why': ''}
        self.fails(pre + 'it chose codex, which is not installed.', stdout=json.dumps(codex),
                   which=lambda c: '/bin/claude' if c == 'claude' else None)

    def test_cli_failure(self):
        self.fails("Couldn't get a suggestion: Please run /login", code=1, stderr='x\nPlease run /login\n')

    def test_timeout(self):
        def run(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 170)
        with self.assertRaises(newteam.NewTeamError) as e:
            newteam.suggest(**ASK, run=run, which=ALL)
        self.assertEqual(e.exception.message, "Couldn't get a suggestion: no answer within 3 minutes.")

    def test_prefers_claude_then_codex(self):
        self.run_with(json.dumps(GOOD))
        self.assertEqual(self.calls[0][0][:2], ['/bin/claude', '-p'])
        codex = {'roles': [dict(GOOD['roles'][0], agent='codex')], 'why': ''}
        self.run_with(json.dumps(codex), which=lambda c: '/bin/codex' if c == 'codex' else None)
        argv = self.calls[0][0]
        self.assertEqual(argv[:2], ['/bin/codex', 'exec'])
        self.assertIn('read-only', argv)
        self.fails('Install Claude Code or Codex, then reopen New team.', which=lambda c: None)

    def test_runs_in_project_with_clean_env_and_timeout(self):
        with mock.patch.dict(os.environ, {'CLAUDECODE': '1'}):
            self.run_with(json.dumps(GOOD))
        kw = self.calls[0][1]
        self.assertEqual((kw['cwd'], kw['timeout']), ('/p', 170))
        self.assertNotIn('CLAUDECODE', kw['env'])
        prompt = kw['input']
        for words in ('a feature', 'every change', 'lean', 'planner', 'reviewer', 'JSON'):
            self.assertIn(words, prompt)


class StarterSharingTest(StarterTest):
    def test_second_team_on_same_project_refused(self):
        s = self.starter()
        s.start(self.repo, 't', ROLES, {})
        self.manual.run_all()
        with self.assertRaises(newteam.NewTeamError) as e:
            self.starter(Rt(live=['repo'])).start(self.repo, 't', ROLES, {})
        self.assertEqual((e.exception.field, e.exception.message),
                         ('project', 'This project already has a running team: repo. Stop it first.'))

    def test_same_name_while_starting_gets_suffix(self):
        other = git_repo(os.path.join(self.d, 'b', 'repo'))
        s = self.starter()
        self.assertEqual(s.start(self.repo, 't', ROLES, {}), {'team': 'repo'})
        self.assertEqual(s.start(other, 't', ROLES, {}), {'team': 'repo-2'})

    def test_a_scheduled_run_names_its_team(self):
        s = self.starter()
        roles = [role('nightly-builder')]
        self.assertEqual(s.start(self.repo, 't', roles, {}, name='nightly'), {'team': 'nightly'})
        with self.assertRaises(newteam.NewTeamError) as e:
            self.starter(Rt(live=['nightly'])).start(git_repo(os.path.join(self.d, 'c')), 't', roles, {}, name='nightly')
        self.assertEqual(e.exception.message, 'nightly is still running.')

    def test_a_scheduled_run_shares_a_project_when_its_roles_differ(self):
        self.starter().start(self.repo, 't', ROLES, {})
        self.manual.run_all()
        got = self.starter(Rt(live=['repo']), scheduled={'nightly'}).start(self.repo, 't', [role('nightly-builder')], {}, name='nightly')
        self.assertEqual(got, {'team': 'nightly'})

    def test_new_team_may_start_beside_a_scheduled_run(self):
        newteam.save(self.repo, [role('nightly-builder')], {}, 'nightly', self.teams)
        got = self.starter(Rt(live=['nightly']), scheduled={'nightly'}).start(self.repo, 't', ROLES, {})
        self.assertEqual(got, {'team': 'repo'})

    def test_roles_that_share_a_worktree_still_refuse(self):
        newteam.save(self.repo, [role('builder')], {}, 'nightly', self.teams)
        with self.assertRaises(newteam.NewTeamError) as e:
            self.starter(Rt(live=['nightly']), scheduled={'nightly'}).start(self.repo, 't', ROLES, {})
        self.assertEqual(e.exception.message, 'This project already has a running team: nightly. Stop it first.')

    def test_records_not_blocked_by_git_work(self):
        s = self.starter()
        seen = []
        real = newteam.worktree.ensure

        def slow(tm, r):
            seen.append(s.lock.acquire(blocking=False) and (s.lock.release() or True))
            return real(tm, r)
        with mock.patch('assieme.newteam.worktree.ensure', side_effect=slow):
            s.start(self.repo, 't', ROLES, {})
        self.assertEqual(seen, [True, True])


class ModelOptionsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_claude_and_codex_options(self):
        cache = os.path.join(self.d, 'models_cache.json')
        with open(cache, 'w') as f:
            json.dump({'models': [
                {'slug': 'gpt-6.1-sol', 'supported_reasoning_levels': [{'effort': e} for e in ('low', 'medium', 'high', 'xhigh', 'max', 'ultra')]},
                {'slug': 'gpt-6-luna', 'supported_reasoning_levels': [{'effort': e} for e in ('low', 'medium', 'high', 'xhigh', 'max')]},
                {'slug': 'bad name!', 'supported_reasoning_levels': []},
            ]}, f)
        o = newteam.model_options(codex_cache=cache)
        self.assertEqual([m['id'] for m in o['claude']['models']], ['fable', 'opus', 'sonnet', 'haiku'])
        self.assertEqual(o['claude']['efforts'], ['low', 'medium', 'high', 'xhigh', 'max'])
        self.assertEqual([m['id'] for m in o['codex']['models']], ['gpt-6.1-sol', 'gpt-6-luna'])
        self.assertEqual(o['codex']['models'][1]['efforts'], ['low', 'medium', 'high', 'xhigh', 'max'])
        self.assertEqual(o['codex']['efforts'], ['low', 'medium', 'high', 'xhigh', 'max'])  # levels every model has

    def test_codex_cache_missing_or_broken(self):
        self.assertEqual(newteam.model_options(codex_cache=os.path.join(self.d, 'none.json'))['codex'],
                         {'models': [], 'efforts': []})
        bad = os.path.join(self.d, 'bad.json')
        with open(bad, 'w') as f:
            f.write('{not json')
        self.assertEqual(newteam.model_options(codex_cache=bad)['codex'], {'models': [], 'efforts': []})

    def test_presets_include_models_and_role_fields(self):
        p = newteam.presets(teams=self.d)
        self.assertIn('claude', p['models'])
        self.assertEqual({(r['model'], r['effort']) for r in p['presets'][0]['roles']}, {(None, None)})


class AddAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {'XDG_STATE_HOME': self.tmp.name})
        self.env.start()
        self.teams = os.path.join(self.tmp.name, 'teams')
        os.makedirs(self.teams)
        self.project = os.path.join(self.tmp.name, 'proj')
        os.makedirs(self.project)
        subprocess.run(['git', 'init', '-q'], cwd=self.project, check=True)
        subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '--allow-empty', '-m', 'x'], cwd=self.project, check=True)
        newteam.save(self.project, [{'name': 'lead', 'agent': 'claude', 'brief': 'lead brief'}], {}, 'shop', self.teams)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def starter(self):
        class Now:
            def __init__(self, target, daemon):
                self.target = target

            def start(self):
                self.target()
        return newteam.Starter(Rt(['shop']), self.teams, thread=Now, which=lambda c: '/x/' + c)

    def test_add_agent_appends_and_starts_only_it(self):
        with mock.patch.object(newteam.team, 'start', return_value=[newteam.team.Started('b', 't5', 'running')]) as start:
            self.assertEqual(self.starter().add_agent('shop', {'name': 'b', 'agent': 'codex', 'kind': 'builder', 'parent': 'lead'}), {'team': 'shop'})
        self.assertEqual(start.call_args.kwargs['only'], 'b')
        self.assertEqual(sorted(newteam.roster('shop', self.teams)), ['b', 'lead'])
        [e] = live.recent_actions()
        self.assertEqual((e['action'], e['agent'], e['address'], e['team']), ('add_agent', 'b', 't5', 'shop'))
        self.assertTrue(os.path.isdir(os.path.join(self.project, '.assieme', 'worktrees', 'b')))

    def test_add_agent_to_a_stopped_team_refuses(self):
        s = self.starter()
        s.rt = Rt()
        with self.assertRaisesRegex(newteam.NewTeamError, 'has stopped'):
            s.add_agent('shop', {'name': 'tester', 'agent': 'claude', 'brief': 'b'})
        self.assertNotIn('tester', open(os.path.join(self.teams, 'shop', 'team.toml')).read())

    def test_add_agent_bad_name_taken(self):
        for name in ('lead', 'Bad Name'):
            with self.assertRaises(newteam.NewTeamError):
                self.starter().add_agent('shop', {'name': name, 'agent': 'codex', 'kind': 'builder'})
        self.assertEqual(sorted(newteam.roster('shop', self.teams)), ['lead'])

    def test_add_agent_no_saved_folder(self):
        with self.assertRaisesRegex(newteam.NewTeamError, "started from the command line"):
            self.starter().add_agent('other', {'name': 'b', 'agent': 'codex', 'kind': 'builder'})

    def test_failed_start_frees_the_name(self):
        with mock.patch.object(newteam.team, 'start', side_effect=OSError('no')):
            self.starter().add_agent('shop', {'name': 'b', 'agent': 'codex', 'kind': 'builder'})
        self.assertEqual(sorted(newteam.roster('shop', self.teams)), ['lead'])
        self.assertEqual(live.recent_actions(), [])

    def test_group_survives_being_saved(self):
        newteam.save(self.project, [{'name': 'x', 'agent': 'claude', 'brief': 'b', 'group': 'payments'}], {}, 'g', self.teams)
        self.assertEqual(newteam.roster('g', self.teams)['x']['group'], 'payments')

    def test_practice_defaults_reach_presets(self):
        settings.set_value('practices.reviews', False)
        self.assertEqual(newteam.presets(self.teams)['practice_defaults'], {'reviews': False})
