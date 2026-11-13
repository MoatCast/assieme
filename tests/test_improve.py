"""Improve this team: a team started outside Assieme compared with the recommended setup, Apply and its Undo."""
import datetime as dt
import os
import tempfile
import unittest
from unittest import mock

from assieme import activity, improve, quiet, settings, team
from tests.test_teammap import NOW, Today, log_agent, session

ROOT = '/w/shop'


def found_row(role, cwd=None, address=None):
    return {'address': address or f'h-{role}', 'state': 'live', 'status': {'state': 'idle'}, 'cwd': cwd or f'{ROOT}/{role}',
            'created_ms': 0, 'argv': ['claude'], 'labels': {team.TEAM: 'shop', team.ROLE: role}}


def terminal(role, *argv, cwd=None):
    return {'address': f'h-{role}', 'name': role, 'argv': list(argv), 'cwd': cwd or f'{ROOT}/{role}', 'agent': argv[0]}


class Brain:
    def __init__(self, rows, agents=(), sessions=(), found=True):
        self.rows, self.agents, self.found, self.today = rows, list(agents), found, Today(sessions)

    def _resolve(self, name, now):
        return self.rows, self.agents, self.found, None


class ImproveCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {'XDG_STATE_HOME': self.tmp.name})
        self.env.start()
        self.log = os.path.join(self.tmp.name, 'actions.jsonl')

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def check(self, brain, terminals=None):
        return improve.check(brain, 'shop', NOW, self.log, terminals or [])

    def ids(self, got):
        return {f['id']: f for f in got['findings']}


class FindingsTest(ImproveCase):
    def test_a_team_started_in_assieme_is_left_alone(self):
        self.assertEqual(self.check(Brain([found_row('a')], found=False))['findings'], [])

    def test_restart_findings_are_review_only(self):
        rows = [found_row('builder-1', cwd=ROOT), found_row('builder-2', cwd=ROOT), found_row('reviewer')]
        terms = [terminal('builder-1', 'claude', '--dangerously-skip-permissions', cwd=ROOT),
                 terminal('builder-2', 'claude', cwd=ROOT), terminal('reviewer', 'codex')]
        got = self.ids(self.check(Brain(rows), terms))
        self.assertIn('builder-1 and builder-2 work in the same folder', got['folder:builder-1']['detail'])
        self.assertIn('permission checks skipped', got['permissions:builder-1']['detail'])
        self.assertIn('reviewer:reviewer', got)
        for k in ('folder:builder-1', 'permissions:builder-1', 'reviewer:reviewer'):
            self.assertEqual((got[k]['kind'], got[k]['restart']), ('review', True))

    def test_what_passed_is_in_place_and_nothing_is_claimed_without_data(self):
        rows = [found_row('planner'), found_row('builder')]
        terms = [terminal('planner', 'claude', '--permission-mode', 'plan'), terminal('builder', 'claude')]
        got = self.check(Brain(rows), terms)
        self.assertEqual(got['in_place'], ['each agent has its own folder', 'safe permissions', 'read-only reviewers'])
        self.assertEqual(self.check(Brain(rows))['in_place'], ['each agent has its own folder'])

    def test_wake_ups_for_nothing_give_the_sender_the_message_rules(self):
        rows = [found_row('planner'), found_row('builder')]
        sessions = [session('planner', 1000, cwd=f'{ROOT}/planner'),
                    session('builder', 0, [('planner', 0, 500_000)] * 6, cwd=f'{ROOT}/builder')]
        f = self.ids(self.check(Brain(rows, sessions=sessions)))['messages:planner']
        self.assertEqual((f['kind'], f['to'], f['groups']), ('apply', ['planner'], ['messages']))
        self.assertIn('planner woke builder 6 times today', f['detail'])

    def test_a_retry_loop_gives_that_agent_the_stop_rules(self):
        fail = [(NOW - dt.timedelta(minutes=i), 'sig', i, 'pytest') for i in range(4)]
        a = log_agent('builder', 'working', failures=fail)
        a.cwd = f'{ROOT}/builder'
        f = self.ids(self.check(Brain([found_row('builder')], [a])))['stopping:builder']
        self.assertEqual((f['kind'], f['to'], f['title']), ('apply', ['builder'], 'No stop rules'))

    def test_the_practices_a_found_team_never_got(self):
        f = self.ids(self.check(Brain([found_row('a'), found_row('b')])))['practices']
        self.assertEqual((f['kind'], f['to'], len(f['groups'])), ('apply', ['a', 'b'], 5))
        settings.set_value('practices.machine', False)
        self.assertEqual(len(self.ids(self.check(Brain([found_row('a')])))['practices']['groups']), 4)

    def test_a_restart_fix_opens_the_running_herdr_team_in_new_team(self):
        rows = [found_row('x', cwd=ROOT), found_row('y', cwd=ROOT)]
        running = {'shop': [terminal('x', 'claude', cwd=ROOT), terminal('y', 'claude', cwd=ROOT)], 'other': [terminal('z', 'codex')]}
        with mock.patch.object(improve.herdr, '_running', lambda: (running, None)):
            got = improve.check(Brain(rows), 'shop', NOW, self.log)
        self.assertEqual(self.ids(got)['folder:x']['draft'], 'running:shop')
        self.assertIsNone(self.ids(got)['practices']['draft'])

    def test_an_agent_outside_herdr_gets_the_rules_to_paste(self):
        f = self.ids(self.check(Brain([found_row('a', address='log:a')])))['practices']
        self.assertEqual(f['kind'], 'review')
        self.assertIn('Assieme can’t send it messages, because it runs in a terminal Assieme doesn’t control', f['detail'])
        self.assertTrue(f['copy'].startswith('assieme: your owner added these team practices.'))


class ApplyTest(ImproveCase):
    def test_apply_holds_one_note_per_agent_and_logs_one_undoable_row(self):
        brain = Brain([found_row('a'), found_row('b', address='log:b')])
        got = improve.apply(brain, 'shop', ['practices'], NOW, self.log, [])
        self.assertEqual(len(got['applied']), 1)
        held = quiet.held()
        self.assertEqual(list(held), [os.path.realpath(f'{ROOT}/a')])
        [n] = held[os.path.realpath(f'{ROOT}/a')]
        self.assertEqual((n['to'], n['session']), ('h-a', None))  # the person's own herdr core
        self.assertIn('Run one action per shell command', n['text'])
        [row] = activity.list_rows('shop', path=self.log, now=NOW)['rows']
        self.assertEqual((row['title'], row['undo']['label']), ('Gave a the team practices', 'Undo'))
        left = self.ids(self.check(brain))['practices']  # b can't be reached: its rules are to paste
        self.assertEqual((left['kind'], left['to']), ('review', ['b']))
        self.assertIn('team practices', self.check(Brain([found_row('a')]))['in_place'])

    def test_undo_takes_back_a_note_still_held(self):
        brain = Brain([found_row('a')])
        [d] = improve.apply(brain, 'shop', ['practices'], NOW, self.log, [])['applied']
        self.assertEqual(activity.undo(d['entry'], None, path=self.log, now=NOW), {'ok': True, 'detail': ''})
        self.assertEqual(quiet.held(), {})
        self.assertIn('practices', self.ids(self.check(brain)))

    def test_undo_after_delivery_sends_a_note_that_it_no_longer_applies(self):
        brain = Brain([found_row('a')])
        [d] = improve.apply(brain, 'shop', ['practices'], NOW, self.log, [])['applied']
        quiet._drop([n for ns in quiet.held().values() for n in ns])  # delivered
        activity.undo(d['entry'], None, path=self.log, now=NOW)
        [n] = quiet.held()[os.path.realpath(f'{ROOT}/a')]
        self.assertIn('no longer need to follow them: “Fewer, cheaper messages”', n['text'])

    def test_agents_sharing_a_folder_have_one_log_but_each_gets_its_note(self):
        rows = [found_row('proj', cwd=ROOT, address='h-b1')]  # Claude Code keeps one log per folder
        terms = [terminal('b1', 'claude', cwd=ROOT), terminal('b2', 'claude', cwd=ROOT)]
        got = self.ids(self.check(Brain(rows), terms))
        self.assertIn('b1 and b2 work in the same folder', got['folder:b1']['detail'])
        self.assertEqual(got['practices']['to'], ['b1', 'b2'])
        improve.apply(Brain(rows), 'shop', ['practices'], NOW, self.log, terms)
        self.assertEqual(sorted(n['to'] for n in quiet.held()[os.path.realpath(ROOT)]), ['h-b1', 'h-b2'])

    def test_a_refused_command_is_review_only_and_never_suggests_widening(self):
        rows = [found_row('b')]
        rep = {'agents': {}, 'findings': [], 'edges': [], 'send_less': [],
               'refusals': [{'agent': 'b', 'command': 'npm run deploy', 'why': 'a deny rule', 'advice': 'allow it explicitly', 'count': 9}]}
        with mock.patch.object(improve.teammap, '_team_report', lambda *a: rep):
            f = self.ids(self.check(Brain(rows)))['refused:b:npm run deploy']
        self.assertEqual(f['kind'], 'review')
        self.assertIn('Check whether it should be allowed, or change the brief so it stops trying.', f['detail'])
        self.assertNotIn('allow it explicitly', f['detail'])

    def test_apply_reads_the_team_and_herdr_once(self):
        calls = {'resolve': 0, 'running': 0}

        class Counting(Brain):
            def _resolve(self, name, now):
                calls['resolve'] += 1
                return super()._resolve(name, now)

        def running():
            calls['running'] += 1
            return {'shop': [terminal('a', 'claude')]}, None
        with mock.patch.object(improve.herdr, '_running', running):
            improve.apply(Counting([found_row('a')]), 'shop', ['practices'], NOW, self.log)
        self.assertEqual(calls, {'resolve': 1, 'running': 1})

    def test_review_findings_are_never_applied(self):
        rows = [found_row('x', cwd=ROOT), found_row('y', cwd=ROOT)]
        got = improve.apply(Brain(rows), 'shop', ['folder:x'], NOW, self.log, [])
        self.assertEqual(got['applied'], [])
        self.assertEqual(quiet.held(), {})


if __name__ == '__main__':
    unittest.main()
