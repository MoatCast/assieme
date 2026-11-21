"""Plan limits from hand-written logs. No real logs are read.

Codex shape: `token_count` events carry `rate_limits` {primary, secondary: {used_percent, window_minutes, resets_at}},
per codex-rs protocol/src/protocol.rs `RateLimitSnapshot` / `RateLimitWindow` (read through Context7, 21 Nov 2026).
Claude Code shape: `rate_limit_info` {status, resetsAt, rateLimitType}, as the plan records it.
"""
import datetime as dt
import json
import os
import tempfile
import unittest

from assieme import limits

NOW = 1_800_000_000.0


def stamp(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('\n'.join(json.dumps(r) for r in rows) + '\n')


def codex_row(t, primary, secondary=None):
    return {'timestamp': stamp(t), 'type': 'event_msg', 'payload': {
        'type': 'token_count', 'info': None, 'rate_limits': {'primary': primary, 'secondary': secondary}}}


def claude_row(t, status, resets):
    return {'timestamp': stamp(t), 'type': 'system',
            'rate_limit_info': {'status': status, 'resetsAt': resets, 'rateLimitType': 'five_hour'}}


class Limits(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.claude, self.codex = os.path.join(self.d, 'claude'), os.path.join(self.d, 'codex')

    def read(self):
        return limits.read(self.claude, self.codex, NOW)

    def test_codex_percent_and_reset(self):
        write(os.path.join(self.codex, '2026', 'a.jsonl'), [codex_row(
            NOW - 60, {'used_percent': 72.0, 'window_minutes': 300, 'resets_at': NOW + 3600},
            {'used_percent': 30.0, 'window_minutes': 10080, 'resets_at': NOW + 86400})])
        self.assertEqual(self.read(), {'codex': {'used': 72.0, 'reached': False, 'resets_at': NOW + 3600}})

    def test_codex_reached(self):
        write(os.path.join(self.codex, 'a.jsonl'), [codex_row(NOW - 60, {'used_percent': 100.0, 'resets_at': NOW + 600})])
        self.assertTrue(self.read()['codex']['reached'])

    def test_claude_limit_hit_until_reset(self):
        write(os.path.join(self.claude, 'p', 's.jsonl'), [claude_row(NOW - 60, 'rejected', NOW + 1800)])
        self.assertEqual(self.read(), {'claude': {'used': None, 'reached': True, 'resets_at': NOW + 1800}})

    def test_claude_hit_in_the_past_is_cleared(self):
        write(os.path.join(self.claude, 'p', 's.jsonl'), [claude_row(NOW - 7200, 'rejected', NOW - 60)])
        self.assertEqual(self.read(), {})

    def test_codex_window_past_reset_is_dropped(self):
        write(os.path.join(self.codex, 'a.jsonl'), [codex_row(NOW - 7200, {'used_percent': 100.0, 'resets_at': NOW - 1})])
        self.assertEqual(self.read(), {})

    def test_no_data_no_entry(self):
        write(os.path.join(self.claude, 'p', 's.jsonl'), [{'timestamp': stamp(NOW), 'type': 'assistant', 'message': {}}])
        write(os.path.join(self.codex, 'a.jsonl'), [codex_row(NOW, None)])
        self.assertEqual(self.read(), {})

    def test_latest_line_wins(self):
        # two Claude agents on one account: one bar, from the latest line in either log
        write(os.path.join(self.claude, 'p', 'one.jsonl'), [claude_row(NOW - 300, 'rejected', NOW + 1800)])
        write(os.path.join(self.claude, 'q', 'two.jsonl'), [claude_row(NOW - 60, 'allowed', NOW + 1800)])
        write(os.path.join(self.codex, 'a.jsonl'), [codex_row(NOW - 300, {'used_percent': 90.0, 'resets_at': NOW + 600}),
                                                    codex_row(NOW - 60, {'used_percent': 40.0, 'resets_at': NOW + 600})])
        self.assertEqual(self.read(), {'codex': {'used': 40.0, 'reached': False, 'resets_at': NOW + 600}})

    def test_odd_codex_lines_are_skipped(self):
        # a non-dict payload, or a resets_at given as text, must not break the whole reading
        write(os.path.join(self.codex, 'a.jsonl'), [
            codex_row(NOW - 120, {'used_percent': 50.0, 'resets_at': NOW + 600}),
            {'timestamp': stamp(NOW - 60), 'type': 'event_msg', 'payload': ['token_count']}])
        self.assertEqual(self.read(), {'codex': {'used': 50.0, 'reached': False, 'resets_at': NOW + 600}})

    def test_codex_reset_as_text_counts_as_unknown(self):
        write(os.path.join(self.codex, 'a.jsonl'), [codex_row(NOW - 60, {'used_percent': 20.0, 'resets_at': 'soon'})])
        self.assertEqual(self.read(), {'codex': {'used': 20.0, 'reached': False, 'resets_at': None}})


if __name__ == '__main__':
    unittest.main()
