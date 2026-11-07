"""The live view: suggestions, one-key nudges through a fake hc, and the action log. No real hc is called."""
import json
import os
import subprocess
import tempfile
import unittest

from wakegraph import live, status
from tests.test_status import NOW, StallTest, hc_wake, prompt, result, team, tool


class SuggestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        c, x = team(self.tmp.name)
        self.agents = status.collect(c, x, NOW)
        self.addr = {'b': 't2', 'c': 't3', 'f': 't6'}
        self.sugg = live.suggest(self.agents, status.stalls(self.agents, NOW), self.addr)

    def tearDown(self):
        self.tmp.cleanup()

    def test_order_and_fixes(self):
        got = [(s.agent, s.action) for s in self.sugg]
        self.assertEqual(set(got[:2]), {('d', None), ('codex:api', None)})  # what waits on you comes first
        self.assertIn(('b', 'nudge'), got)
        self.assertIn(('f', 'nudge'), got)
        c = next(s for s in self.sugg if s.agent == 'c')
        self.assertIn('same command several times', c.message)
        self.assertEqual(next(s for s in self.sugg if s.agent == 'd').hint, 'answer it in its terminal')

    def test_render_marks_what_can_be_done(self):
        text = live.render(self.agents, [], self.sugg, NOW)
        self.assertIn('press', text)
        self.assertIn('nudge it yourself (not in herdr)', text)  # h is stuck but not in herdr
        self.assertNotIn('secret', text)
        self.assertIn('Nothing needs you right now.', live.render([], [], [], NOW))


class StallRootTest(unittest.TestCase):
    def test_idle_blocker_gets_a_nudge(self):
        agents, stalls = StallTest().run_team({
            'coord': [hc_wake(19, 'builder'), tool(16, 'c1', command='hc send steward "need a ruling"'),
                      result(16, 'c1'), {'type': 'assistant', 'timestamp': prompt(15)['timestamp'], 'message': {
                          'id': 'e', 'stop_reason': 'end_turn', 'usage': {'input_tokens': 1}, 'content': []}}],
            # steward's turn was already under way when coord's request came, and it ended without reading it
            'steward': [prompt(20), {'type': 'assistant', 'timestamp': prompt(14)['timestamp'], 'message': {
                'id': 'e2', 'stop_reason': 'end_turn', 'usage': {'input_tokens': 1}, 'content': []}}],
        })
        sugg = live.suggest(agents, stalls, {'steward': 't1'})
        self.assertEqual((sugg[0].agent, sugg[0].action, sugg[0].address), ('steward', 'nudge', 't1'))
        self.assertIn('coord are waiting on you', sugg[0].message)


class ActionTest(unittest.TestCase):
    def test_nudge_goes_through_hc_send_and_is_logged(self):
        calls = []
        runner = lambda cmd: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, 'started\n', '')
        s = live.Suggestion('b is stuck: silent 20 min', 'b', 't2', 'nudge', 'wakegraph: hello')
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, 'state', 'actions.jsonl')
            self.assertEqual(live.run_action(s, runner, log, NOW), (True, 'started'))
            self.assertEqual(calls, [['hc', 'send', 't2', 'wakegraph: hello']])
            entry = live.recent_actions(log)[-1]
        self.assertEqual((entry['action'], entry['agent'], entry['ok'], entry['reason']),
                         ('nudge', 'b', True, 'b is stuck: silent 20 min'))

    def test_no_address_means_no_send(self):
        runner = lambda cmd: self.fail('must not call hc')
        with tempfile.TemporaryDirectory() as d:
            ok, detail = live.run_action(live.Suggestion('x', 'h', None, 'nudge', 'm'), runner, os.path.join(d, 'a.jsonl'))
        self.assertFalse(ok)
        self.assertIn('not running in herdr', detail)

    def test_failure_is_reported(self):
        runner = lambda cmd: subprocess.CompletedProcess(cmd, 1, '', 'no such terminal t9\n')
        with tempfile.TemporaryDirectory() as d:
            ok, detail = live.run_action(live.Suggestion('x', 'b', 't9', 'nudge', 'm'), runner, os.path.join(d, 'a.jsonl'))
        self.assertEqual((ok, detail), (False, 'no such terminal t9'))


if __name__ == '__main__':
    unittest.main()
