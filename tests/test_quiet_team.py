"""Quiet send for Assieme teams: delivery reaches the right core, the stand-in is safe, savings are estimated."""
import os
import unittest
from unittest import mock

from assieme import quiet, runtime
from tests.test_quiet import RECEIVER, SENDER, FakeHC, QuietTestCase, Receiver


class SessionTest(QuietTestCase):
    def hold(self):
        with mock.patch.dict(os.environ, {'HC_SESSION': 'assieme', 'HC_SOCKET': '/s/hc.sock'}):
            self.send('send', 'coord', 'suite done')

    def test_hold_records_session_and_socket(self):
        self.hold()
        [n] = quiet.held()[RECEIVER]
        self.assertEqual((n['session'], n['socket']), ('assieme', '/s/hc.sock'))

    def test_deliver_uses_the_notes_session(self):
        self.hold()
        seen = {}

        def hc(cmd, **kw):
            seen.update(kw.get('env') or {})
            return FakeHC()(cmd, **kw)
        self.assertEqual(quiet.deliver([Receiver('working')], run=hc), [('coord', 1, 'busy')])
        self.assertEqual((seen['HC_SESSION'], seen['HC_SOCKET']), ('assieme', '/s/hc.sock'))

    def test_notes_for_two_sessions_go_in_two_batches(self):
        self.hold()
        with mock.patch.dict(os.environ, {'HC_SESSION': 'other', 'HC_SOCKET': '/s/o.sock'}):
            self.send('send', 'coord', 'lint clean')
        hc = FakeHC()
        self.assertEqual(len(quiet.deliver([Receiver('working')], run=hc)), 2)
        self.assertEqual(len(hc.sends), 2)

    def test_deliver_reports_each_batch(self):
        self.hold()
        got = []
        quiet.deliver([Receiver('working')], run=FakeHC(), on_batch=lambda notes, why: got.append((len(notes), why)))
        self.assertEqual(got, [(1, 'busy')])

    def test_note_for_a_vanished_folder_stays(self):
        self.hold()
        self.assertEqual(quiet.deliver([], run=FakeHC()), [])  # nobody there and not overdue: never dropped
        self.assertEqual(len(quiet.held()[RECEIVER]), 1)

    def test_hold_note_for_an_agent(self):
        quiet.hold_note('t2', '/w/x', 'assieme: practices changed', 'assieme', '/s/hc.sock')
        [n] = quiet.held()['/w/x']
        self.assertEqual((n['from'], n['to']), ('assieme', 't2'))

    def test_shim_runs_python_with_P(self):
        with open(os.path.join(quiet.install(), 'hc')) as f:
            self.assertIn(' -P -m assieme shim "$@"', f.read())


class SavingsTest(unittest.TestCase):
    PAIR = {'from_cwd': '/w/a', 'to_cwd': '/w/b', 'label': 'a -> b'}

    def test_avoided_wakes_times_average(self):
        log = [{'action': 'deliver', 'to_cwd': '/w/b', 'from': {'/w/a': 4}}, {'action': 'deliver', 'to_cwd': '/w/b', 'from': {'/w/a': 2}}]
        edge = {'tokens': 1000, 'wakes': 10}  # 100 tokens a wake; 6 notes in 2 batches avoided 4 wakes
        self.assertAlmostEqual(quiet.savings(self.PAIR, log, edge, 8000), 0.05)

    def test_no_data_is_zero(self):
        self.assertEqual(quiet.savings(self.PAIR, [], {'tokens': 10, 'wakes': 1}, 100), 0)
        log = [{'action': 'deliver', 'to_cwd': '/w/b', 'from': {'/w/a': 3}}]
        self.assertEqual(quiet.savings(self.PAIR, log, None, 100), 0)
        self.assertEqual(quiet.savings(self.PAIR, log, {'tokens': 10, 'wakes': 0}, 100), 0)
        self.assertEqual(quiet.savings(self.PAIR, log, {'tokens': 10, 'wakes': 1}, 0), 0)


class CoreTest(QuietTestCase):
    def test_core_starts_with_shim_first_on_path(self):
        seen = {}

        def fake_run(cmd, env=None, **kw):
            seen.update(env)
            return mock.Mock(returncode=0)
        with mock.patch.object(runtime.subprocess, 'run', fake_run), mock.patch.object(runtime.shutil, 'which', return_value='/x/hc'):
            runtime.Runtime(sock=os.path.join(self.tmp.name, 'c.sock')).ensure_core()
        self.assertTrue(seen['PATH'].startswith(quiet.shim_dir() + os.pathsep))
        self.assertTrue(os.path.exists(os.path.join(quiet.shim_dir(), 'hc')))


class DeliveryRowTest(QuietTestCase):
    def test_delivery_row_names_role_and_team(self):
        quiet._hold({'at': 1, 'from_cwd': SENDER, 'from': 'x', 'to': 't2', 'to_cwd': RECEIVER, 'text': 'hi', 'role': 'kid', 'team': 'shop'})
        from assieme import live
        live.record_delivery(quiet.held()[RECEIVER], 'busy')
        [e] = live.recent_actions()
        self.assertEqual((e['agent'], e['team'], e['address']), ('kid', 'shop', 't2'))

    def test_shim_notes_who_the_receiver_is(self):
        def hc(cmd, **kw):
            if cmd[1] == 'get':
                import subprocess, json
                return subprocess.CompletedProcess(cmd, 0, json.dumps({'cwd': RECEIVER, 'labels': {'assieme.team': 'shop', 'assieme.role': 'kid'}}), '')
            return FakeHC()(cmd, **kw)
        with mock.patch('sys.stdout'):
            quiet.shim_main(['send', 'coord', 'routine'], run=hc, cwd=SENDER)
        [n] = quiet.held()[RECEIVER]
        self.assertEqual((n['role'], n['team']), ('kid', 'shop'))
