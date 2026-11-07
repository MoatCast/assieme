"""F4: refusals explained by source and rule, with a narrower rule suggested. Synthetic settings only."""
import datetime as dt
import json
import os
import tempfile
import unittest

from wakegraph import adapters, report, rules

DENIED = 'Permission to use Bash with command {} has been denied.'


class ExplainTest(unittest.TestCase):
    R = [('Bash(*cloud-launch*)', 'proj/.claude/settings.local.json'),
         ('Bash(git push:*)', 'proj/.claude/settings.json'),
         ('Edit(./secrets/**)', 'proj/.claude/settings.json')]

    def why(self, text, tool='Bash', command=None, path=None):
        return rules.explain(text, tool, command, self.R, path)

    def test_rule_that_matched_only_text(self):
        cmd = 'cd x && grep -n cloud-launch notes.md'
        why, note = self.why(DENIED.format(cmd), command=cmd)
        self.assertEqual(why, 'deny rule `Bash(*cloud-launch*)` (proj/.claude/settings.local.json)')
        self.assertIn('matched a word in the command, not the program run (grep)', note)
        self.assertIn('`Bash(cloud-launch:*)`', note)

    def test_rule_that_matched_the_program(self):
        why, note = self.why(DENIED.format('cloud-launch --gpu'), command='cloud-launch --gpu')
        self.assertIn('Bash(*cloud-launch*)', why)
        self.assertEqual(note, '')
        why, _ = self.why(DENIED.format('git push origin main'), command='git status; git push origin main')
        self.assertIn('Bash(git push:*)', why)

    def test_rule_not_found(self):
        self.assertEqual(self.why(DENIED.format('ls'), command='ls')[0], 'deny rule (not found in readable settings)')

    def test_other_sources(self):
        self.assertEqual(self.why('Permission for this action was denied by the Claude Code auto mode classifier. '
                                  'Reason: [Permission Grant]. If you have', command='git push origin')[0],
                         'auto-mode classifier (Permission Grant)')
        self.assertEqual(self.why('Permission for this command was denied by a built-in Claude Code safety check, '
                                  'not by the user.')[0], 'built-in safety check')
        self.assertEqual(self.why('<tool_use_error>Blocked: sleep 30 followed by: ls.')[0], 'built-in sleep guard')
        self.assertEqual(self.why("sed to '/w/proj/secrets/key.txt' was blocked by a deny rule.")[0],
                         'deny rule `Edit(./secrets/**)` (proj/.claude/settings.json)')

    def test_refused_pattern(self):
        for text in ("sed to '/a' was blocked by a deny rule.", DENIED.format('x'),
                     '<tool_use_error>File is in a directory that is denied by your permission settings.'):
            self.assertTrue(rules.REFUSED.search(text), text)
        self.assertFalse(rules.REFUSED.search('Exit code 1\nnpm warn install-scripts blocked'))


class EndToEndTest(unittest.TestCase):
    def test_report_names_rule_and_fix(self):
        with tempfile.TemporaryDirectory() as root:
            cwd = os.path.join(root, 'work', 'steward')
            os.makedirs(os.path.join(cwd, '.claude'))
            with open(os.path.join(cwd, '.claude', 'settings.local.json'), 'w') as f:
                json.dump({'permissions': {'deny': ['Bash(*cloud-launch*)']}}, f)
            rows = [{'type': 'user', 'timestamp': '2099-01-01T00:00:00Z', 'cwd': cwd, 'message': {'content': 'go'}}]
            for i in range(3):
                cmd = f'hc send coord "do not use cloud-launch yet {i}"'
                rows += [{'type': 'assistant', 'timestamp': f'2099-01-01T00:0{i}:01Z', 'cwd': cwd, 'message': {
                            'id': f'm{i}', 'usage': {'input_tokens': 10},
                            'content': [{'type': 'tool_use', 'id': f't{i}', 'name': 'Bash', 'input': {'command': cmd}}]}},
                         {'type': 'user', 'timestamp': f'2099-01-01T00:0{i}:02Z', 'cwd': cwd, 'message': {'content': [
                             {'type': 'tool_result', 'tool_use_id': f't{i}', 'is_error': True,
                              'content': DENIED.format(cmd)}]}}]
            proj = os.path.join(root, 'claude', '-work-steward')
            os.makedirs(proj)
            path = os.path.join(proj, 's.jsonl')
            with open(path, 'w') as f:
                f.write('\n'.join(json.dumps(r) for r in rows))
            os.utime(path, (4_100_000_000, 4_100_000_000))
            sessions = list(adapters.claude_code(os.path.join(root, 'claude'), dt.datetime(2098, 1, 1, tzinfo=dt.timezone.utc),
                                                 user_settings=os.path.join(root, 'none.json')))
            r = report.aggregate(sessions)
        flag = next(f for f in r['flags'] if 'refused' in f)
        self.assertIn("'Bash: hc' refused 3 times by deny rule `Bash(*cloud-launch*)`", flag)
        self.assertIn('not the program run (hc); deny running it instead: `Bash(cloud-launch:*)`', flag)
        md = report.to_md(r, {'hours': 24, 'since': 'x'})
        self.assertNotIn('do not use', md)  # the message itself is never shown


if __name__ == '__main__':
    unittest.main()


class RuleSourcesTest(unittest.TestCase):
    def test_extra_and_cwd_rules_reach_the_matcher(self):
        with tempfile.TemporaryDirectory() as root:
            role = os.path.join(root, 'coord.json')
            with open(role, 'w') as f:
                json.dump({'permissions': {'deny': ['Bash(*push*)'], 'allow': ['Bash(ls:*)']}}, f)
            self.assertEqual(rules.load_file(role), [('Bash(*push*)', role)])
            self.assertEqual(rules.load_file(os.path.join(root, 'missing.json')), [])
