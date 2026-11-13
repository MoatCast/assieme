"""Gemini CLI and OpenCode logs, in the shapes those versions write (Gemini 0.68 JSONL chats, OpenCode 1.19 SQLite)."""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from assieme import adapters

SINCE = datetime(2026, 11, 13, tzinfo=timezone.utc)


class GeminiTest(unittest.TestCase):
    def test_turns_and_tokens_from_the_last_copy_of_each_message(self):
        root = tempfile.mkdtemp()
        json.dump({'projects': {'/work/shop': 'shop'}}, open(os.path.join(root, 'projects.json'), 'w'))
        chats = os.path.join(root, 'tmp', 'shop', 'chats')
        os.makedirs(chats)
        rows = [{'sessionId': 's1', 'projectHash': 'h', 'startTime': '2026-11-13T10:00:00Z', 'kind': 'main'},
                {'id': 'u1', 'timestamp': '2026-11-13T10:00:01Z', 'type': 'user', 'content': [{'text': 'Fix the footer'}]},
                {'id': 'g1', 'timestamp': '2026-11-13T10:00:05Z', 'type': 'gemini', 'content': 'On it'},
                {'id': 'g1', 'timestamp': '2026-11-13T10:00:05Z', 'type': 'gemini', 'content': 'On it',
                 'tokens': {'input': 1200, 'output': 80, 'cached': 0, 'thoughts': 20, 'tool': 0, 'total': 1300}},
                {'id': 'u2', 'timestamp': '2026-11-13T10:01:00Z', 'type': 'user', 'content': '<hc from="planner">next</hc>'}]
        with open(os.path.join(chats, 'session-2026-11-13T10-00-s1.jsonl'), 'w') as f:
            f.write('\n'.join(json.dumps(r) for r in rows) + '\n')
        [s] = list(adapters.gemini(root, SINCE))
        self.assertEqual((s.agent, s.source, s.cwd, s.calls), ('gemini:shop', 'gemini', '/work/shop', 1))
        self.assertEqual([t.sender for t in s.turns], ['human', 'planner'])


class OpencodeTest(unittest.TestCase):
    def test_turns_and_tokens_and_nothing_else_read(self):
        db = os.path.join(tempfile.mkdtemp(), 'opencode.db')
        con = sqlite3.connect(db)
        con.executescript('''
          CREATE TABLE session (id text, directory text, parent_id text, time_updated integer);
          CREATE TABLE message (id text, session_id text, time_created integer, data text);
          CREATE TABLE part (id text, message_id text, session_id text, time_created integer, data text);''')
        at = int((SINCE + timedelta(hours=1)).timestamp() * 1000)
        con.execute('INSERT INTO session VALUES (?, ?, ?, ?)', ('s1', '/work/shop', None, at))
        con.execute('INSERT INTO message VALUES (?, ?, ?, ?)', ('m1', 's1', at, json.dumps({'role': 'user'})))
        con.execute('INSERT INTO part VALUES (?, ?, ?, ?, ?)', ('p1', 'm1', 's1', at, json.dumps({'type': 'text', 'text': 'Add a test'})))
        con.execute('INSERT INTO message VALUES (?, ?, ?, ?)', ('m2', 's1', at + 5000, json.dumps(
            {'role': 'assistant', 'tokens': {'input': 900, 'output': 60, 'reasoning': 10, 'cache': {'read': 300, 'write': 0}}})))
        con.commit()
        con.close()
        [s] = list(adapters.opencode(db, SINCE))
        self.assertEqual((s.agent, s.cwd, s.calls), ('opencode:shop', '/work/shop', 1))
        self.assertEqual(s.samples[0][1], 1200)  # input with cache reads
        self.assertEqual([t.sender for t in s.turns], ['human'])

    def test_a_missing_or_strange_database_yields_nothing(self):
        self.assertEqual(list(adapters.opencode('/nonexistent/opencode.db', SINCE)), [])
