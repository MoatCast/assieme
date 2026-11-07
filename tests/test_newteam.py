"""New team in the app: presets, checks, saving, background start and Help me choose."""
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from assieme import newteam, preset

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
        self.assertEqual(set(full[0]), {'name', 'agent', 'job', 'checkout', 'parent', 'brief'})
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
        self.assertEqual(newteam.project_check(plain), {'ok': False, 'reason':
                         "This folder isn't a git repository. Assieme gives each agent its own branch, so it needs one."})

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

    def test_save_replaces_old_folder(self):
        newteam.save(self.repo, [role('old')], {}, 'shop', self.teams)
        folder = newteam.save(self.repo, [role('new')], {}, 'shop', self.teams)
        self.assertEqual(sorted(os.listdir(folder)), ['new.md', 'team.toml'])
