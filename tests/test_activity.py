"""Activity: rows in plain words, filters, and Undo only where it really undoes something."""
import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

from assieme import activity, live, quiet, settings, team

NOW = dt.datetime(2026, 11, 8, 12, 0, tzinfo=dt.timezone.utc)


class Rt:
    def __init__(self, labels=None):
        self.killed, self.labels = [], labels if labels is not None else {team.TEAM: 'shop', team.ROLE: 'builder-4'}

    def get(self, t):
        return {'address': t, 'state': 'live', 'labels': self.labels}

    def kill(self, t):
        self.killed.append(t)

    def list(self, labels, all=False):
        return []


class ActivityCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {'XDG_STATE_HOME': self.tmp.name})
        self.env.start()
        self.log = live.log_path()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def rec(self, action, agent='a', ago_h=0, **kw):
        kw.setdefault('reason', 'why')
        return live.record(action, agent, kw.pop('reason'), now=NOW - dt.timedelta(hours=ago_h), **kw)

    def rows(self, **kw):
        return activity.list_rows(now=NOW, **kw)['rows']

    def undo(self, e, rt=None, now=NOW):
        return activity.undo(e['id'], rt or Rt(), now=now)


class ListTest(ActivityCase):
    def test_newest_first_with_plain_titles(self):
        self.rec('nudge', 'builder', ago_h=2)
        self.rec('compact', 'tester', ago_h=1)
        self.assertEqual([r['title'] for r in self.rows()], ['Freed up memory for tester', 'Asked builder to try another way'])

    def test_filters_all_by_you_automatic(self):
        self.rec('nudge', 'a')
        self.rec('compact', 'b', by='auto')
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual([r['title'] for r in self.rows(filter='you')], ['Asked a to try another way'])
        self.assertEqual([r['title'] for r in self.rows(filter='auto')], ['Freed up memory for b'])

    def test_old_rows_without_by_count_as_you(self):
        os.makedirs(os.path.dirname(self.log), exist_ok=True)
        with open(self.log, 'w') as f:
            f.write(json.dumps({'at': '2026-11-08T10:00:00Z', 'action': 'nudge', 'agent': 'x', 'reason': 'r', 'ok': True}) + '\n')
        [r] = self.rows(filter='you')
        self.assertEqual((r['by'], r['undo']), ('you', None))

    def test_old_answer_rows_hide_their_reason(self):
        os.makedirs(os.path.dirname(self.log), exist_ok=True)
        with open(self.log, 'w') as f:
            f.write(json.dumps({'at': '2026-11-08T10:00:00Z', 'action': 'answer', 'agent': 'x', 'reason': 'Ship it?', 'ok': True}) + '\n')
        [r] = self.rows()
        self.assertNotIn('Ship it', json.dumps(r))

    def test_skips_corrupt_lines(self):
        self.rec('nudge')
        with open(self.log, 'a') as f:
            f.write('{broken\n"just a string"\n')
        self.assertEqual(len(self.rows()), 1)

    def test_reads_last_500_lines(self):
        os.makedirs(os.path.dirname(self.log), exist_ok=True)
        with open(self.log, 'w') as f:
            for i in range(600):
                f.write(json.dumps({'at': '2026-11-08T10:00:00Z', 'action': 'nudge', 'agent': f'a{i}', 'reason': '', 'ok': True}) + '\n')
        rows = self.rows()
        self.assertEqual(len(rows), 500)
        self.assertEqual(rows[0]['title'], 'Asked a599 to try another way')

    def test_team_filter_keeps_rows_without_team(self):
        self.rec('nudge', 'a', team='shop')
        self.rec('nudge', 'b', team='other')
        self.rec('fix_mode', 'c', fix='nudge', old='ask', new='auto')
        self.assertEqual(len(self.rows(team='shop')), 2)

    def test_failed_row_says_so(self):
        self.rec('nudge', 'a', ok=False, detail='no terminal')
        [r] = self.rows()
        self.assertFalse(r['ok'])
        self.assertIn('no terminal', r['reason'])


class WordsTest(ActivityCase):
    def test_a_fix_setting_reads_in_plain_words(self):
        activity.change_fix('nudge', 'auto')
        self.assertEqual(activity.list_rows()['rows'][0]['title'], 'Set "Ask stuck agents to try another way" to Automatic')

    def test_a_given_up_delivery_has_a_title(self):
        live.record('dropped', 'b', 'x', by='auto', team='t')
        self.assertEqual(activity.list_rows()['rows'][0]['title'], "Couldn't deliver held notes to b")


class WhichTeamTest(ActivityCase):
    def test_a_fix_from_the_command_line_stays_out_of_each_teams_list(self):
        live.record('nudge', 'someone', 'x')
        live.record('nudge', 'mine', 'x', team='t')
        self.assertEqual([r['agent'] for r in activity.list_rows(team='t')['rows']], ['mine'])

    def test_settings_for_every_team_still_show(self):
        activity.change_fix('nudge', 'auto')
        self.assertEqual(len(activity.list_rows(team='t')['rows']), 1)


class OldRowsTest(ActivityCase):
    def test_an_old_request_row_shows_no_words(self):
        live.record('ask-changes', 'b', 'reviewer on a.py line 2: cap it', team='t', title='Asked b for changes')
        self.assertEqual(activity.list_rows()['rows'][0]['reason'], '')


class UndoTest(ActivityCase):
    def quiet_row(self):
        quiet.set_pair('/w/a', '/w/b', 'a -> b')
        return self.rec('quiet', 'a -> b', pair=['/w/a', '/w/b', 'a -> b'])

    def test_undo_quiet_stops_pair_keeps_held(self):
        e = self.quiet_row()
        quiet.hold_note('t2', '/w/b', 'note', None, None)
        self.assertEqual(self.rows()[0]['undo'], {'label': 'Stop'})
        self.assertTrue(self.undo(e)['ok'])
        self.assertFalse(quiet.is_quiet('/w/a', '/w/b'))
        self.assertEqual(len(quiet.held()[os.path.realpath('/w/b')]), 1)

    def test_quiet_stop_has_no_time_limit(self):
        e = self.quiet_row()
        self.assertTrue(self.undo(e, now=NOW + dt.timedelta(days=9))['ok'])

    def test_quiet_row_has_no_stop_once_removed_elsewhere(self):
        self.quiet_row()
        quiet.set_pair('/w/a', '/w/b', 'a -> b', on=False)
        self.assertIsNone(self.rows()[0]['undo'])

    def test_undo_fix_mode_restores(self):
        settings.set_value('fixes.compact', 'auto')
        e = self.rec('fix_mode', 'compact', fix='compact', old='ask', new='auto')
        self.assertEqual(self.rows()[0]['undo'], {'label': 'Undo'})
        self.assertTrue(self.undo(e)['ok'])
        self.assertEqual(settings.load()['fixes']['compact'], 'ask')

    def test_undo_practice_restores(self):
        settings.set_value('practices.reviews', False)
        e = self.rec('practice', 'Careful reviews', group='reviews', old=True, new=False)
        self.assertTrue(self.undo(e)['ok'])
        self.assertIs(settings.load()['practices']['reviews'], True)

    def test_undo_add_agent_kills_only_that_terminal(self):
        e = self.rec('add_agent', 'builder-4', team='shop', address='t9')
        rt = Rt()
        self.assertEqual(self.rows()[0]['undo']['label'], 'Stop')
        self.assertTrue(self.undo(e, rt)['ok'])
        self.assertEqual(rt.killed, ['t9'])

    def test_undo_add_agent_refuses_another_agents_terminal(self):
        e = self.rec('add_agent', 'builder-4', team='shop', address='t9')
        rt = Rt({team.TEAM: 'shop', team.ROLE: 'someone-else'})
        self.assertFalse(self.undo(e, rt)['ok'])
        self.assertEqual(rt.killed, [])

    def test_undo_after_24h_refused(self):
        settings.set_value('fixes.compact', 'auto')
        e = self.rec('fix_mode', 'compact', fix='compact', old='ask', new='auto', ago_h=25)
        self.assertIsNone(self.rows()[0]['undo'])
        r = self.undo(e)
        self.assertFalse(r['ok'])
        self.assertIn('24 hours', r['detail'])
        self.assertEqual(settings.load()['fixes']['compact'], 'auto')

    def test_undo_twice_refused(self):
        settings.set_value('fixes.compact', 'auto')
        e = self.rec('fix_mode', 'compact', fix='compact', old='ask', new='auto')
        self.assertTrue(self.undo(e)['ok'])
        settings.set_value('fixes.compact', 'auto')
        r = self.undo(e)
        self.assertFalse(r['ok'])
        self.assertEqual(settings.load()['fixes']['compact'], 'auto')

    def test_compact_and_nudge_have_no_undo(self):
        a, b = self.rec('compact', 'x'), self.rec('nudge', 'y')
        self.assertEqual([r['undo'] for r in self.rows()], [None, None])
        self.assertFalse(self.undo(a)['ok'])
        self.assertFalse(self.undo(b)['ok'])

    def test_unknown_id_refused(self):
        self.assertFalse(activity.undo('nope', Rt(), now=NOW)['ok'])

    def test_undo_is_logged_as_a_row_without_undo(self):
        settings.set_value('fixes.compact', 'auto')
        e = self.rec('fix_mode', 'compact', fix='compact', old='ask', new='auto')
        self.undo(e)
        rows = self.rows()
        self.assertEqual(rows[0]['title'], 'Undid: Set "Free up memory between tasks" to Automatic')
        self.assertIsNone(rows[0]['undo'])
        self.assertIsNone(rows[1]['undo'])

    def test_merge_undoes_through_the_review_screen(self):
        class Review:
            undos = {'m1': {}}

            def undo(self, id):
                del self.undos[id]
                live.record('undo-merge', 'b', 'back', of=id, title='Undid the merge of b')
        self.addCleanup(lambda: (activity.UNDO.pop('merge', None), activity.APPLIES.pop('merge', None)))
        activity.register_merge(Review())
        live._log(self.log, {'id': 'm1', 'at': '2026-11-08T12:00:00Z', 'action': 'merge', 'agent': 'b', 'team': 'shop', 'by': 'you', 'ok': True,
                             'undo': True, 'title': 'Merged b into main', 'reason': 'Its worktree is archived.'})
        self.assertEqual(self.rows()[0]['undo'], {'label': 'Undo'})
        self.assertTrue(activity.undo('m1', Rt(), now=NOW)['ok'])
        rows = self.rows()
        self.assertEqual([r['title'] for r in rows], ['Undid the merge of b', 'Merged b into main'])
        self.assertIsNone(rows[1]['undo'])
        self.assertFalse(activity.undo('m1', Rt(), now=NOW)['ok'])

    def test_merge_undo_works_after_a_brain_restart(self):
        class Review:
            undos = {}
            got = None

            def undo(self, id):
                Review.got = self.undos.pop(id)
                live.record('undo-merge', 'b', 'back', of=id, title='Undid the merge of b')
        self.addCleanup(lambda: (activity.UNDO.pop('merge', None), activity.APPLIES.pop('merge', None)))
        activity.register_merge(Review())
        live._log(self.log, {'id': 'm1', 'at': '2026-11-08T12:00:00Z', 'action': 'merge', 'agent': 'b', 'by': 'you', 'ok': True,
                             'title': 'Merged b', 'restore': {'tip': 'abc'}})
        self.assertEqual(self.rows()[0]['undo'], {'label': 'Undo'})
        self.assertTrue(activity.undo('m1', Rt(), now=NOW)['ok'])
        self.assertEqual(Review.got, {'tip': 'abc'})
        self.assertIsNone(self.rows()[1]['undo'])  # still in the log, but it has been undone
        self.assertFalse(activity.undo('m1', Rt(), now=NOW)['ok'])

    def test_a_refused_merge_undo_shows_its_reason_and_stays(self):
        class Review:
            undos = {'m1': {}}

            def undo(self, id):
                raise ValueError('main has moved on')
        self.addCleanup(lambda: (activity.UNDO.pop('merge', None), activity.APPLIES.pop('merge', None)))
        activity.register_merge(Review())
        live._log(self.log, {'id': 'm1', 'at': '2026-11-08T12:00:00Z', 'action': 'merge', 'agent': 'b', 'by': 'you', 'ok': True, 'title': 'Merged b'})
        self.assertEqual(activity.undo('m1', Rt(), now=NOW), {'ok': False, 'detail': 'main has moved on'})
        self.assertIsNotNone(self.rows()[0]['undo'])

    def test_practice_change_holds_one_note_per_running_agent(self):
        class Live(Rt):
            session, sock = 'assieme', '/s/hc.sock'

            def list(self, labels, all=False):
                return [{'address': 't1', 'state': 'live', 'cwd': '/w/a', 'labels': {team.TEAM: 'shop'}},
                        {'address': 't2', 'state': 'finished', 'cwd': '/w/b', 'labels': {team.TEAM: 'shop'}},
                        {'address': 't3', 'state': 'live', 'cwd': '/w/c', 'labels': {}}]
        activity.change_practice('reviews', False, Live())
        self.assertEqual(list(quiet.held()), [os.path.realpath('/w/a')])
        [n] = quiet.held()[os.path.realpath('/w/a')]
        self.assertEqual((n['to'], n['session']), ('t1', 'assieme'))
        self.assertIn('Careful reviews', n['text'])
        self.assertIs(settings.load()['practices']['reviews'], False)
        [r] = self.rows()
        self.assertEqual(r['title'], 'Turned Careful reviews off')

    def test_one_rule_changes_alone_and_undo_restores_it(self):
        class Live(Rt):
            session, sock = 'assieme', '/s/hc.sock'

            def list(self, labels, all=False):
                return [{'address': 't1', 'state': 'live', 'cwd': '/w/a', 'labels': {team.TEAM: 'shop'}}]
        e = activity.change_practice('report-line', False, Live())
        self.assertEqual(settings.load()['practices'], {'report-line': False})
        [n] = quiet.held()[os.path.realpath('/w/a')]
        self.assertIn('Every brief ends with a report line', n['text'])
        self.assertEqual(self.rows()[0]['title'], 'Turned Every brief ends with a report line off')
        activity.undo(e['id'], Live())
        self.assertIs(settings.load()['practices']['report-line'], True)

    def test_a_bigger_team_switch_tells_no_running_agent(self):
        class Live(Rt):
            def list(self, labels, all=False):
                raise AssertionError('running agents are not told')
        e = activity.change_practice('steward', True, Live())
        self.assertEqual((e['old'], e['new']), (False, True))
        self.assertEqual(self.rows()[0]['title'], 'Turned Steward agent on')
        self.assertEqual(quiet.held(), {})
