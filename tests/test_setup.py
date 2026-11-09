"""Developer tools, where agents live, and whether they are signed in."""
import os
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
        self.assertEqual(sorted(setup.handlers(None)), ['setup.install_tools', 'setup.status'])


if __name__ == '__main__':
    unittest.main()
