"""Suggested answers to an agent's plain-word question, asked once in the background and cached."""
import unittest

from assieme import answers, ask


class Manual:
    def __init__(self):
        self.pending = []

    def __call__(self, target, daemon=None):
        self.pending.append(target)
        return self

    def start(self):
        pass

    def run_all(self):
        while self.pending:
            self.pending.pop(0)()


GOOD = '{"answers": ["Behind a flag", "To main"]}'


class AnswersTest(unittest.TestCase):
    def setUp(self):
        self.calls, self.reply, self.clock = [], GOOD, [0.0]
        self.manual = Manual()
        self.a = answers.Answers(ask=self.fake, thread=self.manual, now=lambda: self.clock[0])

    def fake(self, prompt, cwd, timeout_s):
        self.calls.append((prompt, cwd, timeout_s))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def test_get_never_blocks(self):
        self.assertIsNone(self.a.get('k', 'Flag or main?', '/w'))
        self.assertIsNone(self.a.get('k', 'Flag or main?', '/w'))
        self.assertEqual(len(self.manual.pending), 1)
        self.assertEqual(self.calls, [])

    def test_answers_cached(self):
        self.a.get('k', 'Flag or main?', '/w')
        self.manual.run_all()
        self.assertEqual(self.a.get('k', 'Flag or main?', '/w'), ['Behind a flag', 'To main'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1:], ('/w', 60))

    def test_failures_give_none(self):
        for reply in (ask.AskError('boom'), 'not json', '{"answers": ["only one"]}',
                      '{"answers": ["x" * 61, "ok"]}'.replace('"x" * 61', '"' + 'x' * 61 + '"')):
            a = answers.Answers(ask=lambda *x, r=reply: (_ for _ in ()).throw(r) if isinstance(r, Exception) else r,
                                thread=self.manual, now=lambda: self.clock[0])
            a.get('k', 'q?', '/w')
            self.manual.run_all()
            with self.subTest(reply=str(reply)[:20]):
                self.assertIsNone(a.get('k', 'q?', '/w'))
                self.assertEqual(self.manual.pending, [])  # not retried

    def test_cache_expires(self):
        self.a.get('k', 'q?', '/w')
        self.manual.run_all()
        self.clock[0] += 24 * 3600 + 1
        self.assertIsNone(self.a.get('k', 'q?', '/w'))
        self.assertEqual(len(self.manual.pending), 1)

    def test_prompt(self):
        self.a.get('k', 'Behind a flag, or straight to main?', '/w')
        self.manual.run_all()
        self.assertIn('Behind a flag, or straight to main?', self.calls[0][0])
        self.assertIn('Reply with only one JSON object', self.calls[0][0])


class AskTest(unittest.TestCase):
    def test_first_json(self):
        self.assertEqual(ask.first_json('x ```json\n{"a": 1}\n``` y'), {'a': 1})
        self.assertIsNone(ask.first_json('nope'))
        self.assertIsNone(ask.first_json('{bad'))
