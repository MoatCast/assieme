"""Scheduled runs: once per planned time, once on wake, skipped while still going, ended by the top agent's done."""
import datetime as dt
import os
import shutil
import tempfile
import unittest
from unittest import mock

from assieme import activity, mcptools, newteam, schedule, team
from tests.test_newteam import git_repo, role

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 9, 12, 0, tzinfo=UTC)  # a Monday
DAY = {'kind': 'day', 'at': '02:00'}


def at(days=0, hours=0, minutes=0):
    return T0 + dt.timedelta(days=days, hours=hours, minutes=minutes)


class TimeTest(unittest.TestCase):
    def test_daily_waits_for_its_first_time(self):
        self.assertIsNone(schedule.latest(DAY, T0, at(hours=13), UTC))
        self.assertEqual(schedule.latest(DAY, T0, at(days=1, hours=-9), UTC), at(days=1, hours=-10))

    def test_a_week_asleep_is_one_planned_time(self):
        self.assertEqual(schedule.latest(DAY, T0, at(days=7, hours=1), UTC), at(days=7, hours=-10))

    def test_weekly(self):
        every = {'kind': 'week', 'day': 2, 'at': '07:00'}  # Wednesdays
        self.assertIsNone(schedule.latest(every, T0, at(days=1), UTC))
        self.assertEqual(schedule.latest(every, T0, at(days=3), UTC), at(days=2, hours=-5))
        self.assertEqual(schedule.next_time(every, T0, at(days=3), UTC), at(days=9, hours=-5))

    def test_every_few_hours_counts_from_when_it_was_made(self):
        every = {'kind': 'hours', 'n': 6}
        self.assertIsNone(schedule.latest(every, T0, at(hours=5), UTC))
        self.assertEqual(schedule.latest(every, T0, at(hours=13), UTC), at(hours=12))
        self.assertEqual(schedule.next_time(every, T0, at(hours=1), UTC), at(hours=6))

    def test_words(self):
        self.assertEqual(schedule.when_words(DAY), 'Every day at 02:00')
        self.assertEqual(schedule.when_words({'kind': 'week', 'day': 0, 'at': '07:00'}), 'Mondays at 07:00')
        self.assertEqual(schedule.when_words({'kind': 'hours', 'n': 1}), 'Every hour')
        self.assertEqual(schedule.when_words({'kind': 'minutes', 'n': 5}), 'Every 5 minutes')


class Rt:
    def __init__(self):
        self.live, self.killed = set(), []

    def list(self, labels, all=False):
        return [{'address': f't-{n}', 'state': 'live', 'labels': {team.TEAM: n, team.ROLE: f'{n}-builder'}}
                for n in sorted(self.live)]

    def kill(self, t):
        self.killed.append(t)
        self.live.discard(t.removeprefix('t-'))


class SchedulerCase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        p = mock.patch.dict(os.environ, {'XDG_STATE_HOME': os.path.join(self.d, 'state')})
        p.start()
        self.addCleanup(p.stop)
        self.repo = git_repo(os.path.join(self.d, 'shop'))
        self.teams = os.path.join(self.d, 'teams')
        self.rt, self.started, self.said, self.clock = Rt(), [], [], [T0]
        self.fail = None
        self.s = schedule.Scheduler(self.rt, self.start, teams=self.teams, now=lambda: self.clock[0],
                                    announce=lambda title, body: self.said.append(title))

    def start(self, project, task, roles, practices, name):
        if self.fail:
            raise newteam.NewTeamError('project', self.fail)
        self.started.append((project, task, roles, name))
        newteam.save(project, roles, practices, name, self.teams)  # what Starter.start writes
        self.rt.live.add(name)
        return {'team': name}

    def add(self, every=DAY, preset='one-agent', name='Nightly tests'):
        self.s.save(name, self.repo, preset, 'Run the tests and fix what breaks.', every)
        return schedule._load()[-1]

    def tick(self, when):
        self.clock[0] = when
        self.s.tick(tz=UTC)

    def titles(self):
        return [r['title'] for r in activity.list_rows(now=self.clock[0])['rows']]


class RunTest(SchedulerCase):
    def test_runs_once_at_its_time_with_its_own_roles(self):
        s = self.add()
        self.assertEqual(s['team'], 'nightly-tests')
        self.tick(at(hours=1))
        self.assertEqual(self.started, [])
        self.tick(at(hours=14, minutes=1))
        self.tick(at(hours=14, minutes=2))
        [(project, task, roles, name)] = self.started
        self.assertEqual((project, name), (self.repo, 'nightly-tests'))
        self.assertEqual([r['name'] for r in roles], ['nightly-tests-builder'])
        self.assertTrue(task.startswith('Run the tests and fix what breaks.'))
        self.assertIn('call report with state "done"', task)
        self.assertIn('Started Nightly tests', self.titles())
        self.assertEqual(self.s.rows(tz=UTC)[0]['state'], 'running')

    def test_parents_are_renamed_too(self):
        self.add(preset='builder-reviewer')
        self.tick(at(days=1))
        roles = {r['name']: r['parent'] for r in self.started[0][2]}
        self.assertEqual(roles, {'nightly-tests-builder': None, 'nightly-tests-reviewer': 'nightly-tests-builder'})

    def test_still_going_is_skipped_and_said(self):
        self.add()
        self.tick(at(days=1))
        self.tick(at(days=2))
        self.assertEqual(len(self.started), 1)
        self.assertIn('Skipped Nightly tests at 02:00', self.titles())

    def test_asleep_for_days_runs_once(self):
        self.add()
        self.tick(at(days=5))
        self.rt.live.clear()
        self.tick(at(days=5, minutes=1))
        self.assertEqual(len(self.started), 1)

    def test_top_agent_done_stops_the_team(self):
        self.add(preset='builder-reviewer')
        self.tick(at(days=1))
        self.assertFalse(self.s.done('nightly-tests', 'nightly-tests-reviewer'))  # a worker's done ends nothing
        self.assertTrue(self.s.done('nightly-tests', 'nightly-tests-builder'))
        self.tick(at(days=1, minutes=1))
        self.assertEqual(self.rt.killed, ['t-nightly-tests'])
        self.assertIn('Nightly tests finished', self.titles())
        row = self.s.rows(tz=UTC)[0]
        self.assertEqual(row['state'], 'waiting')
        self.assertIn('finished; its work waits in Review', row['line'])
        self.tick(at(days=2))  # the next night runs again
        self.assertEqual(len(self.started), 2)

    def test_done_from_another_team_ends_nothing(self):
        self.add()
        self.tick(at(days=1))
        self.assertFalse(self.s.done('shop', 'builder'))

    def test_failed_start_is_said_and_alerts(self):
        self.add()
        self.fail = 'This project already has a running team: shop. Stop it first.'
        self.tick(at(days=1))
        self.assertEqual(self.said, ["Couldn't start Nightly tests"])
        row = self.s.rows(tz=UTC)[0]
        self.assertTrue(row['failed'])
        self.assertIn("couldn't start: This project already has a running team", row['line'])

    def test_paused_does_not_run_and_resuming_skips_the_past(self):
        s = self.add()
        self.s.pause(s['id'], True)
        self.tick(at(days=1))
        self.clock[0] = at(days=1, hours=1)
        self.s.pause(s['id'], False)
        self.tick(at(days=1, hours=2))
        self.assertEqual(self.started, [])
        self.tick(at(days=2))
        self.assertEqual(len(self.started), 1)

    def test_agents_ending_on_their_own_end_the_run(self):
        self.add()
        self.tick(at(days=1))
        self.rt.live.clear()
        self.tick(at(days=1, minutes=1))
        self.assertIn('ended without saying it was done', self.s.rows(tz=UTC)[0]['line'])

    def test_removing_a_running_schedule_lets_the_run_end(self):
        s = self.add()
        self.tick(at(days=1))
        self.s.remove(s['id'])
        self.assertEqual(self.s.rows(), [])
        self.s.done('nightly-tests', 'nightly-tests-builder')
        self.tick(at(days=1, minutes=1))
        self.assertEqual(self.rt.killed, ['t-nightly-tests'])
        self.tick(at(days=1, minutes=2))
        self.assertEqual(schedule._load(), [])


class SaveTest(SchedulerCase):
    def test_checks(self):
        for args, field in [(('', self.repo, 'one-agent', 't', DAY), 'name'),
                            (('n', self.repo, 'one-agent', ' ', DAY), 'task'),
                            (('n', self.repo, 'one-agent', 't', {'kind': 'day', 'at': '25:00'}), 'every'),
                            (('n', self.repo, 'one-agent', 't', {'kind': 'minutes', 'n': 1}), 'every'),
                            (('n', self.repo, 'one-agent', 't', {'kind': 'week', 'at': '07:00'}), 'every'),
                            (('n', os.path.join(self.d, 'nope'), 'one-agent', 't', DAY), 'project'),
                            (('n', self.repo, 'saved:gone', 't', DAY), 'preset'),
                            (('n', self.repo, '../x', 't', DAY), 'preset')]:
            with self.assertRaises(schedule.ScheduleError) as e:
                self.s.save(*args)
            self.assertEqual(e.exception.field, field, args)

    def test_editing_keeps_the_team_and_a_new_time_starts_counting_now(self):
        s = self.add()
        self.clock[0] = at(days=3)
        self.s.save('Tests at night', self.repo, 'one-agent', 'Run the tests.', {'kind': 'day', 'at': '01:00'}, id=s['id'])
        self.tick(at(days=3, minutes=1))
        self.assertEqual(self.started, [])
        [row] = self.s.rows(tz=UTC)
        self.assertEqual((row['name'], row['team'], row['when']), ('Tests at night', 'nightly-tests', 'Every day at 01:00'))

    def test_team_names_never_clash(self):
        os.makedirs(os.path.join(self.teams, 'nightly-tests'))
        self.assertEqual(self.add()['team'], 'nightly-tests-2')
        self.assertEqual(self.add()['team'], 'nightly-tests-3')

    def test_a_saved_team_keeps_its_models(self):
        newteam.save(self.repo, [role('builder', model='haiku')], {}, 'cheap', self.teams)
        self.assertIn({'id': 'saved:cheap', 'label': 'cheap'}, schedule.options(self.teams))
        self.add(preset='saved:cheap')
        self.tick(at(days=1))
        self.assertEqual(self.started[0][2][0]['model'], 'haiku')
        self.assertNotIn('saved:nightly-tests', [o['id'] for o in schedule.options(self.teams)])  # its own folder


class ReportTest(SchedulerCase):
    def test_report_done_ends_the_run(self):
        self.add()
        self.tick(at(days=1))
        brain = mock.Mock()
        brain.rt.get.return_value = {'labels': {team.TEAM: 'nightly-tests', team.ROLE: 'nightly-tests-builder'}}
        tools = mcptools.Tools(brain, None, teams=self.teams, scheduler=self.s)
        self.assertEqual(tools.report('t-x', 'done')['detail'], schedule.ENDS_NOW)


if __name__ == '__main__':
    unittest.main()
