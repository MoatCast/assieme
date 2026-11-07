"""A2 compact and A5 refusal breaker in the live view. Synthetic logs, fake hc, temp action log."""
import datetime as dt
import json
import os
import subprocess
import tempfile
import unittest

from assieme import live, status
from tests.test_status import NOW, ago, hc_wake, prompt, tool, write

DENIED = 'Permission to use Bash with command {} has been denied.'


def big_reply(m, tokens, stop='end_turn'):
    return {'type': 'assistant', 'timestamp': ago(m), 'message': {
        'id': f'big{m}', 'stop_reason': stop, 'usage': {'input_tokens': 5, 'cache_read_input_tokens': tokens},
        'content': [{'type': 'text', 'text': 'done'}]}}


def refused(m, tid, cmd):
    return {'type': 'user', 'timestamp': ago(m), 'message': {'content': [
        {'type': 'tool_result', 'tool_use_id': tid, 'is_error': True, 'content': DENIED.format(cmd)}]}}


class Team:
    def __init__(self, rows_by_agent, rules=()):
        self.tmp = tempfile.TemporaryDirectory()
        c = os.path.join(self.tmp.name, 'claude')
        for name, rows in rows_by_agent.items():
            write(os.path.join(c, f'-Users-x-team-{name}', 's.jsonl'), rows)
        self.agents = status.collect(c, os.path.join(self.tmp.name, 'none'), NOW, rules_for=lambda cwd: list(rules))

    def suggest(self, addr, done=()):
        return live.suggest(self.agents, [], addr, (), done, NOW)


class CompactTest(unittest.TestCase):
    def test_big_idle_context_gets_compact(self):
        t = Team({'coord': [hc_wake(10, 'small'), big_reply(9, 320_000)], 'small': [hc_wake(10, 'coord'), big_reply(9, 20_000)],
                  'solo': [prompt(10), big_reply(9, 400_000)]})  # a one-off session: never suggested
        self.assertEqual({a.name: a.context for a in t.agents}, {'coord': 320_005, 'small': 20_005, 'solo': 400_005})
        sugg = t.suggest({'coord': 't3'})
        self.assertEqual([(s.agent, s.action, s.message) for s in sugg], [('coord', 'compact', '/compact')])
        self.assertIn('[1] coord holds 320K tokens of context; every wake re-reads it  -> press 1 to compact',
                      live.render(t.agents, [], sugg, NOW))

    def test_busy_agent_is_left_alone(self):
        t = Team({'coord': [prompt(3), big_reply(2, 320_000, stop='tool_use'), tool(2, 'x', command='pytest')],
                  'other': [prompt(10), big_reply(9, 1000)]})
        self.assertEqual(t.suggest({'coord': 't3'}), [])

    def test_fix_just_done_stays_hidden(self):
        t = Team({'coord': [hc_wake(10, 'other'), big_reply(9, 320_000)], 'other': [prompt(10), big_reply(9, 1000)]})
        self.assertEqual(len(t.suggest({'coord': 't3'})), 1)
        self.assertEqual(t.suggest({'coord': 't3'}, done={('coord', 'compact')}), [])
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, 'a.jsonl')
            ok = lambda cmd: subprocess.CompletedProcess(cmd, 0, 'started', '')
            calls = []
            live.run_action(live.Suggestion('x', 'coord', 't3', 'compact', '/compact'),
                            lambda cmd: calls.append(cmd) or ok(cmd), log, NOW - dt.timedelta(minutes=5))
            live.run_action(live.Suggestion('x', 'other', 't4', 'compact', '/compact'),
                            lambda cmd: subprocess.CompletedProcess(cmd, 1, '', 'no'), log, NOW)
            self.assertEqual(live.acted(NOW, log), {('coord', 'compact')})  # failed actions don't hide anything
            self.assertEqual(live.acted(NOW + dt.timedelta(minutes=20), log), set())
        self.assertEqual(calls, [['hc', 'send', 't3', '/compact']])


class RefusalBreakerTest(unittest.TestCase):
    RULES = [('Bash(*push*)', 'coord.json')]

    def rows(self, times):
        out = [prompt(times[0] + 1)]
        for i, m in enumerate(times):
            cmd = f'git push origin main #{i}'
            out += [tool(m, f'p{i}', command=cmd), refused(m, f'p{i}', cmd)]
        return out

    def test_second_refusal_triggers_the_breaker(self):
        t = Team({'coord': self.rows([6, 4]), 'other': [prompt(10), big_reply(9, 1000)]}, self.RULES)
        sugg = t.suggest({'coord': 't3'})
        self.assertEqual(len(sugg), 1)
        s = sugg[0]
        self.assertEqual((s.agent, s.action), ('coord', 'tell'))
        self.assertIn('`Bash: git` refused 2 times by deny rule `Bash(*push*)` (coord.json)', s.text)
        self.assertIn('Do not retry it', s.message)

    def test_one_refusal_or_old_ones_do_not(self):
        self.assertEqual(Team({'coord': self.rows([4]), 'o': [prompt(9), big_reply(8, 1)]}, self.RULES).suggest({}), [])
        old = self.rows([90, 80]) + [big_reply(79, 1000)]
        self.assertEqual(Team({'coord': old, 'o': [prompt(9), big_reply(8, 1)]}, self.RULES).suggest({}), [])


class CodexContextTest(unittest.TestCase):
    def test_codex_context_from_token_count(self):
        with tempfile.TemporaryDirectory() as root:
            day = os.path.join(root, 'codex', '2099')
            write(os.path.join(day, 'rollout-a.jsonl'), [
                {'timestamp': ago(9), 'type': 'session_meta', 'payload': {'id': 'A', 'cwd': '/w/api', 'source': 'cli'}},
                {'timestamp': ago(9), 'type': 'event_msg', 'payload': {'type': 'task_started'}},
                {'timestamp': ago(8), 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                    'last_token_usage': {'input_tokens': 210_000}}}},
                {'timestamp': ago(7), 'type': 'event_msg', 'payload': {'type': 'task_complete'}}])
            [a] = status.collect(os.path.join(root, 'none'), os.path.join(root, 'codex'), NOW)
        self.assertEqual((a.state, a.context), ('idle', 210_000))


if __name__ == '__main__':
    unittest.main()


class CodexCompactTest(unittest.TestCase):
    def rows(self, extra):
        return [{'timestamp': ago(30), 'type': 'session_meta', 'payload': {'id': 'B', 'cwd': '/w/b3', 'source': 'cli'}},
                {'timestamp': ago(30), 'type': 'event_msg', 'payload': {'type': 'task_started'}},
                {'timestamp': ago(29), 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                    'last_token_usage': {'input_tokens': 221_000}}}},
                {'timestamp': ago(29), 'type': 'event_msg', 'payload': {'type': 'task_complete'}},
                {'timestamp': ago(20), 'type': 'event_msg', 'payload': {'type': 'task_started'}}] + extra

    def collect(self, extra, herdr=None):
        with tempfile.TemporaryDirectory() as root:
            write(os.path.join(root, 'codex', 'rollout-b.jsonl'), self.rows(extra))
            [a] = status.collect(os.path.join(root, 'none'), os.path.join(root, 'codex'), NOW, herdr=herdr)
        return a

    def test_open_turn_alone_looks_stuck(self):
        self.assertEqual(self.collect([]).state, 'stuck')

    def test_herdr_done_wins_over_an_open_turn(self):
        a = self.collect([], herdr=[{'address': 't5', 'cwd': '/w/b3', 'state': 'live', 'status': {'state': 'done'}}])
        self.assertEqual((a.state, a.herdr, a.detail), ('idle', 'done', 'herdr: done'))

    def test_compacted_row_resets_context(self):
        a = self.collect([{'type': 'compacted', 'payload': {'message': 'summary'}},
                          {'timestamp': ago(19), 'type': 'event_msg', 'payload': {'type': 'task_complete'}}])
        self.assertEqual((a.state, a.context), ('idle', 0))
