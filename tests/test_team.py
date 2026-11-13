"""Start, status and stop a team, against a scripted fake runtime and a fake clock."""
import os
import tempfile
import unittest
from unittest import mock

from assieme import brief, runtime, team
from assieme.preset import Role, Team

TRUST_CLAUDE = 'Quick safety check\n ❯ No, exit\n   Yes, I trust this folder'
TRUST_CODEX = '› 1. Trust and continue\n  2. Quit'
HOOKS = 'Hooks need review\n› 1. Review hooks'
IDLE = '❯ \n  ⏵⏵ accept edits on'


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeRuntime:
    """Per role: a list of screens and a list of statuses, consumed one per poll (the last one repeats)."""

    def __init__(self, screens=None, statuses=None, finished=(), existing=()):
        self.screens, self.statuses = screens or {}, statuses or {}
        self.finished, self.existing = set(finished), list(existing)
        self.started, self.sent, self.keyed, self.killed, self.gets, self.labelled = [], [], [], [], {}, []
        self.role_of = {}
        self.sock = '/s/assieme/hc.sock'

    def start(self, argv, cwd, labels, name=None, parent=None):
        t = f't{len(self.started) + 1}'
        self.started.append({'argv': argv, 'cwd': cwd, 'labels': labels, 'parent': parent, 'address': t, 'name': name})
        self.role_of[t] = labels['assieme.role']
        return t

    def _next(self, table, t, default):
        seq = table.get(self.role_of[t], [default])
        n = self.gets.get((id(table), t), 0)
        self.gets[(id(table), t)] = n + 1
        return seq[min(n, len(seq) - 1)]

    def get(self, t):
        role = self.role_of[t]
        state = 'finished' if role in self.finished else 'live'
        st = self._next(self.statuses, t, {'state': 'idle'})
        return {'address': t, 'state': state, 'status': st}

    def read(self, t, lines=0):
        return self._next(self.screens, t, IDLE)

    def keys(self, t, keys):
        self.keyed.append((t, keys))

    def send(self, t, text, timeout_s=30):
        self.sent.append((t, text))
        return {'started': True}

    def kill(self, t):
        self.killed.append(t)

    def label(self, t, labels):
        self.labelled.append((t, labels))

    def list(self, labels, all=False):
        return [x for x in self.existing if all or x['state'] == 'live']


def demo(*roles):
    return Team('demo', '/p', {}, list(roles))


COORD = Role('coordinator', 'claude', 'c.md', 'You coordinate.')
BUILDER = Role('builder-1', 'codex', 'b.md', 'You build.', parent='coordinator')


class StartTest(unittest.TestCase):
    def setUp(self):
        p = [mock.patch('assieme.team.worktree.ensure', side_effect=lambda tm, r: f'/p/.assieme/worktrees/{r.name}'),
             mock.patch('assieme.team.worktree.check_repo'),
             mock.patch('assieme.team.launch.missing_clis', return_value=[]),
             mock.patch.dict(os.environ, {'XDG_STATE_HOME': tempfile.mkdtemp()})]
        for x in p:
            x.start()
            self.addCleanup(x.stop)
        self.clock = FakeClock()

    def start(self, rt, tm, task=None, ask=lambda q: True, only=None):
        return team.start(tm, task, rt, ask, only=only, clock=self.clock)

    def test_parent_started_first_and_passed_to_child(self):
        rt = FakeRuntime()
        self.start(rt, demo(COORD, BUILDER))
        self.assertEqual([s['labels']['assieme.role'] for s in rt.started], ['coordinator', 'builder-1'])
        self.assertIsNone(rt.started[0]['parent'])
        self.assertEqual(rt.started[1]['parent'], 't1')

    def test_the_safety_practice_decides_whether_pushes_are_blocked(self):
        for practices, blocked in (({}, True), ({'safety': True}, True), ({'safety': False}, False), ({'no-push': False}, False)):
            rt = FakeRuntime()
            self.start(rt, Team('demo', '/p', practices, [COORD]))
            self.assertEqual('GIT_CONFIG_COUNT=1' in rt.started[0]['argv'], blocked, practices)

    def test_the_steward_waits_for_stops_and_is_not_listed_as_waiting_for_work(self):
        rt = FakeRuntime()
        steward = Role('steward', 'claude', '', 'You are the steward.', checkout='read-only', parent='coordinator')
        self.start(rt, demo(COORD, BUILDER, steward), task='fix bug')
        top = rt.sent[0][1]
        self.assertIn('- builder-1', top)
        self.assertNotIn('- steward', top)
        self.assertIn(team.WAIT_FOR_PARENT.format(parent='coordinator'), rt.sent[2][1])

    def test_gemini_and_cursor_start_only_after_the_person_trusts_the_project(self):
        d = tempfile.mkdtemp()
        with mock.patch('assieme.team.worktree.ensure', side_effect=lambda tm, r: d):
            asked = []
            rt = FakeRuntime()
            got = self.start(rt, Team('demo', '/p', {}, [Role('g', 'gemini', 'b.md', 'brief')]),
                             ask=lambda q: asked.append(q) or False)
            self.assertEqual((got[0].state, got[0].note), ('exited', 'folder not trusted'))
            self.assertEqual(rt.started, [])
            self.assertEqual(asked, ['Trust /p so gemini agents can read, edit and run files there?'])
            rt = FakeRuntime()
            self.start(rt, Team('demo', '/p', {}, [Role('g', 'gemini', 'b.md', 'brief'), Role('c', 'cursor', 'b.md', 'brief')]))
            self.assertIn('GEMINI_CLI_TRUST_WORKSPACE=true', rt.started[0]['argv'])
            self.assertIn('--trust', rt.started[1]['argv'])

    def test_labels_and_cwd(self):
        rt = FakeRuntime()
        self.start(rt, demo(COORD))
        self.assertEqual(rt.started[0]['labels'], {'assieme.team': 'demo', 'assieme.role': 'coordinator',
                                                       'assieme.kind': 'claude'})
        self.assertEqual(rt.started[0]['cwd'], '/p/.assieme/worktrees/coordinator')

    def test_brief_sent_only_after_first_status(self):
        rt = FakeRuntime(statuses={'coordinator': [None, None, None, {'state': 'idle'}]})
        out = self.start(rt, demo(COORD), task='fix bug')
        self.assertEqual(rt.sent, [('t1', f'You coordinate.\n\n{team.TOOLS}\n\nfix bug')])
        self.assertEqual(rt.gets[(id(rt.statuses), 't1')], 4)
        self.assertEqual(out[0].state, 'running')

    def test_the_task_becomes_the_goal_and_everyone_gets_the_rules_and_decisions(self):
        brief.save('demo', rules='No API changes')
        brief.add('demo', 'coordinator', 'Flag or main?', 'Behind a flag')
        rt = FakeRuntime()
        self.start(rt, demo(COORD, BUILDER), task='fix bug')
        self.assertEqual(brief.load('demo')['goal'], 'fix bug')
        top, kid = rt.sent[0][1], rt.sent[1][1]
        for text in (top, kid):
            self.assertNotIn('Goal:', text)  # the top agent has it as its task; the other waits for its share
            self.assertIn('No API changes', text)
            self.assertIn('- Flag or main? -> Behind a flag', text)
        self.assertTrue(kid.endswith('wait and do nothing.'))

    def test_a_task_for_one_agent_leaves_the_goal(self):
        rt = FakeRuntime()
        self.start(rt, demo(COORD, BUILDER), task='team goal')
        self.start(FakeRuntime(), demo(COORD, BUILDER), task='just you', only='builder-1')
        self.assertEqual(brief.load('demo')['goal'], 'team goal')

    def test_brief_waits_while_the_screen_is_still_settling(self):
        # Codex's start-up logo animates past the 5 s hc waits: "t1 is still starting: its output has not settled".
        rt = FakeRuntime()
        refusals = [runtime.RuntimeError_('unavailable', 't1 is still starting: its output has not settled')] * 2
        real = rt.send

        def send(t, text, timeout_s=30):
            if refusals:
                raise refusals.pop()
            return real(t, text, timeout_s)
        rt.send = send
        out = self.start(rt, demo(COORD), task='fix bug')
        self.assertEqual(len(rt.sent), 1)
        self.assertEqual(out[0].state, 'running')

    def test_brief_gives_up_on_other_errors(self):
        rt = FakeRuntime()

        def send(t, text, timeout_s=30):
            raise runtime.RuntimeError_('not_found', 'no terminal "t1"')
        rt.send = send
        with self.assertRaises(runtime.RuntimeError_):
            self.start(rt, demo(COORD), task='fix bug')

    def test_claude_trust_asks_once_then_down_enter(self):
        second = Role('reviewer', 'claude', 'r.md', 'You review.')
        rt = FakeRuntime(screens={'coordinator': [TRUST_CLAUDE, IDLE], 'reviewer': [TRUST_CLAUDE, IDLE]},
                         statuses={'coordinator': [None, None, {'state': 'idle'}], 'reviewer': [None, None, {'state': 'idle'}]})
        asked = []
        self.start(rt, demo(COORD, second), ask=lambda q: asked.append(q) or True)
        self.assertEqual(len(asked), 1)
        self.assertIn('/p', asked[0])
        self.assertEqual(rt.keyed, [('t1', ['down', 'enter']), ('t2', ['down', 'enter'])])

    def test_claude_first_run_screens_are_passed_then_it_starts(self):
        theme = 'Choose the text style that looks best with your terminal\n ❯ ✔ Dark mode'
        notes = 'Security notes:\n 1. Claude can make mistakes\nPress Enter to continue…'
        rt = FakeRuntime(screens={'coordinator': [theme, notes, IDLE]},
                         statuses={'coordinator': [None, None, None, {'state': 'idle'}]})
        out = self.start(rt, demo(COORD), task='fix bug')
        self.assertEqual(rt.keyed, [('t1', ['enter']), ('t1', ['enter'])])
        self.assertEqual(out[0].state, 'running')

    def test_codex_trust_is_enter(self):
        rt = FakeRuntime(screens={'builder-1': [TRUST_CODEX, IDLE]}, statuses={'builder-1': [None, None, {'state': 'idle'}]})
        self.start(rt, demo(Role('builder-1', 'codex', 'b.md', 'x')))
        self.assertEqual(rt.keyed, [('t1', ['enter'])])

    def test_trust_declined_kills_agent(self):
        rt = FakeRuntime(screens={'coordinator': [TRUST_CLAUDE]}, statuses={'coordinator': [None]})
        out = self.start(rt, demo(COORD), ask=lambda q: False)
        self.assertEqual(rt.killed, ['t1'])
        self.assertEqual(out[0].state, 'exited')
        self.assertEqual(rt.keyed, [])

    def test_codex_hook_review_needs_you(self):
        rt = FakeRuntime(screens={'builder-1': [HOOKS]}, statuses={'builder-1': [None]})
        out = self.start(rt, demo(Role('builder-1', 'codex', 'b.md', 'x')))
        self.assertEqual(out[0].state, 'needs-you')
        # The app shows this note to people who may never use a shell: no command in it.
        self.assertEqual(out[0].note, 'Codex wants you to review new hooks once. Open builder-1 in Terminals and choose.')
        self.assertEqual((rt.keyed, rt.sent), ([], []))

    def test_not_reporting_after_60s(self):
        rt = FakeRuntime(screens={'coordinator': ['line a\nline b']}, statuses={'coordinator': [None]})
        out = self.start(rt, demo(COORD))
        self.assertEqual(out[0].state, 'not-reporting')
        self.assertIn('line b', out[0].note)
        self.assertGreaterEqual(self.clock.t, 60)
        self.assertLess(self.clock.t, 62)
        self.assertEqual(rt.sent, [])

    def test_exit_during_startup_keeps_others(self):
        rt = FakeRuntime(screens={'coordinator': ['Error: bad flag']}, finished={'coordinator'})
        other = Role('reviewer', 'claude', 'r.md', 'You review.')
        out = self.start(rt, demo(COORD, other))
        self.assertEqual([(s.role, s.state) for s in out], [('coordinator', 'exited'), ('reviewer', 'running')])
        self.assertIn('bad flag', out[0].note)

    def test_missing_cli_starts_nothing(self):
        rt = FakeRuntime()
        with mock.patch('assieme.team.launch.missing_clis', return_value=['codex']):
            with self.assertRaisesRegex(team.TeamError, 'codex'):
                self.start(rt, demo(COORD, BUILDER))
        self.assertEqual(rt.started, [])

    def test_marks_agent_briefed_after_sending(self):
        rt = FakeRuntime()
        self.start(rt, demo(COORD))
        self.assertEqual(rt.labelled, [('t1', {'assieme.briefed': '1'})])

    def test_worktree_failure_refuses_before_any_agent_starts(self):
        def ensure(tm, r):
            if r.name == 'builder-1':
                raise team.worktree.WorktreeError('branch assieme/demo/builder-1 already exists')
            return '/w'
        rt = FakeRuntime()
        with mock.patch('assieme.team.worktree.ensure', side_effect=ensure):
            with self.assertRaisesRegex(team.worktree.WorktreeError, 'already exists'):
                self.start(rt, demo(COORD, BUILDER))
        self.assertEqual(rt.started, [])

    def test_partly_running_team_starts_the_rest(self):
        live = [{'address': 't7', 'state': 'live', 'status': {'state': 'idle'},
                 'labels': {'assieme.team': 'demo', 'assieme.role': 'coordinator', 'assieme.briefed': '1'}}]
        rt = FakeRuntime(existing=live)
        out = self.start(rt, demo(COORD, BUILDER))
        self.assertEqual([(s.role, s.state) for s in out], [('coordinator', 'already-running'), ('builder-1', 'running')])
        self.assertEqual(rt.started[0]['parent'], 't7')

    def test_rerun_sends_brief_to_live_unbriefed_agent(self):
        live = [{'address': 't7', 'state': 'live', 'status': {'state': 'idle'},
                 'labels': {'assieme.team': 'demo', 'assieme.role': 'coordinator'}}]
        rt = FakeRuntime(existing=live)
        out = self.start(rt, demo(COORD), task='go')
        self.assertEqual(rt.sent, [('t7', f'You coordinate.\n\n{team.TOOLS}\n\ngo')])
        self.assertEqual(rt.labelled, [('t7', {'assieme.briefed': '1'})])
        self.assertEqual((out[0].state, rt.started), ('running', []))

    def test_rerun_while_still_waiting_on_review(self):
        live = [{'address': 't7', 'state': 'live', 'status': None,
                 'labels': {'assieme.team': 'demo', 'assieme.role': 'builder-1'}}]
        rt = FakeRuntime(existing=live)
        out = self.start(rt, demo(Role('builder-1', 'codex', 'b.md', 'x')))
        self.assertEqual(out[0].state, 'needs-you')
        self.assertEqual((rt.sent, rt.started), ([], []))

    def test_already_running_starts_nothing(self):
        live = [{'address': 't7', 'state': 'live', 'status': {'state': 'idle'},
                 'labels': {'assieme.team': 'demo', 'assieme.role': r, 'assieme.briefed': '1'}} for r in ('coordinator', 'builder-1')]
        rt = FakeRuntime(existing=live)
        out = self.start(rt, demo(COORD, BUILDER))
        self.assertEqual({s.state for s in out}, {'already-running'})
        self.assertEqual(rt.started, [])

    def test_only_one_role_reuses_running_parent(self):
        live = [{'address': 't7', 'state': 'live', 'status': {'state': 'idle'},
                 'labels': {'assieme.team': 'demo', 'assieme.role': 'coordinator'}}]
        rt = FakeRuntime(existing=live)
        out = self.start(rt, demo(COORD, BUILDER), only='builder-1')
        self.assertEqual([s['labels']['assieme.role'] for s in rt.started], ['builder-1'])
        self.assertEqual(rt.started[0]['parent'], 't7')
        self.assertEqual([(s.role, s.state) for s in out], [('builder-1', 'running')])

    def test_only_unknown_role(self):
        with self.assertRaisesRegex(team.TeamError, 'nobody'):
            self.start(FakeRuntime(), demo(COORD), only='nobody')


class WhoGetsTheTaskTest(StartTest):
    REVIEWER = Role('reviewer', 'claude', 'r.md', 'You review.', parent='coordinator')

    def sent_to(self, rt, role):
        t = next(s['address'] for s in rt.started if s['labels'][team.ROLE] == role)
        return [text for to, text in rt.sent if to == t]

    def run_team(self, *roles, task='fix bug', **kw):
        rt = FakeRuntime(statuses={r.name: [{'state': 'idle'}] for r in roles})
        self.start(rt, demo(*roles), task=task, **kw)
        return rt

    def test_the_top_agent_gets_the_task(self):
        rt = self.run_team(COORD, self.REVIEWER)
        self.assertIn('fix bug', self.sent_to(rt, 'coordinator')[0])

    def test_an_agent_with_a_parent_waits_for_it_instead(self):
        rt = self.run_team(COORD, self.REVIEWER)
        first = self.sent_to(rt, 'reviewer')[0]
        self.assertNotIn('fix bug', first)
        self.assertIn('coordinator will send you your work', first)

    def test_a_parents_first_message_says_how_to_reach_its_agents(self):
        rt = self.run_team(COORD, BUILDER, self.REVIEWER)
        [first] = self.sent_to(rt, 'coordinator')  # one message: a note sent mid-turn can go missing
        self.assertIn('- builder-1\n', first)
        self.assertIn('- reviewer\n', first)
        self.assertIn('fix bug', first)

    def test_terminals_are_named(self):
        rt = self.run_team(COORD, self.REVIEWER)
        self.assertEqual([s['name'] for s in rt.started], ['demo-coordinator', 'demo-reviewer'])

    def test_a_request_for_one_agent_reaches_it_though_it_has_a_parent(self):
        rt = FakeRuntime(statuses={'reviewer': [{'state': 'idle'}]}, existing=[])
        self.start(rt, demo(COORD, self.REVIEWER), task='fix the note', only='reviewer')
        self.assertIn('fix the note', self.sent_to(rt, 'reviewer')[0])

    def test_an_added_agent_waits_and_its_parent_hears(self):
        rt = FakeRuntime(statuses={'reviewer': [{'state': 'idle'}]},
                         existing=[{'address': 't9', 'state': 'live', 'labels': {team.TEAM: 'demo', team.ROLE: 'coordinator'}, 'status': {'state': 'idle'}}])
        self.start(rt, demo(COORD, self.REVIEWER), task=None, only='reviewer')
        self.assertIn('coordinator will send you your work', self.sent_to(rt, 'reviewer')[0])
        self.assertIn('message_agent, as "reviewer"', [text for to, text in rt.sent if to == 't9'][0])

    def test_two_agents_at_the_top_both_get_it(self):
        other = Role('other', 'claude', 'o.md', 'You build too.')
        rt = self.run_team(COORD, other)
        self.assertIn('fix bug', self.sent_to(rt, 'other')[0])


class StatusStopTest(unittest.TestCase):
    def setUp(self):
        mk = lambda t, tm, r, state: {'address': t, 'state': state, 'status': {'state': 'idle'} if state == 'live' else None,
                                      'labels': {'assieme.team': tm, 'assieme.role': r}}
        self.rt = FakeRuntime(existing=[mk('t1', 'demo', 'coordinator', 'live'), mk('t2', 'demo', 'builder-1', 'live'),
                                        mk('t3', 'demo', 'old', 'finished'), mk('t4', 'other', 'x', 'live'),
                                        {'address': 't5', 'state': 'live', 'status': None, 'labels': {}}])

    def test_status_lists_labelled_terminals(self):
        rows = team.status(self.rt, 'demo')
        self.assertEqual([(r['role'], r['state'], r['status']) for r in rows],
                         [('coordinator', 'live', 'idle'), ('builder-1', 'live', 'idle'), ('old', 'finished', None)])
        self.assertEqual(len(team.status(self.rt)), 4)  # every assieme team, not unlabelled terminals

    def test_stop_kills_live_team_terminals(self):
        self.assertEqual(team.stop(self.rt, 'demo'), 2)
        self.assertEqual(self.rt.killed, ['t1', 't2'])
