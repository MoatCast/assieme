"""A3 quiet send: the hc stand-in, the hold queue and delivery. Fake hc, temp state folder and PATH only."""
import json
import os
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from assieme import cli, live, quiet, status

SENDER, RECEIVER = '/w/builder', '/w/coord'


class FakeHC:
    """Records calls. `hc get <t> --json` answers with the receiver's folder."""

    def __init__(self, code=0, cwd=None):
        self.calls, self.code, self.cwd = [], code, cwd

    def __call__(self, cmd, **kw):
        self.calls.append(cmd[1:])
        if cmd[1] == 'get':
            return subprocess.CompletedProcess(cmd, 0, json.dumps({'cwd': self.cwd or {'coord': RECEIVER}.get(cmd[2], '/w/other')}), '')
        return subprocess.CompletedProcess(cmd, self.code, '{"started":true}', '')

    @property
    def sends(self):
        return [c for c in self.calls if c[0] == 'send']


class QuietTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        bin_dir = os.path.join(self.tmp.name, 'bin')
        os.makedirs(bin_dir)
        open(os.path.join(bin_dir, 'hc'), 'w').close()
        os.chmod(os.path.join(bin_dir, 'hc'), 0o755)
        self.env = mock.patch.dict(os.environ, {'XDG_STATE_HOME': os.path.join(self.tmp.name, 'state'),
                                                'PATH': os.path.join(self.tmp.name, 'state', 'assieme', 'bin') + os.pathsep + bin_dir})
        self.env.start()
        quiet.set_pair(SENDER, RECEIVER, 'builder -> coord')
        quiet.heartbeat()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def send(self, *args, cwd=SENDER):
        hc = FakeHC()
        with mock.patch('sys.stdout'):  # the shim's "held" line is for the agent, not the test log
            code = quiet.shim_main(list(args), run=hc, cwd=cwd)
        return code, hc


class ShimTest(QuietTestCase):
    def test_other_commands_pass_through(self):
        code, hc = self.send('read', 't3', '--lines', '50')
        self.assertEqual((code, hc.calls), (0, [['read', 't3', '--lines', '50']]))

    def test_quiet_pair_is_held(self):
        code, hc = self.send('send', 'coord', 'suite done, all green')
        self.assertEqual((code, hc.sends), (0, []))
        [note] = quiet.held()[RECEIVER]
        self.assertEqual((note['from'], note['to'], note['text']), ('builder', 'coord', 'suite done, all green'))

    def test_sender_in_a_subfolder_counts(self):
        self.send('send', 'coord', 'done', cwd=SENDER + '/tests')
        self.assertEqual(len(quiet.held()[RECEIVER]), 1)

    def test_what_always_goes_now(self):
        for args in (['send', 'coord', 'Can you review this?'], ['send', 'coord', 'URGENT: build broken'],
                     ['send', '--now', 'coord', 'done'], ['send', '--if', 'idle', 'coord', 'done'],
                     ['send', 'steward', 'done']):  # not a quiet pair
            code, hc = self.send(*args)
            self.assertEqual(len(hc.sends), 1, args)
            self.assertNotIn('--now', hc.sends[0])
        self.assertEqual(quiet.held(), {})

    def test_no_deliverer_means_no_holding(self):
        os.utime(quiet._path('deliverer.alive'), (time.time() - 600, time.time() - 600))
        code, hc = self.send('send', 'coord', 'done')
        self.assertEqual(len(hc.sends), 1)
        self.assertEqual(quiet.held(), {})


class Receiver:
    def __init__(self, state):
        self.cwd, self.state = RECEIVER, state


class DeliverTest(QuietTestCase):
    def hold(self, minutes_ago, text='done'):
        self.send('send', 'coord', text)
        notes = quiet._read_json(quiet._path('held.json'), [])
        notes[-1]['at'] = time.time() - 60 * minutes_ago
        quiet._write_json(quiet._path('held.json'), notes)

    def test_busy_receiver_gets_one_batch(self):
        self.hold(3, 'suite done')
        self.hold(1, 'lint clean')
        hc = FakeHC()
        self.assertEqual(quiet.deliver([Receiver('working')], run=hc), [('coord', 2, 'busy')])
        [send] = hc.sends
        self.assertEqual(send[1], 'coord')
        self.assertIn('2 quiet notes held while you were busy', send[2])
        self.assertIn('suite done', send[2])
        self.assertEqual(quiet.held(), {})

    def test_agents_sharing_a_folder_each_get_their_own_notes(self):
        quiet.hold_note('coord', RECEIVER, 'for coord', None, None)
        quiet.hold_note('coord-2', RECEIVER, 'for coord-2', None, None)
        hc = FakeHC(cwd=RECEIVER)
        self.assertEqual(sorted(quiet.deliver([Receiver('working')], run=hc)), [('coord', 1, 'busy'), ('coord-2', 1, 'busy')])
        sends = dict((c[1], c[2]) for c in hc.sends)
        self.assertIn('for coord-2', sends['coord-2'])
        self.assertNotIn('for coord-2', sends['coord'])

    def test_idle_receiver_waits_then_gets_it_anyway(self):
        self.hold(5)
        self.assertEqual(quiet.deliver([Receiver('idle')], run=FakeHC()), [])
        self.hold(25)
        self.assertEqual(quiet.deliver([Receiver('idle')], run=FakeHC()), [('coord', 2, 'overdue')])

    def test_failed_or_gone_keeps_the_notes(self):
        self.hold(30)
        self.assertEqual(quiet.deliver([Receiver('working')], run=FakeHC(code=1)), [])
        self.assertEqual(quiet.deliver([Receiver('exited')], run=FakeHC()), [])
        self.assertEqual(len(quiet.held()[RECEIVER]), 1)

    def test_a_reused_address_gets_nothing(self):
        self.hold(30)
        hc = FakeHC(cwd='/some/other/agent')
        self.assertEqual(quiet.deliver([Receiver('working')], run=hc), [])
        self.assertEqual(hc.sends, [])
        self.assertEqual(len(quiet.held()[RECEIVER]), 1)

    def test_notes_for_a_gone_agent_are_given_up_after_a_day(self):
        self.hold(60 * 23)
        given_up = []
        self.assertEqual(quiet.deliver([Receiver('exited')], run=FakeHC(), on_give_up=given_up.append), [])
        self.assertEqual(given_up, [])
        self.hold(60 * 25)
        quiet.deliver([Receiver('exited')], run=FakeHC(), on_give_up=given_up.append)
        self.assertEqual(len(given_up[0]), 2)
        self.assertEqual(quiet.held(), {})

    def test_note_arriving_during_delivery_is_kept(self):
        self.hold(3, 'first')

        def hc(cmd, **kw):
            if cmd[1] == 'send':  # another agent sends while this delivery is under way
                quiet._hold({'at': time.time(), 'from_cwd': SENDER, 'from': 'builder', 'to': 'coord',
                             'to_cwd': RECEIVER, 'text': 'second'})
            return FakeHC()(cmd, **kw)
        quiet.deliver([Receiver('working')], run=hc)
        self.assertEqual([n['text'] for n in quiet.held()[RECEIVER]], ['second'])


class LiveQuietTest(QuietTestCase):
    def test_send_less_pair_gets_a_quiet_key(self):
        quiet.set_pair(SENDER, RECEIVER, 'builder -> coord', on=False)
        b, c = status.Agent('builder', 'x', ''), status.Agent('coord', 'x', '')
        b.cwd, c.cwd, b.last, c.last = SENDER, RECEIVER, None, None
        x = {'from': 'builder', 'to': 'coord', 'empty': 198, 'wakes': 252, 'empty_tokens': 82e6}
        [s] = live.suggest([b, c], [], {}, send_less=[x])
        self.assertEqual((s.action, s.pair), ('quiet', (SENDER, RECEIVER, 'builder -> coord')))
        self.assertTrue(live.run_action(s, log=os.path.join(self.tmp.name, 'a.jsonl'))[0])
        self.assertTrue(quiet.is_quiet(SENDER, RECEIVER))
        self.assertEqual(live.suggest([b, c], [], {}, send_less=[x]), [])  # already quiet: gone


class TeamTest(QuietTestCase):
    def test_on_installs_a_shim_that_runs_assieme(self):
        d = quiet.install()
        with open(os.path.join(d, 'hc')) as f:
            body = f.read()
        self.assertIn('-m assieme shim "$@"', body)
        self.assertTrue(os.access(os.path.join(d, 'hc'), os.X_OK))
        self.assertNotEqual(os.path.realpath(quiet.real_hc()), os.path.realpath(os.path.join(d, 'hc')))
        quiet.uninstall()
        self.assertFalse(os.path.exists(os.path.join(d, 'hc')))

    def test_install_writes_the_push_blocker_git_runs(self):
        d = quiet.install()
        p = os.path.join(d, 'git-remote-assieme-push-blocked')
        self.assertTrue(os.access(p, os.X_OK))
        r = subprocess.run([p, 'origin', 'assieme-push-blocked://x'], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Assieme blocks pushes for this team', r.stdout + r.stderr)
        quiet.uninstall()
        self.assertFalse(os.path.exists(p))

    def test_cli_routes_shim_before_option_parsing(self):
        with mock.patch.object(quiet, 'shim_main', return_value=0) as m:
            cli.main(['shim', 'send', '--if', 'idle', 'coord', 'x'])
        m.assert_called_once_with(['send', '--if', 'idle', 'coord', 'x'])


if __name__ == '__main__':
    unittest.main()


class EndToEndShellTest(unittest.TestCase):
    def test_real_shim_through_a_shell(self):
        with tempfile.TemporaryDirectory() as root:
            builder, coord, fake_bin = (os.path.join(root, n) for n in ('builder', 'coord', 'bin'))
            for d in (builder, coord, fake_bin):
                os.makedirs(d)
            log = os.path.join(root, 'hc.log')
            with open(os.path.join(fake_bin, 'hc'), 'w') as f:
                f.write(f'#!/bin/sh\necho "$@" >> "{log}"\n'
                        f'if [ "$1" = get ]; then echo \'{{"cwd": "{coord}"}}\'; fi\n')
            os.chmod(os.path.join(fake_bin, 'hc'), 0o755)
            env = dict(os.environ, XDG_STATE_HOME=os.path.join(root, 'state'),
                       PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            with mock.patch.dict(os.environ, env):
                shim = quiet.install()
                quiet.set_pair(builder, coord, 'builder -> coord')
                quiet.heartbeat()
            env['PATH'] = shim + os.pathsep + fake_bin + os.pathsep + os.environ['PATH']
            sh = lambda c: subprocess.run(['sh', '-c', c], cwd=builder, env=env, capture_output=True, text=True)
            held = sh('hc send coord "suite done"')
            sh('hc ls --json')
            sh('hc send coord "Ready for review?"')
            with open(log) as f:
                calls = f.read().splitlines()
            with mock.patch.dict(os.environ, env):
                notes = quiet.held()
        self.assertIn('"held": true', held.stdout)
        self.assertEqual(calls, ['get coord --json', 'ls --json', 'send coord Ready for review?'])
        self.assertEqual([n['text'] for n in notes[os.path.realpath(coord)]], ['suite done'])
