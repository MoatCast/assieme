"""F1 live health on a synthetic team: one agent per state. No real logs are read."""
import datetime as dt
import json
import os
import tempfile
import unittest

from wakegraph import status

NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def ago(minutes):
    return (NOW - dt.timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def write(path, rows, mtime_minutes=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('\n'.join(json.dumps(r) for r in rows) + '\n')
    t = (NOW - dt.timedelta(minutes=mtime_minutes)).timestamp()
    os.utime(path, (t, t))


def prompt(m, text='go'):
    return {'type': 'user', 'timestamp': ago(m), 'message': {'content': text}}


def tool(m, tid, name='Bash', command=None, stop='tool_use'):
    inp = {'command': command} if command else {}
    return {'type': 'assistant', 'timestamp': ago(m), 'message': {
        'id': f'msg-{tid}', 'stop_reason': stop, 'usage': {'input_tokens': 1},
        'content': [{'type': 'tool_use', 'id': tid, 'name': name, 'input': inp}]}}


def result(m, tid):
    return {'type': 'user', 'timestamp': ago(m), 'message': {'content': [
        {'type': 'tool_result', 'tool_use_id': tid, 'content': 'ok'}]}}


def done(m):
    return {'type': 'assistant', 'timestamp': ago(m), 'message': {
        'id': f'end-{m}', 'stop_reason': 'end_turn', 'usage': {'input_tokens': 1}, 'content': [{'type': 'text', 'text': 'ok'}]}}


def team(root):
    c = os.path.join(root, 'claude')
    p = lambda name: os.path.join(c, f'-Users-x-team-{name}', 's.jsonl')
    write(p('a'), [prompt(12), tool(11, 'a1', command='hc send --if idle f "suite done; secret"'), result(10, 'a1'),
                   tool(1, 'a2', command='pytest -q tests/secret_path')])
    write(p('b'), [prompt(30), tool(20, 'b1', command='cargo build')])
    write(p('c'), [prompt(9)] + [x for i in range(4) for x in (
        tool(8 - i, f'c{i}', command='hc wait builder'), result(8 - i, f'c{i}'))])
    write(p('d'), [prompt(5), tool(4, 'd1', name='AskUserQuestion')])
    write(p('e'), [prompt(9), done(8)])
    write(os.path.join(c, '-Users-x-team-e', 's', 'subagents', 'agent-1.jsonl'), [prompt(7, 'find it'), tool(1, 'e1', name='Grep')])
    write(p('f'), [prompt(40), done(30)])
    write(p('g'), [prompt(60), done(59)])
    write(p('h'), [prompt(40), done(39)])
    write(os.path.join(c, '-Users-x-team-h', 's', 'subagents', 'agent-2.jsonl'),
          [prompt(38, 'look'), tool(25, 'h1', name='Read')], mtime_minutes=25)

    x = os.path.join(root, 'codex', '2099', '01', '01')
    ev = lambda m, kind: {'timestamp': ago(m), 'type': 'event_msg', 'payload': {'type': kind}}
    write(os.path.join(x, 'rollout-parent.jsonl'), [
        {'timestamp': ago(20), 'type': 'session_meta', 'payload': {'id': 'P', 'cwd': '/w/app', 'source': 'cli'}},
        ev(20, 'task_started'), ev(10, 'task_complete')])
    write(os.path.join(x, 'rollout-child.jsonl'), [
        {'timestamp': ago(9), 'type': 'session_meta', 'payload': {
            'id': 'C', 'cwd': '/w/app', 'source': {'subagent': {'thread_spawn': {'parent_thread_id': 'P'}}}}},
        ev(9, 'task_started')])
    write(os.path.join(x, 'rollout-api.jsonl'), [
        {'timestamp': ago(5), 'type': 'session_meta', 'payload': {'id': 'A', 'cwd': '/w/api', 'source': 'cli'}},
        ev(5, 'task_started'), ev(2, 'exec_approval_request')])
    return c, os.path.join(root, 'codex')


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.claude, self.codex = team(self.tmp.name)
        self.agents = status.collect(self.claude, self.codex, NOW)
        self.by = {a.name: a for a in self.agents}

    def tearDown(self):
        self.tmp.cleanup()

    def test_states(self):
        got = {n: a.state for n, a in self.by.items()}
        self.assertEqual(got, {
            'a': 'working', 'b': 'stuck', 'c': 'stuck', 'd': 'waiting on you', 'e': 'waiting on subagents',
            'f': 'idle, work waiting', 'g': 'idle', 'h': 'stuck',
            'codex:app': 'waiting on subagents', 'codex:api': 'waiting on you'})

    def test_details(self):
        self.assertEqual(self.by['a'].detail, 'running Bash: pytest')
        self.assertIn('silent 20 min', self.by['b'].detail)
        self.assertIn('same command x4', self.by['c'].detail)
        self.assertIn('message from a', self.by['f'].detail)
        self.assertIn('subagent silent', self.by['h'].detail)

    def test_alerts_first_and_text_hides_arguments(self):
        self.assertEqual(self.agents[0].state, 'stuck')
        text = status.to_text(self.agents, NOW)
        self.assertIn('3 stuck', text)
        self.assertNotIn('secret', text)
        self.assertEqual(json.loads(status.to_json(self.agents, NOW))['agents'][0]['state'], 'stuck')

    def test_herdr_blocked_means_waiting_on_you(self):
        agents = status.collect(self.claude, self.codex, NOW, herdr=[
            {'address': 't1', 'cwd': '/Users/x/team/a', 'state': 'live', 'status': {'state': 'blocked', 'kind': 'permission'}},
            {'address': 't2', 'cwd': '/Users/x/team/g', 'state': 'live', 'status': None},
            {'address': 't3', 'cwd': '/Users/x/team/e', 'state': 'done', 'status': {'state': 'blocked'}}])
        by = {a.name: a for a in agents}
        self.assertEqual((by['a'].state, by['a'].herdr, by['a'].detail), ('waiting on you', 'blocked', 'herdr: permission'))
        self.assertEqual((by['g'].state, by['g'].herdr), ('idle', 'unreported'))
        self.assertEqual((by['e'].state, by['e'].herdr), ('waiting on subagents', None))  # finished terminal ignored

    def test_interrupt_ends_the_turn(self):
        write(os.path.join(self.claude, '-Users-x-team-g', 's.jsonl'), [
            prompt(40), {'type': 'user', 'timestamp': ago(39), 'message': {'content': [
                {'type': 'text', 'text': '[Request interrupted by user]'}]}}])
        by = {a.name: a for a in status.collect(self.claude, self.codex, NOW)}
        self.assertEqual(by['g'].state, 'idle')

    def test_window_excludes_old_sessions(self):
        self.assertEqual(status.collect(self.claude, self.codex, NOW + dt.timedelta(hours=13)), [])


class SendTargetTest(unittest.TestCase):
    def test_targets(self):
        self.assertEqual(status._send_targets('cd x && hc send --if idle builder "a; b" && hc send --force t3 hi'),
                         ['builder', 't3'])
        self.assertEqual(status._send_targets('echo hc send'), [])


if __name__ == '__main__':
    unittest.main()
