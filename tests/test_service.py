"""The brain as a launchd user agent: plist content and the launchctl calls."""
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from assieme import service


class FakeRun:
    def __init__(self, codes=None):
        self.calls, self.codes = [], codes or {}

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.codes.get(cmd[1], 0), '', '')


class PlistTest(unittest.TestCase):
    def test_plist_runs_brain_with_clean_env(self):
        env = {'PATH': '/bin', 'HOME': '/h', 'CLAUDECODE': '1', 'SECRET': 'x', 'CLAUDE_CODE_CHILD_SESSION': 'y'}
        d = plistlib.loads(service.plist('/py', env))
        self.assertEqual(d['Label'], 'dev.assieme.brain')
        self.assertEqual(d['ProgramArguments'], ['/py', '-P', '-m', 'assieme', 'serve'])
        self.assertEqual(d['EnvironmentVariables'], {'PATH': '/bin', 'HOME': '/h'})
        self.assertTrue(d['KeepAlive'] and d['RunAtLoad'])
        self.assertTrue(d['StandardErrorPath'].endswith('assieme/brain.log'))

    def test_plist_keeps_xdg_state_home(self):
        d = plistlib.loads(service.plist('/py', {'PATH': '/bin', 'HOME': '/h', 'XDG_STATE_HOME': '/s'}))
        self.assertEqual(d['EnvironmentVariables']['XDG_STATE_HOME'], '/s')
        self.assertEqual(d['StandardOutPath'], '/s/assieme/brain.log')


class LaunchctlTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        p = mock.patch.dict(os.environ, {'HOME': self.home})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.home, True)

    def test_plist_path(self):
        self.assertEqual(service.plist_path(), f'{self.home}/Library/LaunchAgents/dev.assieme.brain.plist')

    def test_install_bootstraps(self):
        run = FakeRun()
        path = service.install(run=run)
        self.assertTrue(os.path.exists(path))
        target = f'gui/{os.getuid()}'
        self.assertEqual(run.calls[0][:2], ['launchctl', 'bootout'])
        self.assertEqual(run.calls[-1], ['launchctl', 'bootstrap', target, path])

    def test_install_fails_loudly(self):
        waits = []
        with self.assertRaisesRegex(RuntimeError, 'bootstrap'):
            service.install(run=FakeRun({'bootstrap': 5}), sleep=waits.append)
        self.assertEqual(len(waits), service.BOOTSTRAP_TRIES - 1)

    def test_install_waits_for_the_old_service_to_stop(self):
        class Stopping(FakeRun):
            left = 2

            def __call__(self, cmd, **kw):
                if cmd[1] == 'bootstrap' and Stopping.left:
                    Stopping.left -= 1
                    self.calls.append(cmd)
                    return subprocess.CompletedProcess(cmd, 5, '', 'Input/output error')
                return super().__call__(cmd, **kw)
        run, waits = Stopping(), []
        service.install(run=run, sleep=waits.append)
        self.assertEqual(sum(c[1] == 'bootstrap' for c in run.calls), 3)
        self.assertEqual(len(waits), 2)

    def test_uninstall_removes_plist(self):
        path = service.install(run=FakeRun())
        run = FakeRun()
        service.uninstall(run=run)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(run.calls[0][:2], ['launchctl', 'bootout'])

    def test_running(self):
        self.assertTrue(service.running(run=FakeRun()))
        self.assertFalse(service.running(run=FakeRun({'print': 113})))


class CliServiceTest(unittest.TestCase):
    def run_cli(self, argv):
        import contextlib, io
        from assieme import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_status(self):
        with mock.patch('assieme.cli.service.running', return_value=False):
            self.assertEqual(self.run_cli(['service', 'status']), (1, 'not running\n'))
        with mock.patch('assieme.cli.service.running', return_value=True):
            self.assertEqual(self.run_cli(['service', 'status']), (0, 'running\n'))

    def test_install_prints_plist_path(self):
        with mock.patch('assieme.cli.service.install', return_value='/L/dev.assieme.brain.plist'):
            code, out = self.run_cli(['service', 'install'])
        self.assertEqual(code, 0)
        self.assertIn('/L/dev.assieme.brain.plist', out)


class CliTest(unittest.TestCase):
    def test_a_failed_install_is_a_sentence_not_a_traceback(self):
        from assieme import cli
        with mock.patch.object(service, 'install', side_effect=RuntimeError('launchctl bootstrap failed (5)')), \
                mock.patch('sys.stderr') as err:
            self.assertEqual(cli.main(['service', 'install']), 1)
        self.assertIn("Couldn't start the background service", ''.join(c.args[0] for c in err.write.call_args_list))
