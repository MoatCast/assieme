"""One automatic fix per problem, with the problem measured."""
import datetime as dt
import os
import tempfile
import threading
import unittest

from assieme import fixes, settings, teammap

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


if __name__ == '__main__':
    unittest.main()
