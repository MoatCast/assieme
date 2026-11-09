"""Findings from the final review of step 10, each pinned."""
import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

from assieme import activity, adapters, live, newteam, panes, quiet, settings, status, team, teammap
from assieme.status import Agent
from tests.test_teammap import FakeRt, MapCase, NoAnswers, Today, log_agent, row

NOW = dt.datetime(2026, 11, 8, 12, 0, tzinfo=dt.timezone.utc)


class OneBadRowTest(MapCase):
    def test_a_quiet_row_from_the_command_line_can_be_stopped(self):
        s = live.Suggestion('x', 'a', action='quiet', pair=('/w/a', '/w/b', 'a -> b'))
        live.run_action(s)
        [r] = activity.list_rows(now=status.now_utc())['rows']
        self.assertEqual(r['undo'], {'label': 'Stop'})
        self.assertTrue(activity.undo(r['id'], FakeRt())['ok'])
        self.assertFalse(quiet.is_quiet('/w/a', '/w/b'))

    def test_one_odd_row_never_breaks_the_list(self):
        for extra in ({}, {'pair': 'nonsense'}, {'pair': [1]}):
            live.record('quiet', 'a', 'old row', now=NOW, **extra)
        self.assertEqual(len(activity.list_rows(now=NOW)['rows']), 3)
        self.assertEqual({str(r['undo']) for r in activity.list_rows(now=NOW)['rows']}, {'None'})


class CorruptLogTest(unittest.TestCase):
    def collect(self, build):
        d = tempfile.mkdtemp()
        claude, codex = os.path.join(d, 'c'), os.path.join(d, 'x')
        os.makedirs(os.path.join(claude, '-good'))
        os.makedirs(os.path.join(claude, '-bad'))
        os.makedirs(codex)
        with open(os.path.join(claude, '-good', 's.jsonl'), 'w') as f:
            f.write(json.dumps({'type': 'user', 'timestamp': '2026-11-08T11:59:00Z', 'cwd': '/w/good', 'message': {'content': 'hi'}}) + '\n')
        build(claude, codex)
        return status.collect(claude, codex, NOW)

    def test_a_message_that_is_a_string_only_loses_that_file(self):
        def bad(claude, codex):
            with open(os.path.join(claude, '-bad', 's.jsonl'), 'w') as f:
                f.write(json.dumps({'type': 'user', 'timestamp': '2026-11-08T11:59:00Z', 'message': 'just text'}) + '\n')
        self.assertEqual([a.cwd for a in self.collect(bad)], ['/w/good'])

    def test_a_codex_first_line_that_is_not_an_object_only_loses_that_file(self):
        def bad(claude, codex):
            with open(os.path.join(codex, 'r.jsonl'), 'w') as f:
                f.write('"not an object"\n')
        self.assertEqual([a.cwd for a in self.collect(bad)], ['/w/good'])

    def test_each_tick_step_runs_even_if_the_first_fails(self):
        from assieme import cli
        with mock.patch.object(cli.serve, 'serve') as srv, mock.patch.object(cli.serve, 'brain_socket_path', return_value='/s/b.sock'), \
                mock.patch.object(cli.live, 'watch_once', side_effect=ValueError('bad log')), \
                mock.patch.object(cli.teammap.Brain, 'tick') as second, mock.patch.object(cli.machine, 'tick'), mock.patch('traceback.print_exc'):
            cli.main(['serve'])
            srv.call_args.kwargs['tick']()
        second.assert_called_once()


class CompactionTest(unittest.TestCase):
    def test_a_compaction_resets_memory(self):
        a = Agent('a', 'claude-code', '/x')
        status._claude(a, [
            {'type': 'assistant', 'timestamp': '2026-11-08T11:00:00Z', 'message': {'usage': {'input_tokens': 170_000}, 'content': []}},
            {'type': 'system', 'subtype': 'compact_boundary', 'timestamp': '2026-11-08T11:01:00Z'},
        ], False)
        self.assertEqual(a.context, 0)


class SameNameTest(MapCase):
    def brain(self, rows):
        d = tempfile.mkdtemp()
        return teammap.Brain(os.path.join(d, 'c'), os.path.join(d, 'x'), FakeRt(rows), None, NoAnswers())

    def agent(self):
        a = Agent('mine', 'claude-code', '/l.jsonl')
        a.cwd, a.state, a.last, a.herdr_address = '/w/shop', 'working', NOW, 't7'
        return a

    def run_with(self, b, fn):
        with mock.patch.object(status, 'collect', return_value=[self.agent()]), \
                mock.patch.object(status, 'herdr_terminals', return_value=[]), mock.patch.object(status, 'running_cwds', return_value={'/w/shop'}):
            return fn(b)

    def test_a_stopped_team_doesnt_hide_a_found_team_of_that_name(self):
        m = self.run_with(self.brain([row('a', state='finished')]), lambda b: b.map('shop'))
        self.assertTrue(m['found'])
        self.assertEqual([n['role'] for n in m['nodes']], ['mine'])
        self.assertFalse(m['stopped'])

    def test_settings_lists_that_found_team(self):
        got = self.run_with(self.brain([row('a', state='finished')]), lambda b: b.teams())
        self.assertEqual([(t['name'], t['kind']) for t in got['teams']], [('shop', 'found')])

    def test_a_running_team_wins_the_name(self):
        m = self.run_with(self.brain([row('a')]), lambda b: b.map('shop'))
        self.assertFalse(m['found'])

    def test_stopped_team_with_no_found_namesake_says_stopped(self):
        with mock.patch.object(status, 'collect', return_value=[]), mock.patch.object(status, 'herdr_terminals', return_value=[]), \
                mock.patch.object(status, 'running_cwds', return_value=set()):
            m = self.brain([row('a', state='finished')]).map('shop')
        self.assertTrue(m['stopped'])
        self.assertFalse(m['found'])

    def test_a_press_on_the_found_team_goes_through_its_own_hc(self):
        b = self.brain([row('a', state='finished')])
        b.herdr = mock.Mock()
        with mock.patch.object(panes, 'act', return_value={'ok': True}) as act:
            self.run_with(b, lambda x: x.act('shop', 'mine', 'f', 'b'))
        self.assertIs(act.call_args.args[5], b.herdr)


class StopAddedAgentTest(MapCase):
    def rows(self):
        return activity.list_rows(now=NOW)['rows']

    def test_stop_asks_first(self):
        live.record('add_agent', 'b', 'added', team='shop', address='t9', now=NOW)
        self.assertIn('ends', self.rows()[0]['undo']['confirm'])
        live.record('quiet', 'a to b', 'r', now=NOW, pair=['/w/a', '/w/b', 'a to b'])
        quiet.set_pair('/w/a', '/w/b', 'a to b')
        self.assertNotIn('confirm', self.rows()[0]['undo'])

    def test_a_finished_agent_isnt_tidied_away(self):
        e = live.record('add_agent', 'b', 'added', team='shop', address='t9', now=NOW)

        class Rt(FakeRt):
            def get(self, t):
                return {'state': 'finished', 'labels': {team.TEAM: 'shop', team.ROLE: 'b'}}
        rt = Rt()
        with mock.patch.object(newteam, 'forget_role') as forget:
            r = activity.undo(e['id'], rt, now=NOW)
        self.assertFalse(r['ok'])
        self.assertEqual(rt.killed, [])
        forget.assert_not_called()


class ForgetRoleTest(unittest.TestCase):
    def test_children_move_to_the_removed_roles_parent(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, 'proj')
        os.makedirs(p)
        roles = [{'name': 'lead', 'agent': 'claude', 'brief': 'b'}, {'name': 'mid', 'agent': 'claude', 'brief': 'b', 'parent': 'lead'},
                 {'name': 'leaf', 'agent': 'claude', 'brief': 'b', 'parent': 'mid'}]
        folder = newteam.save(p, roles, {}, 'shop', d)
        newteam.forget_role(folder, 'mid')
        got = newteam.roster('shop', d)
        self.assertEqual(sorted(got), ['lead', 'leaf'])
        self.assertEqual(got['leaf']['parent'], 'lead')


class FailedAutomaticTest(MapCase):
    def test_a_failed_fix_waits_like_a_successful_one(self):
        settings.set_value('fixes.nudge', 'auto')

        class Rt:
            calls = 0

            def send(self, t, text):
                Rt.calls += 1
                raise OSError('gone')
        m = self.build([row('a')], [log_agent('a', 'stuck', detail='silent 18 min')])
        for _ in range(3):
            teammap.run_automatic({'shop': m}, settings.load(), Rt(), now=NOW)
        self.assertEqual(Rt.calls, 1)


class SecretsTest(unittest.TestCase):
    def test_program_skips_variables(self):
        self.assertEqual(adapters.program('API_KEY=abc123 FOO=1 npm test'), 'npm')
        self.assertEqual(adapters.program('/usr/bin/env'), 'env')
        self.assertEqual(adapters.program('API_KEY=abc123'), '')
        self.assertEqual(adapters.program(''), '')

    def test_label_recent_and_card_hide_a_variable(self):
        self.assertEqual(adapters.command_label('Bash', 'API_KEY=abc123 npm test', False), 'Bash: npm')
        self.assertEqual(status._short('API_KEY=abc123 npm test', False), 'npm')
        a = log_agent('a', 'stuck', detail='silent', cmds=['API_KEY=abc123 npm test'])
        v = panes.view('shop', [row('a')], [a], NOW, lambda t: '', NoAnswers())
        self.assertEqual(v['panes'][0]['command'], 'npm')
