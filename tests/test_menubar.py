"""The menu bar panel and what pops up: one notification per thing, Open first, nothing routine."""
import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

from assieme import live, menubar, settings, status
from assieme.status import Agent

NOW = dt.datetime(2026, 11, 8, 12, 0, tzinfo=dt.timezone.utc)
FIX = {'id': 'f1', 'text': 'Behind a flag, or to main?', 'buttons': [
    {'id': 'b1', 'label': 'Behind a flag', 'kind': 'answer', 'send': 'Behind a flag'},
    {'id': 'b2', 'label': 'To main', 'kind': 'answer', 'send': 'To main'},
    {'id': 'b3', 'label': 'Reply', 'kind': 'reply', 'send': None}]}
ST = {'notify': {'needs_you': True, 'stalls': True, 'finished': True}, 'fixes': {'nudge': 'auto', 'compact': 'ask', 'tell': 'ask'}}


def pane(role='planner', state='idle', since_s=5, fix=None, detail=''):
    return {'role': role, 'address': f't-{role}', 'state': state, 'since_s': since_s, 'fix': fix, 'detail': detail}


def team(*ps, name='shop', tokens=0, matched=None):
    return (name, {'panes': list(ps), 'spend': {'tokens': tokens} if tokens else None}, matched or {})


def ready(role='builder-1', age=300, tip='abc'):
    return {'team': 'shop', 'project': 'p', 'role': role, 'branch': f'assieme/shop/{role}', 'ahead': 1, 'tip': tip,
            'at': int(NOW.timestamp()) - age, 'subject': 'Backoff in retry.py'}


class Reviews:
    def __init__(self, tests='ok', said=()):
        self.tests, self.said = tests, said

    def get(self, team_, role):
        if self.tests == 'gone':
            raise OSError('no folder')
        return {'checks': [{'id': 'tests', 'state': self.tests}]}

    def _read_once(self, path, read):
        return self.said


class Case(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for target, value in ((status, 'now_utc'),):
            p = mock.patch.object(target, value, return_value=NOW)
            p.start()
            self.addCleanup(p.stop)
        self.settings_file = os.path.join(d.name, 'settings.json')
        for name, fake in (('path', lambda: self.settings_file),):
            p = mock.patch.object(settings, name, fake)
            p.start()
            self.addCleanup(p.stop)
        log = os.path.join(d.name, 'actions.jsonl')
        self.log = log
        p = mock.patch.object(live, 'log_path', lambda: log)
        p.start()
        self.addCleanup(p.stop)
        self.m = menubar.Menubar(None, Reviews(), store=os.path.join(d.name, 'seen.json'))

    def events(self, *teams, waiting=(), stalls=(), now=NOW, st=ST):
        return self.m.events(list(teams), list(waiting), list(stalls), now, st)


class NeedsYouTest(Case):
    def test_a_question_notifies_once_with_open_first(self):
        t = team(pane(state='asking', fix=FIX))
        got = self.events(t)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['title'], 'planner is asking you')
        self.assertEqual(got[0]['body'], 'Behind a flag, or to main?')
        self.assertEqual(got[0]['fix'], 'f1')
        # Clicking "Options" presses the first: it must never answer. Reply is what Open does.
        self.assertEqual([b['label'] for b in got[0]['buttons']], ['Open', 'Behind a flag', 'To main'])
        self.assertEqual(self.events(t), [])

    def test_labels_stay_short(self):
        long = {'id': 'f', 'text': 'q', 'buttons': [
            {'id': 'a', 'label': 'Yes, allow all edits during this session and everything else too (shift+tab)', 'kind': 'menu'}]}
        label = self.events(team(pane(state='asking', fix=long)))[0]['buttons'][1]['label']
        self.assertLessEqual(len(label), menubar.MAX_LABEL)

    def test_a_plain_question_waits_for_its_answers_then_goes_without(self):
        bare = {'id': 'f', 'text': 'Which one?', 'buttons': [{'id': 'r', 'label': 'Reply', 'kind': 'reply', 'send': None}]}
        self.assertEqual(self.events(team(pane(state='asking', since_s=5, fix=bare))), [])
        got = self.events(team(pane(state='asking', since_s=31, fix=bare)))
        self.assertEqual([b['label'] for b in got[0]['buttons']], ['Open'])

    def test_a_stuck_agent_is_one_notification_as_the_minutes_pass(self):
        fix = {'id': 'f', 'text': 'x', 'buttons': [{'id': 'n', 'label': 'Ask it to try another way', 'kind': 'action', 'send': 's'}]}
        got = self.events(team(pane(state='stuck', since_s=60, fix=fix, detail='no progress for 1 min')))
        self.assertEqual(got[0]['title'], 'planner seems stuck')
        self.assertEqual([b['label'] for b in got[0]['buttons']], ['Open', 'Ask it to try another way'])
        self.assertEqual(self.events(team(pane(state='stuck', since_s=120, fix=fix, detail='no progress for 2 min'))), [])

    def test_routine_progress_never_notifies(self):
        self.assertEqual(self.events(team(pane(state='working'), pane('b', 'idle'))), [])

    def test_too_old_stays_quiet(self):
        self.assertEqual(self.events(team(pane(state='asking', since_s=3 * 3600, fix=FIX))), [])

    def test_asked_again_later_notifies_again_but_a_flicker_does_not(self):
        t = team(pane(state='asking', fix=FIX))
        self.events(t)
        self.assertEqual(self.events(team(), now=NOW + dt.timedelta(minutes=5)), [])
        self.assertEqual(self.events(t, now=NOW + dt.timedelta(minutes=6)), [])  # a flicker
        self.events(team(), now=NOW + dt.timedelta(minutes=30))  # answered, and forgotten
        self.assertEqual(len(self.events(t, now=NOW + dt.timedelta(minutes=31))), 1)

    def test_the_switch_turns_it_off(self):
        st = {**ST, 'notify': {**ST['notify'], 'needs_you': False}}
        self.assertEqual(self.events(team(pane(state='asking', fix=FIX)), st=st), [])


class ReadyTest(Case):
    def test_finished_work_says_how_its_tests_went(self):
        got = self.events(team(pane('builder-1', 'idle')), waiting=[ready()])
        self.assertEqual(got[0]['title'], 'Ready to review')
        self.assertEqual(got[0]['body'], 'builder-1 finished “Backoff in retry.py”. Tests pass.')
        self.assertEqual((got[0]['view'], got[0]['buttons']), ('review', []))

    def test_a_busy_agents_commit_is_routine(self):
        self.assertEqual(self.events(team(pane('builder-1', 'working')), waiting=[ready()]), [])

    def test_it_waits_for_the_tests_but_not_for_ever(self):
        self.m.reviews.tests = 'running'
        self.assertEqual(self.events(team(pane('builder-1')), waiting=[ready(age=10)]), [])
        got = self.events(team(pane('builder-1')), waiting=[ready(age=200)])
        self.assertEqual(got[0]['body'], 'builder-1 finished “Backoff in retry.py”.')

    def test_a_new_commit_is_new_work_and_an_old_one_is_not_news(self):
        self.events(team(pane('builder-1')), waiting=[ready()])
        self.assertEqual(len(self.events(team(pane('builder-1')), waiting=[ready(tip='def')])), 1)
        self.assertEqual(self.events(team(), waiting=[ready('old', age=3 * 3600)]), [])

    def test_a_gone_folder_is_still_ready(self):
        self.m.reviews.tests = 'gone'
        self.assertEqual(len(self.events(team(), waiting=[ready()])), 1)


class ReadyStartsNoWorkTest(Case):
    """Asking Review starts the project's tests and reads its whole diff: only when a notification depends on it."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(self.m.reviews, 'get', wraps=self.m.reviews.get)
        self.get = p.start()
        self.addCleanup(p.stop)

    def test_paused_runs_no_tests(self):
        settings.set_value('notify_paused_until', (NOW + dt.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ'))
        self.assertEqual(self.events(team(pane('builder-1')), waiting=[ready()]), [])
        self.get.assert_not_called()

    def test_finished_switched_off_runs_no_tests(self):
        off = {**ST, 'notify': {**ST['notify'], 'finished': False}}
        self.assertEqual(self.events(team(pane('builder-1')), waiting=[ready()], st=off), [])
        self.get.assert_not_called()

    def test_already_notified_asks_once_and_stays_remembered(self):
        self.assertEqual(len(self.events(team(pane('builder-1')), waiting=[ready()])), 1)
        later = NOW + dt.timedelta(seconds=menubar.FORGET_S + 60)
        self.assertEqual(self.events(team(pane('builder-1')), waiting=[ready()], now=later), [])
        self.assertEqual(self.get.call_count, 1)

    def test_still_true_after_a_pause_it_notifies_then(self):
        settings.set_value('notify_paused_until', (NOW + dt.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ'))
        self.events(team(pane('builder-1')), waiting=[ready()])
        settings.set_value('notify_paused_until', None)
        self.assertEqual(len(self.events(team(pane('builder-1')), waiting=[ready()])), 1)


class StopTest(Case):
    def agent(self):
        return {'t-builder-1': Agent('builder-1', 'claude-code', '/logs/b.jsonl')}

    def test_an_agent_that_says_stopped_notifies_with_its_reason(self):
        self.m.reviews.said = ['working on it', '**Stopped:** the test would need weakening.\nDetails follow']
        got = self.events(team(pane('builder-1'), matched=self.agent()))
        self.assertEqual((got[0]['title'], got[0]['body']), ('builder-1 stopped', 'the test would need weakening.'))
        self.assertEqual(self.events(team(pane('builder-1'), matched=self.agent())), [])

    def test_saying_stopped_in_passing_is_not_a_stop(self):
        self.m.reviews.said = ['I stopped the server: all good.']
        self.assertEqual(self.events(team(pane('builder-1'), matched=self.agent())), [])

    def test_a_terminal_that_ended_notifies_unless_you_ended_it_or_it_is_ready(self):
        exited = pane('builder-1', 'exited')
        self.assertEqual(self.events(team(exited), waiting=[ready(age=3 * 3600)]), [])  # its work is what you hear about
        self.assertEqual(self.events(team(exited, name='a'), waiting=[])[0]['body'], 'It has stopped running.')
        live.record('merge', 'builder-1', 'merged', team='b', log=self.log)
        self.assertEqual(self.events(team(exited, name='b')), [])

    def test_an_old_terminal_with_no_age_is_not_news(self):
        self.assertEqual(self.events(team(pane('x', 'exited', since_s=None))), [])

    def test_the_finished_switch_covers_stops_and_ready(self):
        st = {**ST, 'notify': {**ST['notify'], 'finished': False}}
        self.assertEqual(self.events(team(pane('x', 'exited')), waiting=[ready()], st=st), [])


class StallTest(Case):
    def test_a_stall_notifies_once(self):
        s = {'text': 'a waits on b', 'quiet_seconds': 60, 'roots': [{'agent': 'b'}]}
        got = self.events(team(), stalls=[s])
        self.assertEqual(got[0]['title'], 'Team stalled')
        self.assertEqual(self.events(team(), stalls=[s]), [])
        self.assertEqual(self.m.events([], [], [{**s, 'roots': [{'agent': 'c'}], 'quiet_seconds': 9999999}], NOW, ST), [])


class PauseTest(Case):
    def test_nothing_pops_up_while_paused_and_what_is_still_true_comes_after(self):
        brain = mock.Mock()
        brain.teams.return_value = {'teams': []}
        brain.today.ready = True
        self.m.brain = brain
        t = team(pane(state='asking', fix=FIX))
        panel = self.m.pause()
        self.assertIsNotNone(panel['paused_until'])
        self.assertEqual(settings.load()['notify_paused_until'], '2026-11-08T13:00:00Z')
        self.assertEqual(self.events(t), [])
        self.assertIsNone(self.m.resume()['paused_until'])
        self.assertEqual(len(self.events(t)), 1)

    def test_a_finished_or_damaged_pause_is_no_pause(self):
        settings.set_value('notify_paused_until', '2026-11-08T11:00:00Z')
        self.assertIsNone(menubar.pause_until())
        settings.set_value('notify_paused_until', 'nonsense')
        self.assertIsNone(menubar.pause_until())


class PanelTest(Case):
    def test_it_counts_what_needs_you_and_puts_those_teams_first(self):
        stuck = {'id': 'f2', 'text': 't', 'buttons': [{'id': 'n', 'label': 'Try another way', 'kind': 'action', 'send': 's'}]}
        p = self.m._panel([team(pane('x', 'working'), name='quiet', tokens=1000),
                           team(pane(state='asking', fix=FIX), pane('b', 'stuck', fix=stuck), tokens=2000)], ST)
        self.m.brain = None
        self.assertEqual(p['count'], 2)
        self.assertEqual([(t['name'], t['need']) for t in p['teams']], [('shop', 2), ('quiet', 0)])
        asks, is_stuck = p['teams'][0]['items']
        self.assertEqual(asks['text'], 'Behind a flag, or to main?')
        self.assertEqual([b['label'] for b in asks['buttons']], ['Behind a flag', 'To main', 'Reply'])
        self.assertNotIn('send', asks['buttons'][0])  # what's sent stays in the brain
        self.assertEqual((is_stuck['says'], is_stuck['since_s']), ('seems stuck', 5))
        self.assertEqual((p['spend_tokens'], p['auto_fixes']), (3000, 1))

    def setUp(self):
        super().setUp()
        self.m.brain = mock.Mock()
        self.m.brain.today.ready = True


class FallbackTest(Case):
    def test_the_brain_stops_announcing_while_the_app_polls(self):
        self.assertFalse(self.m.app_watching(NOW))
        self.m.polled = NOW
        self.assertTrue(self.m.app_watching(NOW + dt.timedelta(seconds=30)))
        self.assertFalse(self.m.app_watching(NOW + dt.timedelta(seconds=90)))

    def test_watch_once_stays_quiet_when_told_or_paused(self):
        with mock.patch.object(live.status, 'collect', return_value=[]), \
             mock.patch.object(live.status, 'running_cwds', return_value=set()), \
             mock.patch.object(live.status, 'herdr_terminals', return_value=[]), \
             mock.patch.object(live, 'announce', return_value=[('t', 'b')]) as said, \
             mock.patch.object(live.quiet, 'deliver', return_value=[]):
            self.assertEqual(live.watch_once('/c', '/x', announcing=False), [])
            settings.set_value('notify_paused_until', '2026-11-08T13:00:00Z')
            self.assertEqual(live.watch_once('/c', '/x'), [])
            said.assert_not_called()
            settings.set_value('notify_paused_until', None)
            self.assertEqual(len(live.watch_once('/c', '/x')), 1)


class SettingsTest(Case):
    def test_only_a_string_or_nothing_is_kept(self):
        with open(self.settings_file, 'w') as f:
            json.dump({'notify_paused_until': 5}, f)
        self.assertIsNone(settings.load()['notify_paused_until'])
        with self.assertRaises(ValueError):
            settings.set_value('notify_paused_until', 5)
