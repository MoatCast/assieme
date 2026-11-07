"""All tests build synthetic logs in a temp dir; none read real logs."""
import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import unittest

from wakegraph import adapters, cli, report

SINCE = dt.datetime(2098, 12, 31, tzinfo=dt.timezone.utc)
FUTURE = 4_100_000_000  # mtime in 2099, after SINCE


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('\n'.join(json.dumps(r) for r in rows) + '\nnot json\n')
    os.utime(path, (FUTURE, FUTURE))


def ts(i, s=0):
    return f'2099-01-01T00:{i:02d}:{s:02d}.000Z'


def claude_team(root):
    """coord gets 25 hc wakes from builder (each: one call), 3 refusals; a subagent; a human turn; an old call."""
    rows = []
    for i in range(25):
        rows.append({'type': 'user', 'timestamp': ts(i), 'message': {
            'content': '<hc from="t15t builder">builder: suite done SECRET-TEXT</hc>'}})
        rows.append({'type': 'assistant', 'timestamp': ts(i, 1), 'message': {
            'id': f'm{i}', 'usage': {'cache_read_input_tokens': 300_000, 'input_tokens': 10, 'output_tokens': 5},
            'content': [{'type': 'tool_use', 'id': f't{i}', 'name': 'Bash',
                         'input': {'command': 'hc send steward --token abc'}}]}})
        # the same call logged again for a second content block: must not count twice
        rows.append({'type': 'assistant', 'timestamp': ts(i, 1), 'message': {
            'id': f'm{i}', 'usage': {'cache_read_input_tokens': 300_000, 'input_tokens': 10}, 'content': []}})
        if i < 3:
            rows.append({'type': 'user', 'timestamp': ts(i, 2), 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': f't{i}', 'is_error': True,
                 'content': 'Permission for this action was denied by the auto mode classifier'}]}})
    rows.append({'type': 'user', 'timestamp': ts(40), 'isMeta': True, 'message': {'content': 'injected'}})
    rows.append({'type': 'user', 'timestamp': ts(41), 'message': {'content': [{'type': 'text', 'text': 'please review'}]}})
    for k in range(4):
        rows.append({'type': 'assistant', 'timestamp': ts(41, k + 1),
                     'message': {'id': f'h{k}', 'usage': {'input_tokens': 1000}}})
    rows.append({'type': 'user', 'timestamp': ts(50), 'message': {
        'content': '<teammate-message teammate_id="reviewer" color="blue">LGTM</teammate-message>'}})
    rows.append({'type': 'assistant', 'timestamp': '1999-01-01T00:00:00Z', 'message': {'id': 'old', 'usage': {'input_tokens': 9}}})
    proj = os.path.join(root, '-Users-x-src-team-coord')
    write_jsonl(os.path.join(proj, 's1.jsonl'), rows)
    write_jsonl(os.path.join(root, '-Users-x-src-team-builder', 's2.jsonl'), [
        {'type': 'user', 'timestamp': ts(1), 'message': {'content': 'go'}},
        {'type': 'assistant', 'timestamp': ts(1, 1), 'message': {'id': 'b', 'usage': {'input_tokens': 50}}}])
    sub = os.path.join(proj, 's1', 'subagents', 'agent-a1.jsonl')
    write_jsonl(sub, [
        {'type': 'user', 'timestamp': ts(5), 'isSidechain': True, 'message': {'content': 'find the bug'}},
        *[{'type': 'assistant', 'timestamp': ts(5, k), 'message': {'id': f's{k}', 'usage': {'input_tokens': 2000}}}
          for k in range(5)]])
    with open(sub[:-6] + '.meta.json', 'w') as f:
        json.dump({'agentType': 'Explore'}, f)


def codex_pair(root):
    day = os.path.join(root, '2099', '01', '01')
    write_jsonl(os.path.join(day, 'rollout-parent.jsonl'), [
        {'timestamp': ts(0), 'type': 'session_meta', 'payload': {'id': 'P', 'cwd': '/w/app', 'source': 'cli'}},
        {'timestamp': ts(0), 'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [
            {'type': 'input_text', 'text': '<environment_context>cwd</environment_context>'}]}},
        {'timestamp': ts(1), 'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [
            {'type': 'input_text', 'text': 'fix it'}]}},
        {'timestamp': ts(1, 1), 'type': 'response_item', 'payload': {
            'type': 'function_call', 'name': 'exec_command', 'call_id': 'c1',
            'arguments': json.dumps({'cmd': 'git push origin main'})}},
        {'timestamp': ts(1, 2), 'type': 'response_item', 'payload': {
            'type': 'function_call_output', 'call_id': 'c1', 'output': 'exec: sandbox_apply: Operation not permitted'}},
        *[{'timestamp': ts(1, 3 + k), 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
            'total_token_usage': {'total_tokens': 1000 * (k // 2 + 1)},  # each count emitted twice
            'last_token_usage': {'input_tokens': 40_000, 'output_tokens': 10}}}} for k in range(6)],
    ])
    write_jsonl(os.path.join(day, 'rollout-child.jsonl'), [
        {'timestamp': ts(2), 'type': 'session_meta', 'payload': {
            'id': 'C', 'cwd': '/w/app', 'source': {'subagent': {'thread_spawn': {'parent_thread_id': 'P'}}}}},
        {'timestamp': ts(2), 'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [
            {'type': 'input_text', 'text': 'check tests'}]}},
        {'timestamp': ts(2, 1), 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
            'total_token_usage': {'total_tokens': 5}, 'last_token_usage': {'input_tokens': 7000}}}},
    ])


class ClaudeCodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        claude_team(self.tmp.name)
        self.sessions = {s.agent: s for s in adapters.claude_code(self.tmp.name, SINCE)}

    def tearDown(self):
        self.tmp.cleanup()

    def test_agents_named_without_common_prefix(self):
        self.assertEqual(set(self.sessions), {'coord', 'builder', 'coord/Explore'})

    def test_calls_deduplicated_and_old_rows_dropped(self):
        c = self.sessions['coord']
        self.assertEqual(c.calls, 29)
        self.assertEqual(c.peak, 300_010)

    def test_wakes_by_channel(self):
        senders = [(t.sender, t.channel) for t in self.sessions['coord'].turns]
        self.assertEqual(senders.count(('builder', 'hc')), 25)
        self.assertIn(('human', 'human'), senders)
        self.assertIn(('reviewer', 'team'), senders)
        self.assertEqual(len(senders), 27)  # isMeta row is not a wake

    def test_subagent_woken_by_parent(self):
        t = self.sessions['coord/Explore'].turns[0]
        self.assertEqual((t.sender, t.channel, t.calls, t.tokens), ('coord', 'subagent', 5, 10_000))

    def test_refusal_label_hides_arguments(self):
        self.assertEqual(dict(self.sessions['coord'].refusals), {'Bash: hc': 3})

    def test_show_text(self):
        s = {s.agent: s for s in adapters.claude_code(self.tmp.name, SINCE, show_text=True)}['coord']
        self.assertEqual(dict(s.refusals), {'Bash: hc send steward': 3})
        self.assertIn('SECRET-TEXT', s.turns[0].text)

    def test_report_flags(self):
        r = report.aggregate(self.sessions.values())
        self.assertEqual(len(r['flags']), 3, r['flags'])
        edge = next(e for e in r['edges'] if e['from'] == 'builder' and e['to'] == 'coord')
        self.assertEqual((edge['wakes'], edge['empty'], edge['tokens']), (25, 25, 25 * 300_010))


class CodexTest(unittest.TestCase):
    def test_session_spawn_and_refusal(self):
        with tempfile.TemporaryDirectory() as root:
            codex_pair(root)
            s = {s.agent: s for s in adapters.codex(root, SINCE)}
        self.assertEqual(set(s), {'codex:app', 'codex:app/subagent'})
        p = s['codex:app']
        self.assertEqual((p.calls, p.tokens, len(p.turns)), (3, 120_000, 1))
        self.assertEqual(dict(p.refusals), {'exec_command: git': 1})
        self.assertEqual(s['codex:app/subagent'].turns[0].sender, 'codex:app')


class GenericTest(unittest.TestCase):
    def test_generic_events(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'e.jsonl')
            write_jsonl(path, [
                {'ts': '2099-01-01T00:00:00+02:00', 'agent': 'a', 'type': 'wake', 'from': 'b'},
                {'ts': '2099-01-01T00:00:01Z', 'agent': 'a', 'type': 'call', 'input_tokens': 100},
                {'ts': '2099-01-01T00:00:02Z', 'agent': 'a', 'type': 'refusal', 'command': 'rm -rf /'},
                {'ts': '1999-01-01T00:00:00Z', 'agent': 'a', 'type': 'call', 'input_tokens': 5}])
            [s] = adapters.generic(path, SINCE)
        self.assertEqual((s.calls, s.tokens, s.turns[0].sender, dict(s.refusals)), (1, 100, 'b', {'cmd: rm': 1}))


class CliTest(unittest.TestCase):
    def run_cli(self, *args):
        with tempfile.TemporaryDirectory() as root:
            claude_team(os.path.join(root, 'claude'))
            codex_pair(os.path.join(root, 'codex'))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                # synthetic logs are dated 2099; a huge window reaches back to cover "now" too
                cli.main(['report', '--hours', '1e6', '--claude-dir', os.path.join(root, 'claude'),
                          '--codex-dir', os.path.join(root, 'codex'), *args])
            return out.getvalue()

    def test_json(self):
        r = json.loads(self.run_cli('--json'))
        self.assertIn('coord', r['agents'])
        self.assertIn('codex:app', r['agents'])
        self.assertEqual(len(r['flags']), 3)

    def test_markdown_hides_text_by_default(self):
        md = self.run_cli('--md')
        self.assertIn('## Who woke whom', md)
        self.assertIn('| builder | coord | hc | 25 | 25 |', md)
        self.assertNotIn('SECRET-TEXT', md)
        self.assertNotIn('abc', md)


if __name__ == '__main__':
    unittest.main()
