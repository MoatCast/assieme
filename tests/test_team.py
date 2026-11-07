"""Start, status and stop a team, against a scripted fake runtime and a fake clock."""
import unittest
from unittest import mock

from assieme import team
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
        self.started, self.sent, self.keyed, self.killed, self.gets = [], [], [], [], {}
        self.role_of = {}
        self.sock = '/s/assieme/hc.sock'

    def start(self, argv, cwd, labels, name=None, parent=None):
        t = f't{len(self.started) + 1}'
        self.started.append({'argv': argv, 'cwd': cwd, 'labels': labels, 'parent': parent, 'address': t})
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
             mock.patch('assieme.team.launch.missing_clis', return_value=[])]
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

    def test_labels_and_cwd(self):
        rt = FakeRuntime()
        self.start(rt, demo(COORD))
        self.assertEqual(rt.started[0]['labels'], {'assieme.team': 'demo', 'assieme.role': 'coordinator'})
        self.assertEqual(rt.started[0]['cwd'], '/p/.assieme/worktrees/coordinator')

    def test_brief_sent_only_after_first_status(self):
        rt = FakeRuntime(statuses={'coordinator': [None, None, None, {'state': 'idle'}]})
        out = self.start(rt, demo(COORD), task='fix bug')
        self.assertEqual(rt.sent, [('t1', 'You coordinate.\n\nfix bug')])
        self.assertEqual(rt.gets[(id(rt.statuses), 't1')], 4)
        self.assertEqual(out[0].state, 'running')

    def test_claude_trust_asks_once_then_down_enter(self):
        second = Role('reviewer', 'claude', 'r.md', 'You review.')
        rt = FakeRuntime(screens={'coordinator': [TRUST_CLAUDE, IDLE], 'reviewer': [TRUST_CLAUDE, IDLE]},
                         statuses={'coordinator': [None, None, {'state': 'idle'}], 'reviewer': [None, None, {'state': 'idle'}]})
        asked = []
        self.start(rt, demo(COORD, second), ask=lambda q: asked.append(q) or True)
        self.assertEqual(len(asked), 1)
        self.assertIn('/p', asked[0])
        self.assertEqual(rt.keyed, [('t1', ['down', 'enter']), ('t2', ['down', 'enter'])])

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
        self.assertIn('HC_SESSION=assieme HC_SOCKET=/s/assieme/hc.sock hc attach t1', out[0].note)
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

    def test_already_running_starts_nothing(self):
        live = [{'address': 't7', 'state': 'live', 'status': {'state': 'idle'},
                 'labels': {'assieme.team': 'demo', 'assieme.role': r}} for r in ('coordinator', 'builder-1')]
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
