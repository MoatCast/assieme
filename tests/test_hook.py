"""The status hook: agent events become OSC 7501 on the agent's own terminal."""
import io
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from assieme import hook


def fake_ps(table):
    """table: pid -> (tty, ppid), answering `ps -o tty=,ppid= -p <pid>`."""
    def run(cmd, **kw):
        tty, ppid = table[int(cmd[-1])]
        return subprocess.CompletedProcess(cmd, 0, f'{tty} {ppid}\n', '')
    return run


class OscTest(unittest.TestCase):
    def test_osc_working(self):
        self.assertEqual(hook.osc('working'), b'\x1b]7501;state=working:app=agent\x1b\\')

    def test_osc_blocked_has_kind(self):
        self.assertEqual(hook.osc('blocked'), b'\x1b]7501;state=blocked:kind=permission:app=agent\x1b\\')


class FindTtyTest(unittest.TestCase):
    def test_find_tty_walks_past_processes_without_one(self):
        self.assertEqual(hook.find_tty(30, ps=fake_ps({30: ('??', 20), 20: ('ttys004', 1)})), '/dev/ttys004')

    def test_find_tty_none_when_no_ancestor_has_one(self):
        self.assertIsNone(hook.find_tty(30, ps=fake_ps({30: ('??', 1)})))


class MainTest(unittest.TestCase):
    def test_main_writes_to_found_tty_and_never_fails(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            with mock.patch.object(hook, 'find_tty', return_value=path), mock.patch('sys.stdin', io.StringIO('{}')):
                self.assertEqual(hook.main(['idle']), 0)
                self.assertEqual(hook.main(['nonsense']), 0)
            with open(path, 'rb') as f:
                self.assertEqual(f.read(), hook.osc('idle'))
        finally:
            os.remove(path)

    def test_main_without_a_terminal_still_succeeds(self):
        with mock.patch.object(hook, 'find_tty', return_value=None), mock.patch('sys.stdin', io.StringIO('')):
            self.assertEqual(hook.main(['working']), 0)
