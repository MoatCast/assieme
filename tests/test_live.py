"""Suggestions: what needs you, and the fix for each alert."""
import tempfile
import unittest

from assieme import live, status
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


if __name__ == '__main__':
    unittest.main()
