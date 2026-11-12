"""F1 live health on a synthetic team: one agent per state. No real logs are read."""
import datetime as dt
import json
import os
import tempfile
import unittest

from assieme import status

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


def hc_wake(m, sender, text='note'):
    return prompt(m, f'<hc from="t1 {sender}">{sender}: {text}</hc>')


class StallTest(unittest.TestCase):
    """F2: a team where nobody moves but work is open, and the chain of who waits on whom."""

    def run_team(self, rows_by_agent):
        with tempfile.TemporaryDirectory() as root:
            c = os.path.join(root, 'claude')
            for name, rows in rows_by_agent.items():
                write(os.path.join(c, f'-Users-x-team-{name}', 's.jsonl'), rows)
            agents = status.collect(c, os.path.join(root, 'none'), NOW)
            return agents, status.stalls(agents, NOW)

    def chain(self, builder_rows):
        return self.run_team({
            'builder': builder_rows,
            'coord': [hc_wake(19, 'builder'), tool(16, 'c1', command='hc send steward "need a ruling"'),
                      result(16, 'c1'), done(15)],
            'steward': [hc_wake(14, 'coord'), tool(10, 's1', name='AskUserQuestion')],
            'loner': [prompt(30), done(29)],
        })

    def test_chain_ends_at_permission_prompt(self):
        agents, stalls = self.chain([prompt(25), tool(21, 'b1', command='hc send coord "suite done"'),
                                     result(21, 'b1'), done(20)])
        by = {a.name: a for a in agents}
        self.assertEqual((by['builder'].waits_on, by['coord'].waits_on, by['steward'].waits_on),
                         (['coord'], ['steward'], ['you']))
        self.assertEqual(len(stalls), 1)
        self.assertEqual(stalls[0]['team'], ['builder', 'coord', 'steward'])  # loner is not on the team
        self.assertIn('2 agents wait on steward (builder, coord); steward waits on you', stalls[0]['text'])
        self.assertIn('Team stalled for 10 min', status.to_text(agents, NOW))

    def test_no_stall_while_someone_works(self):
        _, stalls = self.chain([prompt(25), tool(21, 'b1', command='hc send coord "suite done"'),
                                result(21, 'b1'), tool(1, 'b2', command='pytest')])
        self.assertEqual(stalls, [])

    def test_hc_wait_on_a_stuck_agent(self):
        agents, stalls = self.run_team({
            'coord': [prompt(30), tool(29, 'c1', command='hc send builder "build it"'), result(29, 'c1'),
                      tool(18, 'c2', command='hc wait builder')],
            'builder': [hc_wake(28, 'coord'), tool(20, 'b1', command='cargo build')],
        })
        by = {a.name: a for a in agents}
        self.assertEqual((by['coord'].state, by['coord'].waits_on), ('working', ['builder']))
        self.assertIn('1 agent waits on builder (coord); builder is stuck', stalls[0]['text'])

    def test_team_that_is_simply_done_is_not_stalled(self):
        _, stalls = self.run_team({
            'coord': [prompt(30), tool(29, 'c1', command='hc send builder "build it"'), result(29, 'c1'), done(28),
                      hc_wake(24, 'builder'), done(23)],
            'builder': [hc_wake(27, 'coord'), tool(26, 'b1', command='hc send coord "built"'), result(26, 'b1'), done(25)],
        })
        self.assertEqual(stalls, [])  # coord read builder's report and finished: a report, not a request

    def test_lost_report_is_a_stall(self):
        _, stalls = self.run_team({
            'coord': [prompt(30), tool(29, 'c1', command='hc send builder "build it"'), result(29, 'c1'), done(28)],
            'builder': [hc_wake(27, 'coord'), tool(26, 'b1', command='hc send coord "built"'), result(26, 'b1'), done(25)],
        })
        self.assertIn('coord never started on its message', stalls[0]['text'])


class RootTest(unittest.TestCase):
    def test_loop(self):
        a, b = status.Agent('a', 'x', ''), status.Agent('b', 'x', '')
        a.waits_on, b.waits_on = ['b'], ['a']
        self.assertEqual(status._root(a, {'a': a, 'b': b}), (a, True))
        self.assertIn('a and b wait on each other', status._blocker(a, True))


class ExitedAndExpiryTest(unittest.TestCase):
    def test_closed_session_is_exited_not_stuck(self):
        with tempfile.TemporaryDirectory() as root:
            c, x = team(root)
            for name in ('b', 'g'):
                path = os.path.join(c, f'-Users-x-team-{name}', 's.jsonl')
                rows = [json.loads(l) for l in open(path)]
                for r in rows:
                    r['cwd'] = f'/w/{name}'
                write(path, rows)
            by = {a.name: a for a in status.collect(c, x, NOW, running={'/w/g'})}
        self.assertEqual((by['b'].state, by['g'].state), ('exited', 'idle'))

    def test_old_request_expires_once_receiver_had_a_turn(self):
        agents, stalls = StallTest().run_team({
            'review': [prompt(300), tool(299, 'r1', command='hc send coord "please re-check"'), result(299, 'r1'), done(298)],
            'coord': [prompt(60), done(59)],  # a later turn, started by someone else
        })
        self.assertEqual({a.name: a.waits_on for a in agents}['review'], [])
        self.assertEqual(stalls, [])


class NamesTest(unittest.TestCase):
    def test_unrelated_project_does_not_spoil_team_names(self):
        from assieme.adapters import _strip_common_prefix
        home = __import__('re').sub(r'[^A-Za-z0-9]', '-', os.path.expanduser('~'))
        names = [f'{home}-Developer-Team-coord', f'{home}-Developer-Team-Alpha', f'{home}-Developer-agent-wakegraph',
                 f'{home}--codex-worktrees-x-Site']
        self.assertEqual(sorted(_strip_common_prefix(names).values()),
                         ['Alpha', 'agent-wakegraph', 'coord', 'x-Site'])


class HerdrCheckTest(unittest.TestCase):
    def test_no_herdr_core_runs_no_hc_command(self):
        # Any `hc` command starts a herdr core of its own: First run's check must not start one by looking.
        from unittest import mock
        with mock.patch.object(status, 'herdr_core_up', return_value=False), \
                mock.patch.object(status.subprocess, 'run') as run:
            self.assertEqual(status.herdr_terminals(), [])
        run.assert_not_called()

    def test_a_running_core_is_read_with_herdrs_own_hc(self):
        from unittest import mock
        got = mock.Mock(stdout=json.dumps({'terminals': []}))
        with mock.patch.object(status, 'herdr_core_up', return_value=True), \
                mock.patch.object(status, 'hc_path', return_value='/herdr/bin/hc'), \
                mock.patch.object(status.subprocess, 'run', return_value=got) as run:
            self.assertEqual(status.herdr_terminals(), [])
        self.assertEqual(run.call_args.args[0][0], '/herdr/bin/hc')
