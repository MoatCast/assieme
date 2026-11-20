"""One automatic fix per problem, with the problem measured."""
import datetime as dt
import os
import tempfile
import threading
import unittest

import importlib

from assieme import activity, fixes, settings, teammap

NOW = dt.datetime(2026, 11, 8, 12, 0, tzinfo=dt.timezone.utc)
AUTO = settings.defaults()


def stuck(role, since_s=540, detail='silent 9 min'):
    return {'role': role, 'address': f't-{role}', 'state': 'stuck', 'since_s': since_s, 'detail': detail,
            'memory_full': False,
            'fix': {'text': 'It has gone quiet.', 'buttons': [{'kind': 'action', 'action': 'nudge', 'send': 'continue'}]}}


def working(role):
    return {'role': role, 'address': f't-{role}', 'state': 'working', 'since_s': 5, 'detail': '', 'fix': None}


def views(*panes):
    return {'shop': {'found': False, 'panes': list(panes)}}


def view_aged(view, extra_s):
    return views(*[dict(p, since_s=p['since_s'] + extra_s) for p in view['shop']['panes']])


class FixesCase(unittest.TestCase):
    def setUp(self):
        try:
            os.remove(fixes.state_path())
        except OSError:
            pass
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, 'actions.jsonl')
        sent = self.sent = []
        lock = threading.Lock()

        class Rt:
            def send(self, t, text):
                with lock:
                    sent.append(t)

            def sent_to(self, t):
                return sent.count(t)
        self.rt = Rt()

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_nudge_per_problem_even_after_15_minutes(self):
        view = views(stuck('builder-3', since_s=540))
        teammap.run_automatic(view, AUTO, self.rt, log=self.log, now=NOW)
        teammap.run_automatic(view_aged(view, 1200), AUTO, self.rt, log=self.log, now=NOW + dt.timedelta(minutes=20))
        self.assertEqual(self.rt.sent_to('t-builder-3'), 1)

    def test_a_new_problem_after_working_gets_a_fresh_attempt(self):
        teammap.run_automatic(views(stuck('builder-3', since_s=540)), AUTO, self.rt, log=self.log, now=NOW)
        later = NOW + dt.timedelta(hours=1)
        teammap.run_automatic(views(stuck('builder-3', since_s=300)), AUTO, self.rt, log=self.log, now=later)
        self.assertEqual(self.rt.sent_to('t-builder-3'), 2)

    def test_fix_row_carries_the_measured_problem(self):
        e = teammap.run_automatic(views(stuck('builder-3', since_s=540, detail='same test failed 4 times')), AUTO, self.rt,
                                  log=self.log, now=NOW, tokens={'builder-3': (52000, 14000)})[0]
        self.assertEqual(e['measured'], {'minutes': 9, 'what': 'same test failed 4 times', 'tokens': 38000})
        self.assertEqual(e['check_until'], '2026-11-08T12:10:00Z')
        self.assertEqual(e['problem'], 'shop/builder-3/nudge/20261108T1151')

    def test_two_ticks_at_once_send_one_nudge(self):
        view = views(stuck('builder-3', since_s=540))
        ts = [threading.Thread(target=teammap.run_automatic, args=(view, AUTO, self.rt),
                               kwargs={'log': self.log, 'now': NOW}) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(self.rt.sent_to('t-builder-3'), 1)

    def test_jitter_across_a_minute_boundary_is_still_one_problem(self):
        teammap.run_automatic(views(stuck('builder-3', since_s=541)), AUTO, self.rt, log=self.log, now=NOW)  # started 11:50:59
        later = NOW + dt.timedelta(seconds=5)
        teammap.run_automatic(views(stuck('builder-3', since_s=545)), AUTO, self.rt, log=self.log, now=later)  # 11:51:00
        self.assertEqual(self.rt.sent_to('t-builder-3'), 1)

    def test_measure_without_tokens(self):
        self.assertEqual(fixes.measure(stuck('a'), None, 5), {'minutes': 9, 'what': 'silent 9 min', 'tokens': None})

    def test_start_tokens_are_remembered_per_problem(self):
        self.assertEqual(fixes.start_tokens('shop', 'a', NOW - dt.timedelta(minutes=9), 14000, NOW), 14000)
        self.assertEqual(fixes.start_tokens('shop', 'a', NOW - dt.timedelta(minutes=9), 20000, NOW), 14000)
        later = NOW + dt.timedelta(hours=1)  # a new problem starts after the old one was seen
        self.assertEqual(fixes.start_tokens('shop', 'a', later - dt.timedelta(minutes=2), 30000, later), 30000)

    def test_damaged_state_reads_as_empty(self):
        os.makedirs(os.path.dirname(fixes.state_path()), exist_ok=True)
        with open(fixes.state_path(), 'w') as f:
            f.write('{nope')
        self.assertEqual(fixes.start_tokens('shop', 'a', NOW, 7, NOW), 7)


def local(minutes):
    return (NOW + dt.timedelta(minutes=minutes)).astimezone().strftime('%H:%M')


class OutcomeCase(unittest.TestCase):
    setUp, tearDown = FixesCase.setUp, FixesCase.tearDown

    def nudge_at(self, now):
        return teammap.run_automatic(views(stuck('builder-3')), AUTO, self.rt, log=self.log, now=now)[0]

    def row_of(self, id):
        rows = activity.list_rows('shop', path=self.log, now=NOW + dt.timedelta(hours=3))['rows']
        return next(r for r in rows if r['id'] == id)

    def test_nudge_is_fixed_when_the_agent_works_again(self):
        fix = self.nudge_at(NOW)
        fixes.check_outcomes(views(working('builder-3')), self.rt, log=self.log, now=NOW + dt.timedelta(minutes=4))
        row = self.row_of(fix['id'])
        self.assertEqual((row['outcome'], row['outcome_at'], row['agent']), ('fixed', local(4), 'builder-3'))
        self.assertEqual(row['measured']['minutes'], 9)

    def test_still_stuck_after_ten_minutes(self):
        fix = self.nudge_at(NOW)
        fixes.check_outcomes(views(stuck('builder-3', since_s=1200)), self.rt, log=self.log, now=NOW + dt.timedelta(minutes=10))
        self.assertEqual(self.row_of(fix['id'])['outcome'], 'still_stuck')

    def test_not_judged_before_the_time_limit(self):
        self.nudge_at(NOW)
        out = fixes.check_outcomes(views(stuck('builder-3', since_s=300)), self.rt, log=self.log, now=NOW + dt.timedelta(minutes=3))
        self.assertEqual(out, [])

    def test_checking_until_the_time_limit(self):
        fix = self.nudge_at(NOW)
        rows = activity.list_rows('shop', path=self.log, now=NOW + dt.timedelta(minutes=3))['rows']
        self.assertEqual([(r['outcome'], r['check_until']) for r in rows if r['id'] == fix['id']], [('checking', local(10))])

    def test_an_exited_agent_is_not_checking_forever(self):
        fix = self.nudge_at(NOW)
        out = fixes.check_outcomes(views(), self.rt, log=self.log, now=NOW + dt.timedelta(minutes=11))
        self.assertEqual(out, [])
        self.assertIsNone(self.row_of(fix['id'])['outcome'])

    def test_outcome_survives_a_brain_restart(self):
        fix = self.nudge_at(NOW)
        importlib.reload(fixes)  # nothing kept in memory
        fixes.check_outcomes(views(working('builder-3')), self.rt, log=self.log, now=NOW + dt.timedelta(minutes=2))
        self.assertEqual(self.row_of(fix['id'])['outcome'], 'fixed')

    def test_one_outcome_per_fix(self):
        self.nudge_at(NOW)
        w = views(working('builder-3'))
        fixes.check_outcomes(w, self.rt, log=self.log, now=NOW + dt.timedelta(minutes=1))
        self.assertEqual(fixes.check_outcomes(w, self.rt, log=self.log, now=NOW + dt.timedelta(minutes=2)), [])

    def tell_pane(self, refused=(), buttons=None):
        iso = lambda m: (NOW + dt.timedelta(minutes=m)).strftime('%Y-%m-%dT%H:%M:%SZ')
        send = 'assieme: `make deploy` has been refused 2 times by rule. Do not retry it.'
        return dict(stuck('builder-3'), refused=[(iso(m), 'make deploy') for m in refused], fix={'text': 'x', 'buttons': buttons or [
            {'kind': 'action', 'action': 'tell', 'send': send}]})

    def tell_at(self):
        a = dict(AUTO, fixes=dict(AUTO['fixes'], tell='auto'))
        return teammap.run_automatic(views(self.tell_pane([-25, -1])), a, self.rt, log=self.log, now=NOW)[0]

    def test_an_obeyed_tell_is_fixed_though_old_refusals_stay_in_the_window(self):
        fix = self.tell_at()
        fixes.check_outcomes(views(self.tell_pane([-25, -1])), self.rt, log=self.log, now=NOW + dt.timedelta(minutes=10))
        self.assertEqual(self.row_of(fix['id'])['outcome'], 'fixed')

    def test_a_tell_refused_again_is_still_stuck_even_when_old_refusals_age_out(self):
        fix = self.tell_at()
        later = views(self.tell_pane([-25, -1, 5], buttons=[]))  # the loop's button is gone: one refusal is left in the window
        fixes.check_outcomes(later, self.rt, log=self.log, now=NOW + dt.timedelta(minutes=10))
        self.assertEqual(self.row_of(fix['id'])['outcome'], 'still_stuck')

    def test_a_warning_tell_offered_later_is_not_a_refusal(self):
        fix = self.tell_at()
        warn = [{'kind': 'action', 'action': 'tell', 'send': 'assieme: your context is growing fast.'}]
        fixes.check_outcomes(views(self.tell_pane([-25, -1], buttons=warn)), self.rt, log=self.log, now=NOW + dt.timedelta(minutes=10))
        self.assertEqual(self.row_of(fix['id'])['outcome'], 'fixed')

    def test_a_tell_from_before_this_field_gets_no_outcome(self):
        from assieme import live
        old = live.record('tell', 'builder-3', 'x', team='shop', by='auto', log=self.log, now=NOW, problem='k',
                          check_until=fixes.check_until(NOW))
        out = fixes.check_outcomes(views(self.tell_pane()), self.rt, log=self.log, now=NOW + dt.timedelta(minutes=10))
        self.assertEqual((out, self.row_of(old['id'])['outcome']), ([], None))

    def test_outcome_entries_are_not_rows(self):
        self.nudge_at(NOW)
        fixes.check_outcomes(views(working('builder-3')), self.rt, log=self.log, now=NOW + dt.timedelta(minutes=1))
        actions = [r['action'] for r in activity.list_rows('shop', path=self.log, now=NOW)['rows']]
        self.assertNotIn('outcome', actions)

    def test_other_rows_have_no_outcome(self):
        from assieme import live
        live.record('send', 'a', 'x', team='shop', log=self.log, now=NOW)
        [r] = activity.list_rows('shop', path=self.log, now=NOW)['rows']
        self.assertEqual((r['outcome'], r['outcome_at'], r['check_until'], r['measured'], r['problem']), (None,) * 5)


if __name__ == '__main__':
    unittest.main()
