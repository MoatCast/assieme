"""Daily limits: pause and ask at the limit, never end an agent, log every pause, carry on when told."""
import datetime as dt
import json
import os

from assieme import activity, budget, panes, quiet
from tests.test_teammap import MapCase, NOW, row, session


class Rt:
    def __init__(self):
        self.interrupted, self.sent, self.killed = [], [], []

    def interrupt(self, t, timeout_s=3):
        self.interrupted.append(t)

    def send(self, t, text, timeout_s=30):
        self.sent.append((t, text))

    def kill(self, t):
        self.killed.append(t)


class BudgetCase(MapCase):
    def setUp(self):
        super().setUp()
        self.rt = Rt()
        self.rows = [row('a', 'working'), row('b', 'idle')]
        self.sessions = [session('a', tokens=700), session('b', tokens=400)]

    def built(self):
        return self.build(self.rows, sessions=self.sessions)

    def limit(self, limits, at_limit='pause'):
        b = self.built()['budget']
        return budget.set_limits('shop', limits, at_limit, self.rt, {a['role']: a['used'] for a in b['agents']}, b['used'], now=NOW)

    def check(self, now=NOW, **kw):
        return budget.check('shop', self.built(), self.rt, now, **kw)

    def rows_logged(self):
        return [r['title'] for r in activity.list_rows(team='shop', now=NOW)['rows']]


class PauseTest(BudgetCase):
    def test_under_the_limit_nothing_happens(self):
        self.limit({'*': 5000})
        self.assertEqual(self.check(), [])
        self.assertEqual(self.rt.interrupted, [])
        self.assertEqual(self.built()['budget']['asks'], [])

    def test_team_limit_stops_the_working_agent_and_asks(self):
        self.limit({'*': 1000})
        said = []
        self.check(announce=lambda title, body: said.append(title))
        self.assertEqual(self.rt.interrupted, ['t-a'])  # b was idle: nothing to press
        self.assertEqual(self.rt.killed, [])            # never ended
        self.assertEqual(said, ['Paused shop at today’s limit'])
        self.assertIn('Paused shop at today’s limit', self.rows_logged())
        m = self.built()
        self.assertEqual(m['title'], 'Paused at today’s limit')
        self.assertEqual([p['fix']['budget'] for p in m['panes']], ['*', '*'])
        self.assertEqual(m['panes'][0]['fix']['buttons'][0]['label'], 'Add 250 and carry on')
        self.assertTrue(all(n['paused'] for n in m['nodes']))

    def test_an_agent_that_seems_stuck_but_is_mid_turn_is_stopped_too(self):
        built = self.built()
        built['panes'][0]['state'] = 'stuck'  # the logs' guess; its own hook still says working
        self.limit({'*': 1000})
        budget.check('shop', built | {'budget': self.built()['budget']}, self.rt, NOW)
        self.assertEqual(self.rt.interrupted, ['t-a'])

    def test_it_pauses_once(self):
        self.limit({'*': 1000})
        self.check()
        self.check()
        self.assertEqual(self.rt.interrupted, ['t-a'])
        self.assertEqual(self.rows_logged().count('Paused shop at today’s limit'), 1)

    def test_an_agents_own_limit_pauses_only_it(self):
        self.limit({'a': 500})
        self.check()
        m = self.built()
        self.assertEqual([bool(p['fix']) for p in m['panes']], [True, False])
        self.assertIn('The others carry on', m['subtitle'])

    def test_a_one_agent_team_has_no_others_to_mention(self):
        self.rows, self.sessions = [row('a', 'working')], [session('a', tokens=700)]
        self.limit({'a': 500})
        self.check()
        self.assertEqual(self.built()['subtitle'], 'a stopped where it was.')

    def test_a_real_question_is_left_alone_until_answered(self):
        self.rows = [row('a', 'blocked')]
        self.limit({'*': 500})
        self.check()
        self.assertEqual(self.rt.interrupted, [])
        self.assertNotIn('budget', self.built()['panes'][0]['fix'])
        self.rows = [row('a', 'working')]  # the person answered: now it is stopped
        self.check()
        self.assertEqual(self.rt.interrupted, ['t-a'])

    def test_just_tell_me_never_stops_anyone_and_says_it_once_a_day(self):
        self.limit({'*': 1000}, 'tell')
        self.check()
        self.check()
        self.assertEqual(self.rt.interrupted, [])
        self.assertEqual(self.rows_logged().count('shop reached today’s limit'), 1)
        self.assertEqual(len(self.built()['budget']['told']), 1)
        self.check(now=NOW + dt.timedelta(days=1))
        self.assertEqual(self.rows_logged().count('shop reached today’s limit'), 2)

    def test_a_damaged_file_means_no_limits(self):
        os.makedirs(quiet.state_dir(), exist_ok=True)
        with open(quiet._path('budgets.json'), 'w') as f:
            json.dump({'shop': {'limits': {'*': 'lots'}, 'at_limit': 7, 'paused': []}}, f)
        self.assertEqual(budget.get('shop')['limits'], {})
        self.assertEqual(self.check(), [])

    def test_found_teams_have_no_limits(self):
        self.assertIsNone(self.build(self.rows, sessions=self.sessions, found=True)['budget'])


class CarryOnTest(BudgetCase):
    def setUp(self):
        super().setUp()
        self.limit({'*': 1000})
        self.check()

    def press(self):
        p = self.built()['panes'][0]
        return panes.act('shop', p['role'], p['fix']['id'], p['fix']['buttons'][0]['id'], self.built, self.rt, now=NOW)

    def test_the_button_tells_each_agent_to_carry_on_with_held_messages(self):
        self.assertTrue(budget.hold('shop', 'b', 'a', 'review my commit'))
        self.assertEqual(self.press(), {'ok': True, 'detail': ''})
        self.assertEqual([t for t, _ in self.rt.sent], ['t-a', 't-b'])
        self.assertIn('carry on', self.rt.sent[0][1])
        self.assertIn('- from a: review my commit', self.rt.sent[1][1])
        self.assertIn('Let shop carry on', self.rows_logged())

    def test_it_adds_a_quarter_from_now_so_it_is_not_paused_again_at_once(self):
        self.press()
        self.assertEqual(self.built()['budget']['allowed'], 1350)  # used 1100, plus a quarter of 1000
        self.check()
        self.assertEqual(self.built()['budget']['asks'], [])

    def test_a_second_press_does_nothing(self):
        p = self.built()['panes'][0]
        self.press()
        again = panes.act('shop', p['role'], p['fix']['id'], p['fix']['buttons'][0]['id'], self.built, self.rt, now=NOW)
        self.assertFalse(again['ok'])
        self.assertEqual(len(self.rt.sent), 2)

    def test_raising_the_limit_carries_on(self):
        self.limit({'*': 5000})
        self.assertEqual(len(self.rt.sent), 2)
        self.assertEqual(self.built()['budget']['asks'], [])

    def test_not_paused_means_not_held(self):
        self.press()
        self.assertFalse(budget.hold('shop', 'b', 'a', 'hello'))

    def test_an_agent_that_has_gone_is_no_longer_paused(self):
        self.rows = [row('a', state='finished'), row('b', state='finished')]
        self.check()
        self.assertEqual(budget.get('shop')['paused'], {})
