"""The Team tab's data: who wakes whom, cards, tips, spend, groups, agent detail, automatic fixes."""
import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

from assieme import activity, live, panes, quiet, settings, status, team, teammap
from assieme.model import HUMAN, Session
from assieme.status import Agent

NOW = dt.datetime(2026, 11, 8, 12, 0, tzinfo=dt.timezone.utc)
MS = int(NOW.timestamp() * 1000)
WT = '/p/.assieme/worktrees'


def row(role, status_='idle', state='live', parent=None, agent='claude', cwd=None):
    return {'address': f't-{role}', 'state': state, 'status': {'state': status_}, 'status_ms': MS - 30000,
            'cwd': cwd or f'{WT}/{role}', 'argv': [agent], 'created_ms': 0, 'parent': parent and f't-{parent}',
            'labels': {team.TEAM: 'shop', team.ROLE: role}}


def log_agent(role, state='idle', **kw):
    a = Agent(role, 'claude-code', f'/logs/{role}.jsonl')
    a.cwd, a.state, a.last = f'{WT}/{role}', state, NOW - dt.timedelta(seconds=10)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def session(role, tokens=0, wakes=(), cwd=None):
    """wakes: (sender, calls, tokens) turns, all this morning."""
    s = Session(role, 'claude-code', cwd=cwd or f'{WT}/{role}')
    s.calls, s.tokens = 1, tokens
    for sender, calls, tok in wakes:
        s.wake(sender, 'hc', ts=NOW - dt.timedelta(minutes=5))
        s.turns[-1].calls, s.turns[-1].tokens = calls, tok
        s.tokens += tok
    return s


class Today:
    def __init__(self, sessions=()):
        self.got = list(sessions)

    def sessions(self):
        return self.got


class NoAnswers:
    def get(self, key, question):
        return None


class MapCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {'XDG_STATE_HOME': self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def build(self, rows, agents=(), sessions=(), **kw):
        return teammap.build('shop', rows, list(agents), NOW, lambda a: '⏺ ok\n', NoAnswers(), Today(sessions),
                             settings.load(), **kw)


class TitleTest(MapCase):
    def test_all_quiet(self):
        m = self.build([row('a', 'working'), row('b', 'idle')])
        self.assertEqual(m['title'], 'All quiet')
        self.assertIn('notification', m['subtitle'])

    def test_title_counts_panes(self):
        a, b = log_agent('a', 'stuck', detail='silent 18 min'), log_agent('b', 'stuck', detail='silent 20 min')
        m = self.build([row('a'), row('b'), row('c', 'working')], [a, b])
        self.assertEqual(m['title'], 'Two agents need you')
        self.assertEqual(m['subtitle'], 'The other one is working or done.')

    def test_one_agent_needs_you(self):
        m = self.build([row('a', 'blocked')], [log_agent('a', 'waiting on you')])
        self.assertEqual(m['title'], 'One agent needs you')

    def test_stopped_team(self):
        m = self.build([row('a', state='finished')])
        self.assertTrue(m['stopped'])

    def test_no_rows_at_all(self):
        m = self.build([])
        self.assertTrue(m['stopped'])
        self.assertEqual(m['nodes'], [])


class EdgeTest(MapCase):
    def test_edges_come_from_team_sessions_only(self):
        s = [session('b', wakes=[('t-a', 3, 100), ('t-a', 3, 100)]), session('z', wakes=[('t-a', 3, 900)], cwd='/elsewhere')]
        m = self.build([row('a'), row('b')], sessions=s)
        self.assertEqual([(e['from'], e['to'], e['wakes']) for e in m['edges']], [('a', 'b', 2)])

    def test_tag_may_name_the_role(self):
        m = self.build([row('a'), row('b')], sessions=[session('b', wakes=[('a', 3, 100)])])
        self.assertEqual(m['edges'][0]['from'], 'a')

    def test_people_are_not_edges(self):
        m = self.build([row('a')], sessions=[session('a', wakes=[(HUMAN, 3, 100)])])
        self.assertEqual(m['edges'], [])

    def test_noisy_needs_10_in_last_hour_and_most_empty(self):
        empty = [('t-a', 1, 10)] * 12
        m = self.build([row('a'), row('b')], sessions=[session('b', wakes=empty)])
        self.assertEqual(m['edges'][0]['noisy'], {'per_hour': 12})
        busy = [('t-a', 9, 10)] * 12
        m = self.build([row('a'), row('b')], sessions=[session('b', wakes=busy)])
        self.assertIsNone(m['edges'][0]['noisy'])
        few = [('t-a', 1, 10)] * 3
        m = self.build([row('a'), row('b')], sessions=[session('b', wakes=few)])
        self.assertIsNone(m['edges'][0]['noisy'])

    def test_waits(self):
        a = log_agent('a', 'idle', waits_on=['b'])
        m = self.build([row('a'), row('b')], [a, log_agent('b', 'working')])
        self.assertEqual(m['waits'], [{'from': 'a', 'to': 'b'}])

    def test_waiting_on_you_is_not_an_edge(self):
        a = log_agent('a', 'waiting on you', waits_on=['you'])
        self.assertEqual(self.build([row('a')], [a])['waits'], [])

    def test_shared_folder_not_counted_twice(self):
        rows = [row('a', cwd='/w/shared'), row('b', cwd='/w/shared')]
        m = self.build(rows, sessions=[session('x', tokens=1000, cwd='/w/shared')])
        self.assertEqual(m['spend']['tokens'], 1000)

    def test_parents_come_from_the_terminals(self):
        m = self.build([row('a'), row('b', parent='a')])
        self.assertEqual({n['role']: n['parent'] for n in m['nodes']}, {'a': None, 'b': 'a'})

    def test_advice_matches_live_suggest(self):
        a = log_agent('a', 'stuck', detail='silent 18 min')
        b = log_agent('b', 'idle', context=160_000)
        c = log_agent('c', 'idle')
        b.peers, c.peers = {c}, {b}
        now_ts = NOW - dt.timedelta(minutes=2)
        c.refused = [(now_ts, 'Bash: git', 'deny rule', ''), (now_ts, 'Bash: git', 'deny rule', '')]
        agents = [a, b, c]
        addr = {x.name: f't-{x.name}' for x in agents}
        want = {(s.agent, s.action) for s in live.suggest(agents, status.stalls(agents, NOW), addr, now=NOW)
                if s.action in panes.ACTIONS}
        m = self.build([row('a'), row('b'), row('c')], agents)
        got = {(p['role'], x['action']) for p in m['panes'] if p['fix'] for x in p['fix']['buttons'] if x.get('action')}
        self.assertEqual(got, want)


class SpendTipTest(MapCase):
    def test_spend_is_tokens_and_waste(self):
        s = [session('a', tokens=700), session('b', wakes=[('t-a', 1, 300)])]
        m = self.build([row('a'), row('b')], sessions=s)
        self.assertEqual(m['spend'], {'tokens': 1000, 'waste_share': 0.3})

    def test_no_logs_gives_no_spend(self):
        self.assertIsNone(self.build([row('a')])['spend'])

    def test_tips_are_send_less_share(self):
        s = [session('a', tokens=700), session('b', wakes=[('t-a', 1, 300)])]
        [t] = self.build([row('a'), row('b')], sessions=s)['tips']
        self.assertEqual((t['from'], t['to'], t['share']), ('a', 'b', 0.3))

    def test_no_tip_once_quiet(self):
        quiet.set_pair(f'{WT}/a', f'{WT}/b', 'a to b')
        s = [session('a', tokens=700), session('b', wakes=[('t-a', 1, 300)])]
        self.assertEqual(self.build([row('a'), row('b')], sessions=s)['tips'], [])

    def test_found_team_has_no_tips(self):
        s = [session('a', tokens=700), session('b', wakes=[('t-a', 1, 300)])]
        self.assertEqual(self.build([row('a'), row('b')], sessions=s, found=True)['tips'], [])

    def test_apply_tip_sets_pair_and_logs(self):
        s = [session('a', tokens=700), session('b', wakes=[('t-a', 1, 300)])]
        m = self.build([row('a'), row('b')], sessions=s)
        r = teammap.apply_tip('shop', m['tips'][0]['id'], m, now=NOW)
        self.assertTrue(r['ok'])
        self.assertTrue(quiet.is_quiet(f'{WT}/a', f'{WT}/b'))
        [e] = live.recent_actions()
        self.assertEqual((e['action'], e['team']), ('quiet', 'shop'))

    def test_a_stale_tip_is_refused(self):
        m = self.build([row('a')])
        self.assertFalse(teammap.apply_tip('shop', 'nope', m)['ok'])

    def test_done_rows_leave_out_answers_and_deliveries(self):
        live.record('answer', 'a', panes.ANSWERED, team='shop', now=NOW)
        live.record('deliver', 'a', 'busy', by='auto', now=NOW)
        live.record('compact', 'b', 'big', team='shop', now=NOW)
        self.assertEqual([r['title'] for r in self.build([row('a')])['done']], ['Freed up memory for b'])

    def test_build_does_not_read_logs(self):
        class Boom:
            def sessions(self):
                return []

            def refresh(self, now):
                raise AssertionError('polling must not read whole logs')
        teammap.build('shop', [row('a')], [], NOW, lambda a: '', NoAnswers(), Boom(), settings.load())


class FreshTodayTest(MapCase):
    def test_failed_refresh_keeps_last_copy(self):
        t = teammap.Today('/c', '/x', every_s=0)
        with mock.patch.object(teammap, '_read_sessions', return_value=['good']):
            t.refresh(NOW)
        with mock.patch.object(teammap, '_read_sessions', side_effect=OSError('corrupt')):
            t.refresh(NOW)
        self.assertEqual(t.sessions(), ['good'])

    def test_refresh_waits_its_interval(self):
        t = teammap.Today('/c', '/x', every_s=120)
        with mock.patch.object(teammap, '_read_sessions', return_value=[]) as read:
            t.refresh(NOW)
            t.refresh(NOW + dt.timedelta(seconds=60))
            t.refresh(NOW + dt.timedelta(seconds=121))
        self.assertEqual(read.call_count, 2)


class GroupTest(unittest.TestCase):
    def nodes(self, spec):
        return [{'role': r, 'parent': p, 'state': s, 'group': g} for r, p, s, g in spec]

    def test_groups_only_above_8(self):
        n = self.nodes([(f'a{i}', None, 'idle', None) for i in range(8)])
        self.assertIsNone(teammap.groups(n, []))
        n = self.nodes([(f'a{i}', None, 'idle', None) for i in range(9)])
        self.assertIsNotNone(teammap.groups(n, []))

    def test_group_field_wins(self):
        n = self.nodes([(f'a{i}', None, 'idle', 'payments' if i < 5 else 'docs') for i in range(9)])
        self.assertEqual({g['name']: len(g['roles']) for g in teammap.groups(n, [])}, {'payments': 5, 'docs': 4})

    def test_group_by_lead(self):
        spec = [('boss', None, 'idle', None), ('pay', 'boss', 'idle', None), ('docs', 'boss', 'idle', None)]
        spec += [(f'p{i}', 'pay', 'idle', None) for i in range(4)] + [(f'd{i}', 'docs', 'idle', None) for i in range(3)]
        got = {g['name']: set(g['roles']) for g in teammap.groups(self.nodes(spec), [])}
        self.assertEqual(got, {'boss': {'boss'}, 'pay': {'pay', 'p0', 'p1', 'p2', 'p3'}, 'docs': {'docs', 'd0', 'd1', 'd2'}})

    def test_two_roots_are_two_groups(self):
        spec = [('x', None, 'idle', None), ('y', None, 'idle', None)]
        spec += [(f'x{i}', 'x', 'idle', None) for i in range(4)] + [(f'y{i}', 'y', 'idle', None) for i in range(3)]
        self.assertEqual({g['name'] for g in teammap.groups(self.nodes(spec), [])}, {'x', 'y'})

    def test_flat_team_groups_by_first_word(self):
        spec = [(f'builder-{i}', None, 'idle', None) for i in range(6)] + [(f'tester-{i}', None, 'idle', None) for i in range(3)]
        self.assertEqual({g['name']: len(g['roles']) for g in teammap.groups(self.nodes(spec), [])}, {'builder': 6, 'tester': 3})

    def test_group_counts_and_edges_sum(self):
        spec = [(f'a-{i}', None, 'asking' if i == 0 else 'working', None) for i in range(5)]
        spec += [(f'b-{i}', None, 'idle', None) for i in range(4)]
        edges = [{'from': 'a-0', 'to': 'b-0', 'wakes': 2, 'noisy': None}, {'from': 'a-1', 'to': 'b-1', 'wakes': 3, 'noisy': {'per_hour': 11}},
                 {'from': 'a-0', 'to': 'a-1', 'wakes': 9, 'noisy': None}]
        gs = teammap.groups(self.nodes(spec), edges)
        a = next(g for g in gs if g['name'] == 'a')
        self.assertEqual(a['need_you'], 1)
        self.assertEqual(a['edges'], [{'to': 'b', 'wakes': 5, 'noisy': {'per_hour': 11}}])


class FoundTest(MapCase):
    def agent(self, name, cwd, state='idle', address=None, source='claude-code'):
        a = Agent(name, source, f'/logs/{name}.jsonl')
        a.cwd, a.state, a.last, a.herdr_address = cwd, state, NOW, address
        return a

    def test_found_groups_by_folder_and_skips_exited_and_team_folders(self):
        agents = [self.agent('one', '/w/shop'), self.agent('two', '/w/shop'), self.agent('old', '/w/old', 'exited'),
                  self.agent('t', f'{WT}/a')]
        got = teammap.found_groups(agents, {}, taken_cwds={f'{WT}/a'})
        self.assertEqual({k: [a.name for a in v] for k, v in got.items()}, {'shop': ['one', 'two']})

    def test_change_the_groups_moves_a_folder(self):
        agents = [self.agent('one', '/w/one'), self.agent('two', '/w/two')]
        got = teammap.found_groups(agents, {os.path.realpath('/w/two'): 'mine', os.path.realpath('/w/one'): 'mine'}, set())
        self.assertEqual(list(got), ['mine'])
        self.assertEqual(len(got['mine']), 2)

    def test_stopped_team_worktree_counts_as_its_project(self):
        got = teammap.found_groups([self.agent('x', '/w/shop/.assieme/worktrees/builder')], {}, set())
        self.assertEqual(list(got), ['shop'])

    def test_found_rows_mark_what_can_be_reached(self):
        rows = teammap.found_rows('shop', [self.agent('a', '/w/a', address='t3'), self.agent('b', '/w/b', source='codex')])
        self.assertEqual(rows[0]['address'], 't3')
        self.assertTrue(rows[1]['address'].startswith('log:'))
        self.assertEqual(rows[1]['argv'], ['codex'])

    def test_found_agent_without_terminal_gets_no_buttons(self):
        a = self.agent('b', '/w/b', 'stuck')
        a.detail = 'silent 18 min'
        m = self.build(teammap.found_rows('shop', [a]), [a], found=True)
        [p] = m['panes']
        self.assertEqual(p['fix']['buttons'], [])
        self.assertFalse(p['reachable'])
        self.assertTrue(m['found'])


class DetailTest(MapCase):
    def test_detail_for_agent_without_log(self):
        m = self.build([row('a')])
        d = teammap.detail('shop', 'a', m, [], {}, NOW)
        self.assertIsNone(d['memory'])
        self.assertEqual((d['recent'], d['wakers']), ([], []))

    def test_unknown_role_is_none(self):
        self.assertIsNone(teammap.detail('shop', 'zz', self.build([row('a')]), [], {}, NOW))

    def test_amber_above_75(self):
        a = log_agent('a', context=176_000)
        d = teammap.detail('shop', 'a', self.build([row('a')], [a]), [a], {}, NOW)
        self.assertEqual(d['memory'], {'pct': 88, 'amber': True})
        a.context = 100_000
        self.assertEqual(teammap.detail('shop', 'a', self.build([row('a')], [a]), [a], {}, NOW)['memory'], {'pct': 50, 'amber': False})

    def test_wakers_last_hour_only(self):
        a = log_agent('a', wakes=[(NOW - dt.timedelta(minutes=5), 't-b'), (NOW - dt.timedelta(minutes=9), 't-b'),
                                  (NOW - dt.timedelta(minutes=8), 'c'), (NOW - dt.timedelta(hours=3), 't-b')])
        m = self.build([row('a'), row('b'), row('c')], [a])
        d = teammap.detail('shop', 'a', m, [a], {}, NOW)
        self.assertEqual(d['wakers'], [{'role': 'b', 'count': 2}, {'role': 'c', 'count': 1}])

    def test_job_and_cli(self):
        d = teammap.detail('shop', 'a', self.build([row('a', agent='codex')]), [], {'a': {'job': 'fixes tests'}}, NOW)
        self.assertEqual((d['cli'], d['job']), ('codex', 'fixes tests'))


class RecentActionsTest(unittest.TestCase):
    def log(self, *rows):
        d = tempfile.mkdtemp()
        p = os.path.join(d, 's.jsonl')
        with open(p, 'w') as f:
            for r in rows:
                f.write(json.dumps(r) + '\n')
        return p

    def claude(self):
        return self.log(
            {'type': 'user', 'timestamp': '2026-11-08T11:00:00Z', 'message': {'content': '<hc from="t1">go</hc>'}},
            {'type': 'assistant', 'timestamp': '2026-11-08T11:01:00Z', 'message': {'content': [
                {'type': 'tool_use', 'id': 'x', 'name': 'Bash', 'input': {'command': 'pytest tests/secret.py --token=abc'}}]}},
            {'type': 'user', 'timestamp': '2026-11-08T11:02:00Z', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'x', 'is_error': True, 'content': 'nope'}]}},
            {'type': 'assistant', 'timestamp': '2026-11-08T11:03:00Z', 'message': {'content': [
                {'type': 'tool_use', 'id': 'y', 'name': 'Edit', 'input': {'file_path': '/w/src/retry.py'}}]}})

    def test_recent_hides_arguments_by_default(self):
        got = [t for _, t in status.recent_actions(self.claude(), 'claude-code')]
        self.assertEqual(got, ['Edited retry.py', 'Ran pytest, failed', 'Took a note from t1'])

    def test_recent_shows_commands_when_on(self):
        got = [t for _, t in status.recent_actions(self.claude(), 'claude-code', show_text=True)]
        self.assertIn('Ran pytest tests/secret.py --token=abc, failed', got)

    def test_recent_keeps_n(self):
        self.assertEqual(len(status.recent_actions(self.claude(), 'claude-code', n=2)), 2)

    def test_codex(self):
        p = self.log(
            {'type': 'event_msg', 'timestamp': '2026-11-08T11:00:00Z', 'payload': {'type': 'function_call', 'name': 'shell', 'call_id': 'a',
                                                                                  'arguments': json.dumps({'command': ['pytest', '-q']})}})
        self.assertEqual([t for _, t in status.recent_actions(p, 'codex')], ['Ran pytest'])

    def test_missing_file(self):
        self.assertEqual(status.recent_actions('/nope.jsonl', 'codex'), [])


class MemoryLimitTest(unittest.TestCase):
    def test_memory_limit_claude_200k_then_1m(self):
        a = Agent('a', 'claude-code', '/x')
        a.context = 150_000
        self.assertEqual(status.memory_limit(a), 200_000)
        a.context = 250_000
        self.assertEqual(status.memory_limit(a), 1_000_000)

    def test_memory_limit_codex_from_log(self):
        a = Agent('a', 'codex', '/x')
        status._codex(a, [{'timestamp': '2026-11-08T11:00:00Z', 'payload': {'type': 'token_count', 'info': {
            'model_context_window': 272_000, 'last_token_usage': {'input_tokens': 5}}}}], False)
        self.assertEqual(status.memory_limit(a), 272_000)

    def test_unknown_codex_limit_is_none(self):
        self.assertIsNone(status.memory_limit(Agent('a', 'codex', '/x')))


class RunAutomaticTest(MapCase):
    def setUp(self):
        super().setUp()
        self.sent = []
        outer = self

        class Rt:
            def send(self, t, text):
                if t == 't-bad':
                    raise OSError('gone')
                outer.sent.append((t, text))
        self.rt = Rt()

    def stuck(self, role='a'):
        return self.build([row(role)], [log_agent(role, 'stuck', detail='silent 18 min')])

    def auto(self, views, **kw):
        return teammap.run_automatic(views, settings.load(), self.rt, now=NOW, **kw)

    def test_ask_first_does_nothing(self):
        self.assertEqual(self.auto({'shop': self.stuck()}), [])
        self.assertEqual(self.sent, [])

    def test_automatic_sends_once(self):
        settings.set_value('fixes.nudge', 'auto')
        self.assertEqual(len(self.auto({'shop': self.stuck()})), 1)
        self.assertEqual(self.auto({'shop': self.stuck()}), [])
        self.assertEqual(len(self.sent), 1)

    def test_it_can_repeat_after_the_wait(self):
        settings.set_value('fixes.nudge', 'auto')
        self.auto({'shop': self.stuck()})
        later = NOW + dt.timedelta(seconds=live.ACTED_HIDE + 1)
        teammap.run_automatic({'shop': self.stuck()}, settings.load(), self.rt, now=later)
        self.assertEqual(len(self.sent), 2)

    def test_logged_by_auto(self):
        settings.set_value('fixes.nudge', 'auto')
        [e] = self.auto({'shop': self.stuck()})
        self.assertEqual((e['by'], e['team'], e['action'], e['agent']), ('auto', 'shop', 'nudge', 'a'))

    def test_found_teams_never_acted_on(self):
        settings.set_value('fixes.nudge', 'auto')
        m = self.stuck()
        m['found'] = True
        self.assertEqual(self.auto({'x': m}), [])

    def test_only_the_switched_on_fix_runs(self):
        settings.set_value('fixes.compact', 'auto')
        self.assertEqual(self.auto({'shop': self.stuck()}), [])

    def test_never_answers_a_question(self):
        settings.set_value('fixes.nudge', 'auto')
        m = self.build([row('a', 'blocked')], [log_agent('a', 'waiting on you')])
        self.assertEqual(self.auto({'shop': m}), [])

    def test_send_failure_is_logged_and_does_not_stop_others(self):
        settings.set_value('fixes.nudge', 'auto')
        bad = self.stuck('a')
        bad['panes'][0]['address'] = 't-bad'
        good = self.stuck('b')
        got = self.auto({'one': bad, 'two': good})
        self.assertEqual([e['ok'] for e in got], [False, True])


class FakeRt:
    session, sock = 'assieme', '/s/hc.sock'

    def __init__(self, rows=()):
        self.rows, self.sent, self.killed = list(rows), [], []

    def list(self, labels, all=False):
        return self.rows

    def read(self, t, lines=0):
        return '⏺ ok\n'

    def send(self, t, text):
        self.sent.append((t, text))

    def kill(self, t):
        self.killed.append(t)


class BrainTest(MapCase):
    def brain(self, rows=()):
        d = tempfile.mkdtemp()
        return teammap.Brain(os.path.join(d, 'c'), os.path.join(d, 'x'), FakeRt(rows), None, NoAnswers())

    def test_team_map_unknown_team_is_empty(self):
        with mock.patch.object(status, 'herdr_terminals', return_value=None), mock.patch.object(status, 'running_cwds', return_value=set()):
            m = self.brain().map('gone')
        self.assertTrue(m['stopped'])
        self.assertEqual(m['nodes'], [])

    def test_team_map_for_a_running_team(self):
        with mock.patch.object(status, 'running_cwds', return_value=set()):
            m = self.brain([row('a', 'working')]).map('shop')
        self.assertEqual([n['role'] for n in m['nodes']], ['a'])
        self.assertFalse(m['stopped'])

    def test_quiet_pairs_lists_gone_folder(self):
        quiet.set_pair('/gone/a', '/gone/b', 'a to b')
        got = self.brain().pairs()
        self.assertEqual([p['label'] for p in got['pairs']], ['a to b'])
        b = self.brain()
        b.set_pair('/gone/a', '/gone/b', 'a to b', False)
        self.assertEqual(b.pairs()['pairs'], [])

    def test_pair_added_in_settings_can_be_stopped_from_activity(self):
        b = self.brain()
        b.set_pair('/w/a', '/w/b', 'a to b', True)
        [r] = activity.list_rows(now=status.now_utc())['rows']
        self.assertEqual(r['undo'], {'label': 'Stop'})
        self.assertTrue(activity.undo(r['id'], FakeRt())['ok'])
        self.assertFalse(quiet.is_quiet('/w/a', '/w/b'))

    def test_fix_change_is_logged_with_undo(self):
        b = self.brain()
        self.assertEqual(b.change_setting('fixes.compact', 'auto')['fixes']['compact'], 'auto')
        [r] = activity.list_rows(now=status.now_utc())['rows']
        self.assertEqual(r['undo'], {'label': 'Undo'})

    def test_a_bad_fix_value_changes_nothing(self):
        with self.assertRaises(ValueError):
            self.brain().change_setting('fixes.compact', 'always')
        self.assertEqual(live.recent_actions(), [])

    def test_tick_refreshes_today_and_runs_automatic(self):
        settings.set_value('fixes.nudge', 'auto')
        b = self.brain([row('a')])
        a = log_agent('a', 'stuck', detail='silent 18 min')
        with mock.patch.object(status, 'collect', return_value=[a]), mock.patch.object(status, 'running_cwds', return_value=set()), \
                mock.patch.object(b.today, 'refresh') as refresh:
            got = b.tick(NOW)
        refresh.assert_called_once()
        self.assertEqual([e['action'] for e in got], ['nudge'])
        self.assertEqual(len(b.rt.sent), 1)

    def test_tick_reads_no_teams_while_everything_asks_first(self):
        b = self.brain([row('a')])
        with mock.patch.object(b, 'map') as m, mock.patch.object(b.today, 'refresh'):
            self.assertEqual(b.tick(NOW), [])
        m.assert_not_called()

    def test_every_method_the_app_uses_exists(self):
        names = set(self.brain().handlers())
        self.assertTrue({'team.view', 'team.map', 'pane.act', 'agent.detail', 'tip.apply', 'team.add_agent', 'team.stop',
                         'teams.list', 'activity.list', 'activity.undo', 'settings.get', 'settings.set', 'quiet.pairs',
                         'quiet.set', 'groups.set'} <= names)


class RealCheckFindingsTest(MapCase):
    def brain(self, rows):
        d = tempfile.mkdtemp()
        return teammap.Brain(os.path.join(d, 'c'), os.path.join(d, 'x'), FakeRt(rows), None, NoAnswers())

    def test_pairs_with_running_agents_lists_them_for_this_team(self):
        quiet.set_pair(f'{WT}/a', f'{WT}/b', 'a to b')
        got = self.brain([row('a'), row('b')]).pairs('shop')
        self.assertEqual([c['role'] for c in got['agents']], ['a', 'b'])
        self.assertEqual(got['pairs'][0]['label'], 'a to b')

    def test_all_idle_subtitle_reads_plainly(self):
        self.assertEqual(self.build([row('a'), row('b')])['subtitle'], "Two idle or done. You'll get a notification if one needs you.")
        m = self.build([row('a', 'working'), row('b'), row('c')])
        self.assertEqual(m['subtitle'], "One working, two idle or done. You'll get a notification if one needs you.")
        m = self.build([row('a', 'working')])
        self.assertEqual(m['subtitle'], "One working. You'll get a notification if one needs you.")

    def test_detail_names_who_sent_a_note_by_role(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, 's.jsonl')
        with open(p, 'w') as f:
            f.write(json.dumps({'type': 'user', 'timestamp': '2026-11-08T11:00:00Z', 'message': {'content': '<hc from="t-b">go</hc>'}}) + '\n')
        a = log_agent('a')
        a.path = p
        rows = [row('a'), row('b')]
        got = teammap.detail('shop', 'a', self.build(rows, [a]), [a], {}, NOW, rows)
        self.assertEqual(got['recent'][0]['text'], 'Took a note from b')

    def test_suggested_answers_session_is_not_a_found_team(self):
        from assieme import answers
        a = Agent('assieme-answers', 'claude-code', '/l.jsonl')
        a.cwd, a.state, a.last = answers.default_workdir(), 'idle', NOW
        self.assertEqual(teammap.found_groups([a], {}, set()), {})
