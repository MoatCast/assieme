"""CLI: assieme team start/status/stop, and quiet send under assieme quiet."""
import contextlib
import io
import unittest
from unittest import mock

from assieme import cli, preset
from assieme.team import Started


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTeamTest(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch('assieme.cli.runtime.Runtime'),
                  mock.patch('assieme.cli.preset.load', return_value=preset.Team('demo', '/p', {}, []))):
            p.start()
            self.addCleanup(p.stop)

    def test_quiet_moved(self):
        with mock.patch('assieme.cli.quiet.pairs', return_value=[]), mock.patch('assieme.cli.quiet.held', return_value={}), \
             mock.patch('assieme.cli.quiet.deliverer_alive', return_value=False):
            code, out, _ = run(['quiet', 'status'])
        self.assertEqual(code, 0)
        self.assertIn('stand-in installed', out)

    def test_team_start_prints_one_line_per_role(self):
        res = [Started('coordinator', 't1', 'running'),
               Started('builder-1', 't2', 'needs-you', 'Codex wants you to review new hooks once. Open builder-1 in Terminals and choose.')]
        with mock.patch('assieme.cli.team.start', return_value=res):
            code, out, _ = run(['team', 'start', '/presets/demo', '--task', 'fix it'])
        self.assertEqual(code, 0)
        self.assertRegex(out, r'coordinator\s+running')
        self.assertIn('hc attach t2', out)
        self.assertIn('then: assieme team start /presets/demo', out)

    def test_team_start_bad_preset_exits_1_with_message(self):
        with mock.patch('assieme.cli.preset.load', side_effect=preset.PresetError('no team.toml in /x')):
            code, _, err = run(['team', 'start', '/x'])
        self.assertEqual(code, 1)
        self.assertIn('no team.toml in /x', err)
        self.assertNotIn('Traceback', err)

    def test_exited_role_gets_retry_hint(self):
        with mock.patch('assieme.cli.team.start', return_value=[Started('builder-1', 't2', 'exited', 'Error: x')]):
            _, out, _ = run(['team', 'start', '/presets/demo'])
        self.assertIn('assieme team start /presets/demo --only builder-1', out)

    def test_team_stop(self):
        with mock.patch('assieme.cli.team.stop', return_value=2):
            code, out, _ = run(['team', 'stop', 'demo'])
        self.assertEqual(code, 0)
        self.assertIn('stopped 2 agents', out)

    def test_team_status(self):
        rows = [{'team': 'demo', 'role': 'coordinator', 'address': 't1', 'state': 'live', 'status': 'idle'}]
        with mock.patch('assieme.cli.team.status', return_value=rows):
            code, out, _ = run(['team', 'status', 'demo'])
        self.assertEqual(code, 0)
        self.assertRegex(out, r'demo\s+coordinator\s+t1\s+live\s+idle')
