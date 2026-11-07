"""F9 owner's queue and notifications. Synthetic logs, a temp state folder and a fake notifier only."""
import datetime as dt
import os
import tempfile
import unittest

from wakegraph import live, status
from tests.test_status import NOW, StallTest, ago, hc_wake, prompt, result, tool


def reply(m, text, stop='end_turn'):
    return {'type': 'assistant', 'timestamp': ago(m), 'message': {
        'id': f'r{m}', 'stop_reason': stop, 'usage': {'input_tokens': 1}, 'content': [{'type': 'text', 'text': text}]}}


class QuestionTest(unittest.TestCase):
    def team(self, rows):
        return {a.name: a for a in StallTest().run_team(rows)[0]}

    def test_question_to_a_person_is_yours(self):
        by = self.team({'lab': [prompt(10), reply(8, 'Two options: A or B. Which do you want?')],
                        'other': [prompt(10), reply(8, 'Done.')]})
        self.assertEqual((by['lab'].state, by['lab'].ask_kind), ('waiting on you', 'a question in its reply'))
        self.assertEqual(by['other'].state, 'idle')

    def test_question_to_another_agent_is_not(self):
        by = self.team({'builder': [hc_wake(10, 'coord'), reply(8, 'Want me to run the full suite?')],
                        'coord': [prompt(30), reply(29, 'ok')]})
        self.assertEqual(by['builder'].state, 'idle')

    def test_question_followed_by_work_is_not_waiting(self):
        by = self.team({'lab': [prompt(10), reply(9, 'Shall I check?', stop='tool_use'),
                                tool(9, 'x1', command='ls'), result(9, 'x1'), reply(8, 'All good.')],
                        'other': [prompt(10), reply(8, 'Done.')]})
        self.assertEqual(by['lab'].state, 'idle')


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.agents, self.stalls = StallTest().run_team({
            'builder': [prompt(25), tool(21, 'b1', command='hc send coord "suite done"'), result(21, 'b1'),
                        {'type': 'assistant', 'timestamp': ago(20), 'message': {
                            'id': 'e', 'stop_reason': 'end_turn', 'usage': {'input_tokens': 1}, 'content': []}}],
            'coord': [hc_wake(19, 'builder'), tool(16, 'c1', command='hc send steward "need a ruling"'),
                      result(16, 'c1'), {'type': 'assistant', 'timestamp': ago(15), 'message': {
                          'id': 'e2', 'stop_reason': 'end_turn', 'usage': {'input_tokens': 1}, 'content': []}}],
            'steward': [hc_wake(14, 'coord'), tool(10, 's1', name='AskUserQuestion')],
        })
        self.queue = status.owner_queue(self.agents, NOW)

    def test_queue_says_what_it_holds_up(self):
        self.assertEqual(len(self.queue), 1)
        q = self.queue[0]
        self.assertEqual((q['agent'], q['kind'], q['seconds'], q['blocks']),
                         ('steward', 'a question', 600, ['builder', 'coord']))

    def test_live_view_lists_it_under_needs_you(self):
        sugg = live.suggest(self.agents, self.stalls, {'steward': 't2'}, self.queue)
        text = live.render(self.agents, self.stalls, sugg, NOW)
        self.assertIn('Needs you (1):', text)
        self.assertIn('[1] steward: a question, waiting 10 min; holds up builder, coord  -> answer it in hc attach t2', text)

    def test_notifies_once_and_survives_restart(self):
        sent = []
        fake = lambda title, body: sent.append((title, body))
        with tempfile.TemporaryDirectory() as d:
            store = os.path.join(d, 'notified.json')
            first = live.announce(self.queue, self.stalls, NOW, store, fake)
            again = live.announce(self.queue, self.stalls, NOW + dt.timedelta(minutes=1), store, fake)
        self.assertEqual([t for t, _ in first], ['steward needs you', 'Team stalled'])
        self.assertEqual(sent[0], ('wakegraph: steward needs you', 'a question, waiting 10 min; holds up builder, coord'))
        self.assertEqual(again, [])

    def test_old_items_do_not_pop_up(self):
        old = [dict(self.queue[0], seconds=3 * 3600, key='old')]
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(live.announce(old, [], NOW, os.path.join(d, 'n.json'), lambda *a: None), [])


if __name__ == '__main__':
    unittest.main()
