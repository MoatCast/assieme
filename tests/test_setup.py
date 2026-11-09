"""Developer tools, where agents live, and whether they are signed in."""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace as Done
from unittest import mock

from assieme import setup


def fail_if_called(*a, **k):
    raise AssertionError('should not run anything')


class GitReady(unittest.TestCase):
    def test_the_apple_stub_alone_is_not_git(self):
        self.assertFalse(setup.git_ready(which=lambda n: '/usr/bin/git', run=lambda *a, **k: Done(returncode=2)))

    def test_the_apple_stub_is_never_run(self):
        calls = []
        setup.git_ready(which=lambda n: '/usr/bin/git', run=lambda argv, **k: calls.append(argv) or Done(returncode=0))
        self.assertEqual(calls, [['xcode-select', '-p']])

    def test_the_stub_counts_once_xcode_select_knows_the_tools(self):
        self.assertTrue(setup.git_ready(which=lambda n: '/usr/bin/git', run=lambda *a, **k: Done(returncode=0)))

    def test_git_outside_usr_bin_counts_without_xcode_select(self):
        self.assertTrue(setup.git_ready(which=lambda n: '/opt/homebrew/bin/git', run=fail_if_called))

    def test_no_xcode_select_means_no_git(self):
        def missing(*a, **k):
            raise FileNotFoundError('xcode-select')
        self.assertFalse(setup.git_ready(which=lambda n: None, run=missing))


class AgentPath(unittest.TestCase):
    def test_an_agent_installed_after_start_is_found_in_local_bin(self):
        with tempfile.TemporaryDirectory() as home:
            exe = os.path.join(home, '.local', 'bin', 'claude')
            os.makedirs(os.path.dirname(exe))
            open(exe, 'w').close()
            os.chmod(exe, 0o755)
            with mock.patch.dict(os.environ, {'HOME': home}):
                self.assertEqual(setup.agent_path('claude', which=lambda n: None, login_path=lambda: ''), exe)

    def test_the_login_shells_folders_are_searched(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            exe = os.path.join(d, 'codex')
            open(exe, 'w').close()
            os.chmod(exe, 0o755)
            with mock.patch.dict(os.environ, {'HOME': home}):
                self.assertEqual(setup.agent_path('codex', which=lambda n: None, login_path=lambda: f'/nope:{d}'), exe)

    def test_the_brains_own_path_wins(self):
        self.assertEqual(setup.agent_path('claude', which=lambda n: '/x/claude', login_path=fail_if_called), '/x/claude')

    def test_a_missing_agent_is_none(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {'HOME': home}):
            self.assertIsNone(setup.agent_path('claude', which=lambda n: None, login_path=lambda: '/nope'))

    def test_login_path_is_read_at_most_once_a_minute(self):
        reads, now = [], [0.0]

        def login():
            reads.append(1)
            return ''
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {'HOME': home}):
            for t in (0, 30, 61):
                now[0] = t
                setup.agent_path('claude', which=lambda n: None, login_path=login, clock=lambda: now[0])
                if t == 30:
                    self.assertEqual(len(reads), 1)
        self.assertEqual(len(reads), 2)


class Status(unittest.TestCase):
    def status(self, run, installed=('claude', 'codex')):
        with mock.patch.object(setup, 'git_ready', return_value=True), \
                mock.patch.object(setup, 'agent_path', side_effect=lambda n: f'/x/{n}' if n in installed else None):
            return setup.status(run=run)

    def test_status_says_not_signed_in_when_the_command_says_so(self):
        got = self.status(lambda argv, **k: Done(returncode=1 if argv[0] == '/x/codex' else 0))
        self.assertEqual(got, {'git': True, 'agents': {'claude': {'installed': True, 'signed_in': True},
                                                       'codex': {'installed': True, 'signed_in': False}}})

    def test_the_right_command_runs_by_absolute_path(self):
        calls = []
        self.status(lambda argv, **k: calls.append(argv) or Done(returncode=0))
        self.assertEqual(calls, [['/x/claude', 'auth', 'status'], ['/x/codex', 'login', 'status']])

    def test_status_treats_an_unclear_answer_as_signed_in(self):
        def run(argv, **k):
            if argv[0] == '/x/claude':
                return Done(returncode=127)
            raise OSError('cannot run')
        got = self.status(run)['agents']
        self.assertTrue(got['claude']['signed_in'] and got['codex']['signed_in'])

    def test_a_hanging_status_command_answers_as_signed_in(self):
        timeouts = []

        def run(argv, **k):
            timeouts.append(k.get('timeout'))
            raise subprocess.TimeoutExpired(argv, k.get('timeout'))
        self.assertTrue(self.status(run)['agents']['claude']['signed_in'])
        self.assertEqual(timeouts, [5, 5])

    def test_status_discards_the_commands_output(self):
        seen = []
        self.status(lambda argv, **k: seen.append(k) or Done(returncode=0))
        self.assertEqual(len(seen), 2)
        for k in seen:
            self.assertEqual((k['stdout'], k['stderr']), (subprocess.DEVNULL, subprocess.DEVNULL))
            self.assertNotIn('capture_output', k)

    def test_two_slow_commands_cost_one_wait_not_two(self):
        def slow(argv, **k):
            time.sleep(0.3)
            return Done(returncode=0)
        t = time.monotonic()
        self.status(slow)
        self.assertLess(time.monotonic() - t, 0.5)

    def test_not_installed_means_not_signed_in(self):
        got = self.status(fail_if_called, installed=())['agents']
        self.assertEqual(got['claude'], {'installed': False, 'signed_in': False})


class InstallTools(unittest.TestCase):
    def test_it_opens_apples_installer(self):
        calls = []
        self.assertEqual(setup.install_tools(run=lambda argv, **k: calls.append(argv) or Done(returncode=0)), {'ok': True})
        self.assertEqual(calls, [['xcode-select', '--install']])

    def test_handlers_carry_both_methods(self):
        self.assertEqual(sorted(setup.handlers(None)), ['setup.folder_create', 'setup.folder_preview', 'setup.folder_save',
                                                        'setup.install_tools', 'setup.status'])


def git(cwd, *args):
    return subprocess.run(['git', '-C', cwd, *args], capture_output=True, text=True, check=True).stdout


ID = ['-c', 'user.name=t', '-c', 'user.email=t@t']


class FolderTest(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        self.p = os.path.join(self.d, 'proj')
        os.makedirs(self.p)

    def write(self, name, text='x'):
        path = os.path.join(self.p, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(text)

    def commit_all(self):
        git(self.p, *ID, 'add', '-A')
        git(self.p, *ID, 'commit', '-q', '-m', 'c')

    def repo(self, commit=True):
        git(self.p, 'init', '-q')
        if commit:
            self.write('base.txt')
            self.commit_all()

    def snapshot(self):
        out = {}
        for root, _, names in os.walk(self.p):
            for n in names:
                st = os.stat(os.path.join(root, n))
                out[os.path.join(root, n)] = (st.st_mtime_ns, st.st_size)
        return out

    def listing(self):
        return sorted(git(self.p, 'ls-tree', '-r', '--name-only', 'HEAD').splitlines())


class FolderPreview(FolderTest):
    def test_preview_changes_nothing(self):
        self.write('a.txt')
        self.write('.env')
        before = self.snapshot()
        got = setup.folder_preview(self.p)
        self.assertEqual((got['files'], got['first']), (1, True))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(os.path.exists(os.path.join(self.p, '.git')))

    def test_preview_of_an_existing_repository_writes_nothing_into_it(self):
        self.repo()
        self.write('a.txt')
        self.write('.env')
        before = self.snapshot()
        got = setup.folder_preview(self.p)
        self.assertEqual((got['files'], got['first']), (1, False))
        self.assertEqual(self.snapshot(), before)  # includes .git/index and .git/info/exclude

    def test_preview_lists_what_is_left_out(self):
        for n in ('a.txt', '.env', '.env.local', 'a.pem', 'node_modules/x/y.js', '.DS_Store'):
            self.write(n)
        got = setup.folder_preview(self.p)
        self.assertEqual((got['files'], got['sample']), (1, ['a.txt']))
        self.assertEqual(sorted(got['left_out']), ['.DS_Store', '.env', '.env.local', 'a.pem', 'node_modules'])
        self.assertFalse(got['empty'])

    def test_names_with_spaces_and_accents_count(self):
        names = ['my file.txt', 'caf\u00e9.md', 'sub dir/\u00e9 x.txt']
        for n in names:
            self.write(n)
        got = setup.folder_preview(self.p)
        self.assertEqual(got['files'], 3)
        self.assertEqual(sorted(got['sample']), sorted(names))

    def test_sample_stops_at_500(self):
        for i in range(501):
            self.write(f'f{i:03}.txt')
        got = setup.folder_preview(self.p)
        self.assertEqual((got['files'], len(got['sample'])), (501, 500))

    def test_an_empty_folder_is_empty(self):
        self.assertEqual(setup.folder_preview(self.p), {'files': 0, 'sample': [], 'left_out': [], 'empty': True, 'first': True})

    def test_a_repository_without_commits_is_a_first_version(self):
        self.repo(commit=False)
        self.write('a.txt')
        self.assertTrue(setup.folder_preview(self.p)['first'])

    def tracked_secret(self):
        self.repo()
        self.write('.env', 'one')
        git(self.p, *ID, 'add', '-f', '.env')
        git(self.p, *ID, 'commit', '-q', '-m', 'env')
        self.write('.env', 'two')

    def test_a_tracked_secret_with_changes_is_listed_as_a_file(self):
        self.tracked_secret()
        got = setup.folder_preview(self.p)
        self.assertEqual(got['sample'], ['.env'])
        self.assertEqual(got['left_out'], [])

    def test_preview_counts_what_a_save_commits(self):
        self.tracked_secret()
        self.write('new.txt')
        self.write('secrets.json')
        self.write('base.txt', 'changed')
        n = setup.folder_preview(self.p)['files']
        setup.folder_save(self.p)
        self.assertEqual(n, len(git(self.p, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').splitlines()))
        self.assertEqual(n, 3)

    def test_more_than_20000_files_is_refused(self):
        for i in range(4):
            self.write(f'f{i}.txt')
        with mock.patch.object(setup, 'MAX_FILES', 3):
            with self.assertRaises(setup.TooLarge):
                setup.folder_preview(self.p)
            with self.assertRaises(setup.TooLarge):
                setup.folder_save(self.p)
        self.assertFalse(os.path.exists(os.path.join(self.p, '.git')))

    def test_a_listing_that_takes_too_long_counts_as_too_large(self):
        def slow(argv, **k):
            raise subprocess.TimeoutExpired(argv, k.get('timeout'))
        with mock.patch.object(setup.subprocess, 'run', slow):
            with self.assertRaises(setup.TooLarge):
                setup.folder_preview(self.p)


class FolderSave(FolderTest):
    def test_save_excludes_secrets_and_leaves_project_files_alone(self):
        self.write('a.txt')
        self.write('.env', 'KEY=1')
        self.write('node_modules/m.js')
        self.assertEqual(setup.folder_save(self.p), {'ok': True})
        self.assertEqual(self.listing(), ['a.txt'])
        self.assertFalse(os.path.exists(os.path.join(self.p, '.gitignore')))
        self.assertEqual(open(os.path.join(self.p, '.env')).read(), 'KEY=1')
        self.assertEqual(git(self.p, 'log', '-1', '--format=%s').strip(), 'First version, saved by Assieme')

    def test_save_works_without_a_git_identity(self):
        home = os.path.join(self.d, 'home')
        os.makedirs(home)
        self.write('a.txt')
        with mock.patch.dict(os.environ, {'HOME': home, 'XDG_CONFIG_HOME': home, 'GIT_CONFIG_NOSYSTEM': '1'}):
            for k in ('GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL', 'EMAIL'):
                os.environ.pop(k, None)
            setup.folder_save(self.p)
            self.assertEqual(git(self.p, 'log', '-1', '--format=%an <%ae>').strip(), 'Assieme <assieme@localhost>')
        self.assertEqual(os.listdir(home), [])  # global config untouched

    def test_empty_folder_makes_an_empty_first_commit(self):
        setup.folder_save(self.p)
        self.assertEqual(self.listing(), [])
        self.assertEqual(git(self.p, 'rev-list', '--count', 'HEAD').strip(), '1')

    def test_save_them_first_saves_changes_on_the_current_branch(self):
        self.repo()
        git(self.p, 'checkout', '-q', '-b', 'feature')
        self.write('base.txt', 'changed')
        self.write('new.txt')
        self.write('.env')
        setup.folder_save(self.p)
        self.assertEqual(git(self.p, 'rev-parse', '--abbrev-ref', 'HEAD').strip(), 'feature')
        self.assertEqual(git(self.p, 'log', '-1', '--format=%s').strip(), 'Saved by Assieme before starting a team')
        self.assertEqual(self.listing(), ['base.txt', 'new.txt'])
        self.assertEqual(git(self.p, 'rev-list', '--count', 'HEAD').strip(), '2')

    def test_a_failed_save_reports_git_in_plain_text(self):
        self.repo()
        self.write('a.txt')
        open(os.path.join(self.p, '.git', 'index.lock'), 'w').close()
        with self.assertRaises(setup.SetupError) as cm:
            setup.folder_save(self.p)
        self.assertIn('index.lock', str(cm.exception))

    def test_nothing_to_save_in_a_repository_makes_no_commit(self):
        self.repo()
        self.write('.env')
        self.assertEqual(setup.folder_save(self.p), {'ok': True})
        self.assertEqual(git(self.p, 'rev-list', '--count', 'HEAD').strip(), '1')

    def test_save_commits_exactly_what_the_preview_listed(self):
        self.repo()
        self.write('.gitignore', '!.env.example\n')
        self.commit_all()
        for n in ('.env.example', '.env', 'new.txt'):
            self.write(n)
        os.remove(os.path.join(self.p, 'base.txt'))
        got = setup.folder_preview(self.p)
        self.assertEqual(got['sample'], ['base.txt', 'new.txt'])
        self.assertEqual(sorted(got['left_out']), ['.env', '.env.example'])
        setup.folder_save(self.p)
        changed = git(self.p, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').splitlines()
        self.assertEqual(sorted(changed), got['sample'])
        self.assertNotIn('.env.example', self.listing())

    def test_save_refuses_home_and_root(self):
        with mock.patch.dict(os.environ, {'HOME': self.p}):
            with self.assertRaises(setup.TooLarge):
                setup.folder_save(self.p)
        with self.assertRaises(setup.TooLarge):
            setup.folder_save('/')


class FolderCreate(FolderTest):
    def test_new_folder_is_made(self):
        path = os.path.join(self.d, 'fresh')
        self.assertEqual(setup.folder_create(path), {'path': path})
        self.assertTrue(os.path.isdir(path))

    def test_new_folder_refuses_an_existing_path(self):
        with self.assertRaisesRegex(setup.SetupError, 'already'):
            setup.folder_create(self.p)


if __name__ == '__main__':
    unittest.main()
