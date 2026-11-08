"""Settings file: safe defaults, atomic writes. Also who-woke-whom tags and the action log entry shape."""
import datetime as dt
import json
import os
import tempfile
import unittest

from assieme import adapters, live, panes, settings, team

NOW = dt.datetime(2026, 11, 8, 12, 0, tzinfo=dt.timezone.utc)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), 'settings.json')

    def test_missing_file_gives_defaults(self):
        s = settings.load(self.path)
        self.assertEqual(s['fixes'], {'nudge': 'ask', 'compact': 'ask', 'tell': 'ask'})
        self.assertEqual(s['notify'], {'needs_you': True, 'stalls': True, 'finished': True})
        self.assertIs(s['show_commands'], False)

    def test_corrupt_file_gives_defaults(self):
        with open(self.path, 'w') as f:
            f.write('{not json')
        self.assertEqual(settings.load(self.path), settings.defaults())

    def test_a_bad_fix_value_is_ask(self):
        with open(self.path, 'w') as f:
            json.dump({'fixes': {'nudge': 'yes', 'compact': 'auto'}, 'show_commands': 'sure'}, f)
        s = settings.load(self.path)
        self.assertEqual(s['fixes'], {'nudge': 'ask', 'compact': 'auto', 'tell': 'ask'})
        self.assertIs(s['show_commands'], False)

    def test_set_value_merges_and_leaves_no_temp_file(self):
        settings.set_value('fixes.compact', 'auto', self.path)
        s = settings.set_value('first_run_done', True, self.path)
        self.assertEqual(s['fixes']['compact'], 'auto')
        self.assertIs(s['first_run_done'], True)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ['settings.json'])

    def test_unknown_key_refused(self):
        for key, value in (('nope', 1), ('fixes.delete_everything', 'auto'), ('fixes.nudge', 'yes')):
            with self.assertRaises(ValueError):
                settings.set_value(key, value, self.path)


class WakeTagTest(unittest.TestCase):
    def test_real_tag_has_only_the_address(self):
        self.assertEqual(adapters.wake_sender('<hc from="t2">kid says OK</hc>'), ('t2', 'hc'))

    def test_tag_with_a_name_and_reach(self):
        self.assertEqual(adapters.wake_sender('<hc from="t3 lead" reach="hc send t3 when done">x</hc>'), ('lead', 'hc'))
        self.assertEqual(adapters.wake_sender('<hc from="t15t builder">x</hc>'), ('builder', 'hc'))

    def test_person_typing_is_human(self):
        self.assertEqual(adapters.wake_sender('please look at from="x"')[0], 'human')

    def test_session_keeps_its_folder(self):
        d = tempfile.mkdtemp()
        project = os.path.join(d, '-p')
        os.makedirs(project)
        with open(os.path.join(project, 's.jsonl'), 'w') as f:
            f.write(json.dumps({'type': 'assistant', 'timestamp': '2026-11-08T11:00:00Z', 'cwd': '/p/wt',
                                'message': {'id': 'a', 'usage': {'input_tokens': 5, 'output_tokens': 1}}}) + '\n')
        [s] = list(adapters.claude_code(d, NOW - dt.timedelta(hours=24)))
        self.assertEqual(s.cwd, '/p/wt')


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.log = os.path.join(tempfile.mkdtemp(), 'actions.jsonl')

    def test_record_writes_id_by_team(self):
        e = live.record('nudge', 'builder', 'silent', team='shop', log=self.log, now=NOW)
        with open(self.log) as f:
            saved = json.loads(f.read())
        self.assertEqual(saved, e)
        self.assertTrue(e['id'])
        self.assertEqual((e['by'], e['team'], e['at']), ('you', 'shop', '2026-11-08T12:00:00Z'))

    def test_pane_act_log_has_no_question_text(self):
        fix = {'id': 'f', 'text': 'Ship behind a flag?', 'buttons': [{'id': 'b', 'label': 'Yes', 'kind': 'answer', 'send': 'Yes'}]}
        view = {'panes': [{'role': 'a', 'address': 't1', 'fix': fix}]}

        class Rt:
            def send(self, *a):
                pass
        self.assertTrue(panes.act('shop', 'a', 'f', 'b', lambda: view, Rt(), log=self.log, now=NOW)['ok'])
        with open(self.log) as f:
            body = f.read()
        self.assertNotIn('Ship behind a flag', body)
        self.assertEqual(json.loads(body)['reason'], panes.ANSWERED)


class AnnounceSwitchTest(unittest.TestCase):
    def test_announce_respects_notify_switches(self):
        store = os.path.join(tempfile.mkdtemp(), 'n.json')
        queue = [{'key': 'q', 'agent': 'a', 'kind': 'a question', 'seconds': 5, 'blocks': []}]
        stalls = [{'text': 'stalled', 'quiet_seconds': 5, 'roots': [{'agent': 'a'}]}]
        off = {'needs_you': False, 'stalls': False}
        self.assertEqual(live.announce(queue, stalls, NOW, store, lambda *a: None, off), [])
        sent = live.announce(queue, stalls, NOW, store, lambda *a: None, {'needs_you': True, 'stalls': False})
        self.assertEqual([t for t, _ in sent], ['a needs you'])
