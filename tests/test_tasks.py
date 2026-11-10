"""The task board: the store, what a merge does to cards, the board view and the agents' side."""
import os
import tempfile
import unittest
from unittest import mock

from assieme import serve, tasks, team


def pane(role, state='working', **over):
    return {'role': role, 'address': f't-{role}', 'state': state, 'since_s': 40, 'detail': '', 'fix': None,
            'live': True, 'reachable': True} | over


class FakeRt:
    def __init__(self):
        self.sent = []

    def send(self, t, text):
        self.sent.append((t, text))


class FakeBrain:
    def __init__(self, panes):
        self.panes, self.rt = panes, FakeRt()

    def map(self, name):
        return {'panes': self.panes, 'spend': {'tokens': 1200, 'waste_share': 0.1}}

    def _resolve(self, name, now):
        return None, None, None, self.rt

    def _assieme_rows(self, name=None):
        return [{'state': 'live', 'labels': {team.TEAM: 'shop', team.ROLE: p['role']}} for p in self.panes]


class FakeReviews:
    def __init__(self, cards=None):
        self.cards = cards or {}

    def card(self, name, role):
        return self.cards.get(role)


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = mock.patch.dict(os.environ, {'XDG_STATE_HOME': d.name})
        p.start()
        self.addCleanup(p.stop)
        log = mock.patch.object(tasks.live, 'record')
        self.recorded = log.start()
        self.addCleanup(log.stop)
        self.brain = FakeBrain([pane('planner'), pane('builder-1')])
        self.reviews = FakeReviews()
        me = lambda address: ('shop', {'t1': 'planner', 't2': 'builder-1'}[address], {})
        self.board = tasks.Board(self.brain, self.reviews, me)


class Store(Base):
    def test_cards_get_short_ids_and_start_in_to_do(self):
        a = tasks.add('shop', '  Backoff in\n retry.py ', 'builder-1', by='planner', now=10)
        b = tasks.add('shop', 'Metrics', now=10)
        self.assertEqual((a['id'], a['title'], a['role'], a['status'], a['by']), ('1', 'Backoff in retry.py', 'builder-1', 'todo', 'planner'))
        self.assertEqual((b['id'], b['role'], b['by']), ('2', None, 'you'))
        self.assertEqual(tasks.board('other')['cards'], [])

    def test_a_card_needs_a_title_and_a_real_status(self):
        with self.assertRaisesRegex(ValueError, 'what needs doing'):
            tasks.add('shop', '   ')
        tasks.add('shop', 'a')
        with self.assertRaisesRegex(ValueError, 'status is one of'):
            tasks.move('shop', '1', 'finished')
        with self.assertRaisesRegex(ValueError, 'No task 9'):
            tasks.move('shop', '9', 'done')
        self.assertEqual(tasks.board('shop')['cards'][0]['status'], 'todo')

    def test_moving_keeps_the_note_unless_one_is_given(self):
        tasks.add('shop', 'a', now=10)
        tasks.move('shop', '#1', 'doing', note='half way', needs_you=True, now=20)
        c = tasks.move('shop', '1', 'review', now=30)
        self.assertEqual((c['status'], c['note'], c['needs_you'], c['moved_ms']), ('review', 'half way', False, 30_000))
        self.assertEqual(tasks.move('shop', '1', 'review', note='', now=40)['moved_ms'], 30_000)

    def test_remove_and_a_fresh_board(self):
        tasks.add('shop', 'a')
        tasks.add('shop', 'b')
        tasks.remove('shop', '1')
        self.assertEqual([c['id'] for c in tasks.board('shop')['cards']], ['2'])
        tasks.fresh('shop', '\nRetry with backoff. Planner: split it.\nand more words\n')
        self.assertEqual(tasks.board('shop'), {'title': 'Retry with backoff', 'next': 1, 'cards': []})
        tasks.fresh('shop', "Fix greet.py: 'helo' should be 'hello'")
        self.assertEqual(tasks.board('shop')['title'], "Fix greet.py: 'helo' should be 'hello'")

    def test_a_damaged_file_reads_as_an_empty_board(self):
        os.makedirs(os.path.dirname(tasks._file()))
        with open(tasks._file(), 'w') as f:
            f.write('{"shop": {"cards": "no"}}')
        self.assertEqual(tasks.add('shop', 'a')['id'], '1')

    def test_a_card_said_again_after_a_merge_is_left_alone_by_undo(self):
        tasks.add('shop', 'a', 'builder-1')
        tasks.move('shop', '1', 'review')
        tasks.merged('shop', 'builder-1', 'main', 'u1')
        tasks.move('shop', '1', 'done', note='shipped')
        tasks.unmerged('shop', 'u1')
        self.assertEqual(tasks.board('shop')['cards'][0]['status'], 'done')


class TheBoard(Base):
    def test_cards_are_joined_with_what_is_true_now(self):
        fix = {'id': 'f1', 'text': 'Seems stuck.', 'buttons': []}
        self.brain.panes[1] = pane('builder-1', 'stuck', fix=fix)
        self.reviews.cards['planner'] = {'ahead': 2, 'files': 3}
        for title, role, status in (('a', 'builder-1', 'doing'), ('b', 'planner', 'review'), ('c', 'builder-1', 'todo'), ('d', None, 'review')):
            tasks.move('shop', tasks.add('shop', title, role)['id'], status)
        got = self.board.get('shop')
        a, b, c, d = got['cards']
        self.assertEqual((a['agent']['state'], a['agent']['fix']), ('stuck', fix))
        self.assertEqual(b['branch'], {'ahead': 2, 'files': 3})
        self.assertNotIn('agent', c)  # its agent's state belongs to the card it is doing
        self.assertNotIn('branch', d)
        self.assertEqual((got['spend']['tokens'], a['moved_s'] < 5), (1200, True))

    def test_a_new_task_for_a_running_agent_is_sent_to_it(self):
        got = self.board.add('shop', 'Add metrics', 'builder-1')
        self.assertTrue(got['told'])
        self.assertEqual(self.brain.rt.sent[0][0], 't-builder-1')
        self.assertIn('card 1 on the board: Add metrics', self.brain.rt.sent[0][1])
        self.recorded.assert_called_once()
        self.assertFalse(self.board.add('shop', 'Later')['told'])
        self.assertFalse(self.board.add('shop', 'For someone gone', 'tester')['told'])
        self.assertEqual(len(self.brain.rt.sent), 1)

    def test_the_app_reaches_it_through_the_brain(self):
        h = self.board.handlers()
        call = lambda method, **params: serve.handle({'id': 1, 'method': method, 'params': params}, h)
        self.assertEqual(call('board.add', team='shop', title='a')['result']['card']['id'], '1')
        self.assertEqual(call('board.move', team='shop', id='1', status='doing')['result']['status'], 'doing')
        self.assertIn('No task 7', call('board.move', team='shop', id='7', status='doing')['error']['message'])
        self.assertEqual(call('board.remove', team='shop', id='1')['result'], {'ok': True})
        self.assertEqual(call('board.get', team='shop')['result']['cards'], [])


class Agents(Base):
    def test_an_agent_sees_and_changes_only_its_own_teams_cards(self):
        tasks.add('other', 'not yours')
        made = self.board.task_add('t1', 'Backoff in retry.py', 'builder-1')['task']
        self.assertEqual((made['by'], made['role']), ('planner', 'builder-1'))
        self.assertEqual([c['title'] for c in self.board.tasks('t2')['tasks']], ['Backoff in retry.py'])
        got = self.board.task_set('t2', made['id'], 'doing', 'on it', True)['task']
        self.assertEqual((got['status'], got['note'], got['needs_you']), ('doing', 'on it', True))
        self.assertEqual(tasks.board('other')['cards'][0]['status'], 'todo')
        self.assertEqual(self.brain.rt.sent, [])  # the card alone wakes nobody

    def test_an_unknown_assignee_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'No running agent named ghost. Your team: planner, builder-1'):
            self.board.task_add('t1', 'x', 'ghost')

    def test_done_with_an_unmerged_branch_goes_to_review(self):
        self.reviews.cards['builder-1'] = {'ahead': 1}
        mine = self.board.task_add('t2', 'a', 'builder-1')['task']['id']
        nobodys = self.board.task_add('t2', 'b')['task']['id']
        got = self.board.task_set('t2', mine, 'done')
        self.assertEqual(got['task']['status'], 'review')
        self.assertIn("isn't merged yet", got['detail'])
        self.assertEqual(self.board.task_set('t2', nobodys, 'done')['task']['status'], 'done')
        self.reviews.cards['builder-1'] = {'ahead': 0}
        self.assertEqual(self.board.task_set('t2', mine, 'done')['task']['status'], 'done')


if __name__ == '__main__':
    unittest.main()
