"""Terminals panes: each Assieme agent's state, the needs-you strip, fixes, menus and suggested answers."""
import datetime as dt
import os
import unittest

from assieme import brief, launch, panes, team
from assieme.preset import Role
from assieme.status import Agent

NOW = dt.datetime(2026, 11, 7, 12, 0, tzinfo=dt.timezone.utc)
MS = int(NOW.timestamp() * 1000)
WT = '/p/.assieme/worktrees'


def fixture(name):
    with open(os.path.join(os.path.dirname(__file__), 'fixtures', 'screens', name)) as f:
        return f.read()


def row(role, status='idle', state='live', agent='claude', created=0, status_ago_s=30):
    return {'address': f't-{role}', 'state': state, 'status': {'state': status}, 'status_ms': MS - status_ago_s * 1000,
            'cwd': f'{WT}/{role}', 'argv': [agent, '--settings', '{}'], 'created_ms': created,
            'labels': {team.TEAM: 'shop', team.ROLE: role}}


def log_agent(name, role, state='idle', last_ago_s=10, **kw):
    a = Agent(name, 'claude-code', f'/logs/{name}.jsonl')
    a.cwd, a.state, a.last = f'{WT}/{role}', state, NOW - dt.timedelta(seconds=last_ago_s)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


class FakeAnswers:
    def __init__(self, result=None):
        self.result, self.asked = result, []

    def get(self, key, question):
        self.asked.append((key, question))
        return self.result


QUESTION = '⏺ Split into parts.\n\n  Before part 4: behind a flag,\n  or straight to main?\n\n───\n❯ \n───\n'


def view(rows, agents=(), screens=None, answers=None):
    screens = screens or {}
    return panes.view('shop', rows, list(agents), NOW, lambda a: screens.get(a, '⏺ ok\n'), answers or FakeAnswers())


def pane(v, role):
    return next(p for p in v['panes'] if p['role'] == role)


class AgentKindTest(unittest.TestCase):
    def test_a_codex_agent_started_with_the_push_block_is_still_codex(self):
        for kind in ('claude', 'codex'):
            r = row('dev')
            r['argv'] = launch.argv(Role('dev', kind, 'b.md', 'brief'), which=lambda n: f'/home/me/.local/bin/{n}')
            self.assertEqual(r['argv'][0], '/usr/bin/env')
            self.assertEqual(pane(view([r]), 'dev')['agent'], kind)


class MatchTest(unittest.TestCase):
    def test_match_by_worktree(self):
        a = log_agent('x', 'builder')
        self.assertEqual(panes.match([row('builder'), row('reviewer')], [a]), {'t-builder': a})

    def test_match_prefers_most_recent(self):
        old, new = log_agent('old', 'builder', last_ago_s=900), log_agent('new', 'builder', last_ago_s=5)
        self.assertIs(panes.match([row('builder')], [old, new])['t-builder'], new)


class ViewTest(unittest.TestCase):
    def test_states(self):
        rows = [row('a', 'blocked', created=1), row('b', created=2), row('c', created=3), row('d', 'working', created=4),
                row('e', created=5), row('f', state='finished', created=6)]
        agents = [log_agent('B', 'b', 'stuck', detail='same failure 4 times'), log_agent('C', 'c', 'idle, work waiting')]
        v = view(rows, agents)
        self.assertEqual([(p['role'], p['state'], p['live']) for p in v['panes']],
                         [('a', 'asking', True), ('b', 'stuck', True), ('c', 'waiting', True), ('d', 'working', True),
                          ('e', 'idle', True), ('f', 'exited', False)])
        self.assertEqual(pane(v, 'a')['agent'], 'claude')

    def test_log_waiting_on_you_is_asking(self):
        v = view([row('a')], [log_agent('A', 'a', 'waiting on you', ask_since=NOW - dt.timedelta(minutes=12))])
        self.assertEqual((pane(v, 'a')['state'], pane(v, 'a')['since_s']), ('asking', 720))

    def test_strip_order(self):
        rows = [row('w', created=1), row('s', created=2), row('q', 'blocked', created=3)]
        agents = [log_agent('W', 'w', 'idle, work waiting'), log_agent('S', 's', 'stuck', detail='loops')]
        v = view(rows, agents, screens={'t-q': QUESTION})
        self.assertEqual([s['role'] for s in v['strip']], ['q', 's', 'w'])
        self.assertEqual(v['strip'][0]['text'], 'q is asking: Before part 4: behind a flag, or straight to main?')

    def test_menu_buttons(self):
        v = view([row('a', 'blocked')], screens={'t-a': fixture('claude_permission.txt')})
        b = pane(v, 'a')['fix']['buttons']
        self.assertEqual([(x['kind'], x['send']) for x in b], [('menu', '1'), ('menu', '2'), ('menu', '3'), ('reply', None)])
        # Its own permission menu in Assieme's words; the middle choice (accept edits for the session) keeps its own.
        self.assertEqual(b[0]['label'], 'Allow once')
        self.assertTrue(b[1]['label'].startswith('Yes, and switch to accept edits'))
        self.assertEqual(b[2]['label'], 'Block')

    def test_answers_pending_gives_reply_only(self):
        ans = FakeAnswers(None)
        v = view([row('a', 'blocked')], screens={'t-a': QUESTION}, answers=ans)
        self.assertEqual([x['kind'] for x in pane(v, 'a')['fix']['buttons']], ['reply'])
        key, question = ans.asked[0]
        self.assertEqual(question, 'Before part 4: behind a flag, or straight to main?')
        self.assertTrue(key.startswith('shop/a/'))

    def test_answer_buttons(self):
        v = view([row('a', 'blocked')], screens={'t-a': QUESTION}, answers=FakeAnswers(['Behind a flag', 'To main']))
        b = pane(v, 'a')['fix']['buttons']
        self.assertEqual([(x['kind'], x['label'], x['send']) for x in b],
                         [('answer', 'Behind a flag', 'Behind a flag'), ('answer', 'To main', 'To main'), ('reply', 'Reply', None)])

    def test_actions_from_suggestions(self):
        stuck = log_agent('S', 's', 'stuck', detail='same failure')
        v = view([row('s')], [stuck])
        b = pane(v, 's')['fix']['buttons']
        self.assertEqual((b[0]['kind'], b[0]['label']), ('action', 'Ask it to try another way'))
        self.assertTrue(b[0]['send'].startswith('assieme:'))
        big = log_agent('B', 'b', 'idle', context=200_000)
        big.peers = {log_agent('X', 'x')}
        b = pane(view([row('b')], [big]), 'b')['fix']['buttons']
        self.assertEqual((b[0]['label'], b[0]['send']), ('Compact', '/compact'))
        refused = log_agent('R', 'r', 'working', refused=[(NOW, 'git push', 'deny rule', '')] * 2)
        b = pane(view([row('r', 'working')], [refused]), 'r')['fix']['buttons']
        self.assertEqual(b[0]['label'], 'Tell it to stop retrying')

    def test_exited_pane(self):
        p = pane(view([row('a', state='finished')], screens={'t-a': 'work\nError: boom\n\n'}), 'a')
        self.assertEqual((p['fix'], p['detail']), (None, 'Error: boom'))

    def test_ids_stable(self):
        s = {'t-a': fixture('claude_permission.txt')}
        f1, f2 = pane(view([row('a', 'blocked')], screens=s), 'a')['fix'], pane(view([row('a', 'blocked')], screens=s), 'a')['fix']
        self.assertEqual(f1, f2)
        f3 = pane(view([row('a', 'blocked')], screens={'t-a': fixture('claude_question.txt')}), 'a')['fix']
        self.assertNotEqual(f1['id'], f3['id'])

    def test_unmatched_role_still_has_pane(self):
        p = pane(view([row('a', 'working', status_ago_s=90)]), 'a')
        self.assertEqual((p['state'], p['since_s'], p['fix']), ('working', 90, None))


class FakeRt:
    def __init__(self, fail=None):
        self.keys_sent, self.sent, self.fail = [], [], fail

    def keys(self, t, keys):
        self.keys_sent.append((t, keys))

    def send(self, t, text, timeout_s=30):
        if self.fail:
            raise self.fail
        self.sent.append((t, text))


class ActTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.log = os.path.join(tempfile.mkdtemp(), 'actions.jsonl')
        self.screens = {'t-a': fixture('claude_permission.txt')}
        self.rows = [row('a', 'blocked')]
        self.now_view = lambda: view(self.rows, screens=self.screens,
                                     answers=FakeAnswers(['Behind a flag', 'To main']))

    def logged(self):
        import json
        with open(self.log) as f:
            return [json.loads(x) for x in f]

    def act(self, rt, fix, button):
        return panes.act('shop', 'a', fix, button, self.now_view, rt, log=self.log)

    def test_act_menu_presses_digit(self):
        f = pane(self.now_view(), 'a')['fix']
        rt = FakeRt()
        self.assertEqual(self.act(rt, f['id'], f['buttons'][1]['id']), {'ok': True, 'detail': 'pressed 2'})
        self.assertEqual(rt.keys_sent, [('t-a', ['2'])])
        e = self.logged()[0]
        self.assertEqual((e['action'], e['agent'], e['ok']), ('menu', 'a', True))

    def test_act_refuses_changed_menu(self):
        f = pane(self.now_view(), 'a')['fix']
        self.screens['t-a'] = fixture('claude_question.txt')
        rt = FakeRt()
        self.assertEqual(self.act(rt, f['id'], f['buttons'][0]['id']), {'ok': False, 'detail': panes.CHANGED})
        self.assertEqual(rt.keys_sent, [])

    def test_act_answer_and_action_send(self):
        self.screens['t-a'] = QUESTION
        f = pane(self.now_view(), 'a')['fix']
        rt = FakeRt()
        self.assertTrue(self.act(rt, f['id'], f['buttons'][1]['id'])['ok'])
        self.assertEqual(rt.sent, [('t-a', 'To main')])
        self.assertEqual([(d['q'], d['a']) for d in brief.load('shop')['decisions']], [(f['question'], 'To main')])
        self.rows = [row('a')]
        stuck = log_agent('A', 'a', 'stuck', detail='loops')
        self.now_view = lambda: view(self.rows, [stuck])
        f = pane(self.now_view(), 'a')['fix']
        self.assertTrue(self.act(rt, f['id'], f['buttons'][0]['id'])['ok'])
        self.assertTrue(rt.sent[-1][1].startswith('assieme:'))
        self.assertEqual([e['action'] for e in self.logged()], ['answer', 'nudge'])

    def test_act_reply_does_nothing(self):
        f = pane(self.now_view(), 'a')['fix']
        rt = FakeRt()
        self.assertEqual(self.act(rt, f['id'], f['buttons'][-1]['id']), {'ok': True, 'detail': ''})
        self.assertEqual((rt.keys_sent, rt.sent), ([], []))

    def test_act_failure_reported(self):
        from assieme import runtime
        self.screens['t-a'] = QUESTION
        f = pane(self.now_view(), 'a')['fix']
        r = self.act(FakeRt(fail=runtime.RuntimeError_('unavailable', 'no core')), f['id'], f['buttons'][0]['id'])
        self.assertEqual(r, {'ok': False, 'detail': 'no core'})
        self.assertFalse(self.logged()[0]['ok'])


class ReviewFixTest(unittest.TestCase):
    def test_restarted_team_keeps_the_live_row_per_role(self):
        old = row('builder', state='finished', created=1)
        new = dict(row('builder', 'blocked', created=5), address='t-new')
        v = view([old, new], screens={'t-new': fixture('claude_permission.txt')})
        self.assertEqual([(p['address'], p['state']) for p in v['panes']], [('t-new', 'asking')])
        self.assertEqual([s['role'] for s in v['strip']], ['builder'])

    def test_finished_only_keeps_newest(self):
        a, b = row('builder', state='finished', created=1), dict(row('builder', state='finished', created=9), address='t-b')
        self.assertEqual([p['address'] for p in view([a, b])['panes']], ['t-b'])

    def test_same_menu_for_another_file_is_a_different_fix(self):
        s1 = fixture('claude_permission.txt')
        f1 = pane(view([row('a', 'blocked')], screens={'t-a': s1}), 'a')['fix']
        f2 = pane(view([row('a', 'blocked')], screens={'t-a': s1.replace('note.txt', 'b.py')}), 'a')['fix']
        self.assertNotEqual(f1['id'], f2['id'])
        self.assertNotEqual(f1['buttons'][0]['id'], f2['buttons'][0]['id'])


class EarlierRunTest(unittest.TestCase):
    def test_roles_from_an_earlier_run_are_hidden(self):
        old = [row('reviewer', state='finished', created=1_000), row('perm', state='finished', created=2_000)]
        now = [row('menu', 'blocked', created=5_000_000), row('plain', created=5_003_000),
               row('quit', state='finished', created=5_004_000)]
        self.assertEqual([p['role'] for p in view(old + now)['panes']], ['menu', 'plain', 'quit'])

    def test_a_stopped_team_shows_its_last_run(self):
        rows = [row('old', state='finished', created=1_000), row('a', state='finished', created=9_000_000),
                row('b', state='finished', created=9_010_000)]
        self.assertEqual([p['role'] for p in view(rows)['panes']], ['a', 'b'])
