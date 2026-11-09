"""The MCP server: reports on panes, the brain's mcp.* methods, the two faces and asking first, connect."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from mcp import Client
from mcp.types import ElicitResult

from assieme import connect, mcpserver, mcptools, newteam, panes, quiet, reports, settings, team

NOW_MS = 1_000_000_000_000


def row(role, address, cwd=None, briefed=True, status='idle', state='live', status_ms=0, created=0, turn=None):
    labels = {team.TEAM: 'shop', team.ROLE: role}
    if briefed:
        labels[team.BRIEFED] = '1'
    return {'address': address, 'state': state, 'status': {'state': status}, 'status_ms': status_ms, 'created_ms': created, 'turn': turn,
            'cwd': cwd or f'/wt/{role}', 'argv': ['claude'], 'labels': labels}


class FakeRt:
    session, sock = 'assieme', '/s'

    def __init__(self, rows):
        self.rows, self.sent, self.killed = rows, [], []

    def get(self, t):
        return next(r for r in self.rows if r['address'] == t)

    def send(self, t, text):
        self.sent.append((t, text))

    def kill(self, t):
        self.killed.append(t)

    def read(self, t, lines=0):
        return 'one\ntwo\nthree\n'


class FakeBrain:
    def __init__(self, rows, built=None):
        self.rt, self.built, self.acted = FakeRt(rows), built, []

    def _assieme_rows(self, name=None):
        return [r for r in self.rt.rows if not name or r['labels'][team.TEAM] == name]

    def map(self, name):
        return self.built

    def _resolve(self, name, now):
        return None, None, None, self.rt

    def act(self, name, role, fix, button, by='you'):
        self.acted.append((role, fix, button, by))
        return {'ok': True, 'detail': 'sent'}


class State(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name
        p = mock.patch.dict(os.environ, {'XDG_STATE_HOME': d.name})
        p.start()
        self.addCleanup(p.stop)


class BrainStaysStandardLibrary(unittest.TestCase):
    def test_the_brain_doesnt_load_the_sdk(self):
        code = "import sys, assieme.cli; sys.exit(any(m == 'mcp' or m.startswith(('mcp.', 'pydantic')) for m in sys.modules))"
        self.assertEqual(subprocess.run([sys.executable, '-c', code]).returncode, 0)


class ReportsTest(State):
    def test_a_report_holds_until_the_next_turn(self):
        reports.save('shop', 'b', 'stuck', 'no key', now=NOW_MS / 1000, turn=3)
        self.assertEqual(reports.current('shop', 'b', row('b', 't1', turn=3))['note'], 'no key')
        self.assertIsNone(reports.current('shop', 'b', row('b', 't1', turn=4)))
        self.assertIsNone(reports.current('shop', 'b', row('b', 't1', turn=3, status='working')), 'hc counts a turn at its end')
        self.assertIsNone(reports.current('shop', 'b', row('b', 't2', created=NOW_MS + 1)), 'a restarted agent')

    def test_a_teammates_note_doesnt_clear_it(self):
        reports.save('shop', 'b', 'need_decision', 'Which?', now=NOW_MS / 1000, turn=3)
        reports.noted('shop', 'b')
        self.assertTrue(reports.current('shop', 'b', row('b', 't1', turn=4)))
        self.assertIsNone(reports.current('shop', 'b', row('b', 't1', turn=5)), 'but the next turn does')

    def test_a_report_that_ended_is_gone_from_the_file(self):
        reports.save('shop', 'b', 'stuck', 'SECRET-NOTE', now=NOW_MS / 1000, turn=3)
        reports.current('shop', 'b', row('b', 't1', turn=4))
        with open(reports._file()) as f:
            self.assertNotIn('SECRET-NOTE', f.read())
        reports.save('shop', 'b', 'stuck', 'SECRET-NOTE', now=NOW_MS / 1000, turn=3)
        self.view(state='finished')
        with open(reports._file()) as f:
            self.assertNotIn('SECRET-NOTE', f.read(), 'an agent that stopped')

    def test_a_damaged_file_means_no_report(self):
        reports.save('shop', 'b', 'stuck', 'x')
        with open(reports._file(), 'w') as f:
            f.write('[1]')
        self.assertIsNone(reports.current('shop', 'b', row('b', 't1')))
        with open(reports._file(), 'w') as f:
            json.dump({'shop/b': 'odd'}, f)
        self.assertIsNone(reports.current('shop', 'b', row('b', 't1')))

    def test_done_clears_and_a_note_is_required(self):
        reports.save('shop', 'b', 'stuck', 'x')
        reports.save('shop', 'b', 'done')
        self.assertIsNone(reports.current('shop', 'b', row('b', 't1')))
        with self.assertRaises(ValueError):
            reports.save('shop', 'b', 'need_decision', ' ')
        with self.assertRaises(ValueError):
            reports.save('shop', 'b', 'nonsense', 'x')

    def view(self, **kw):
        import datetime as dt
        now = dt.datetime.fromtimestamp(NOW_MS / 1000 + 60, dt.timezone.utc)
        class No:
            def get(self, *a):
                return None
        return panes.view('shop', [row('b', 't1', **kw)], [], now, lambda a: 'screen', No())

    def test_a_decision_shows_as_asking_with_reply(self):
        reports.save('shop', 'b', 'need_decision', 'Which database?', now=NOW_MS / 1000)
        p = self.view()['panes'][0]
        self.assertEqual((p['state'], p['fix']['text'], p['since_s']), ('asking', 'Which database?', 60))
        self.assertEqual([b['kind'] for b in p['fix']['buttons']], ['reply'])

    def test_stuck_shows_the_agents_words(self):
        reports.save('shop', 'b', 'stuck', 'No API key', now=NOW_MS / 1000)
        v = self.view()
        self.assertEqual((v['panes'][0]['state'], v['panes'][0]['detail']), ('stuck', 'No API key'))
        self.assertEqual(v['strip'][0]['text'], 'b is stuck: No API key')

    def test_a_real_prompt_wins(self):
        reports.save('shop', 'b', 'stuck', 'x', now=NOW_MS / 1000)
        self.assertEqual(self.view(status='blocked')['panes'][0]['detail'], 'Waiting for you')


def button(id_, kind, label, send=None, action=None):
    return {'id': id_, 'kind': kind, 'label': label, 'send': send, **({'action': action} if action else {})}


def pane(role='b', state='asking', buttons=(), text='Which one?', **kw):
    return {'role': role, 'agent': 'claude', 'address': 't1', 'state': state, 'since_s': 3, 'detail': '', 'reachable': True,
            'fix': {'id': 'f1', 'text': text, 'buttons': list(buttons)}, 'memory_full': False, **kw}


class ToolsTest(State):
    def tools(self, rows=(), built=None, starter=None, **kw):
        self.brain = FakeBrain(list(rows), built)
        self.teams = os.path.join(self.dir, 'teams')
        self.notes = []
        return mcptools.Tools(self.brain, starter, self.teams, opener=lambda: True,
                              notify=lambda *a: self.notes.append(a), **kw)

    def test_a_message_carries_the_marker_the_map_reads(self):
        from assieme.adapters import wake_sender
        t = self.tools([row('a', 't1'), row('b', 't2')])
        self.assertEqual(t.message('t1', 'b', 'done?')['sent'], 'now')
        [(to, text)] = self.brain.rt.sent
        self.assertEqual((to, wake_sender(text)), ('t2', ('a', 'hc')))

    def test_a_message_names_teammates_when_wrong_and_waits_for_a_booting_agent(self):
        t = self.tools([row('a', 't1'), row('b', 't2'), row('c', 't3', briefed=False)])
        with self.assertRaisesRegex(ValueError, 'Your teammates: b, c'):
            t.message('t1', 'zed', 'hi')
        with self.assertRaisesRegex(ValueError, 'still starting'):
            t.message('t1', 'c', 'hi')
        self.assertEqual(self.brain.rt.sent, [])

    def test_a_quiet_pair_holds_routine_notes_but_not_questions(self):
        t = self.tools([row('a', 't1', cwd=self.dir), row('b', 't2', cwd=os.path.join(self.dir, 'teams'))])
        os.makedirs(os.path.join(self.dir, 'teams'), exist_ok=True)
        quiet.set_pair(self.dir, self.teams, 'a to b')
        quiet.heartbeat()
        self.assertEqual(t.message('t1', 'b', 'tests pass')['sent'], 'held')
        self.assertEqual(self.brain.rt.sent, [])
        self.assertEqual(list(quiet.held().values())[0][0]['from'], 'a')
        self.assertEqual(t.message('t1', 'b', 'which one?')['sent'], 'now')

    def test_only_a_team_agent_has_an_identity(self):
        bare = row('x', 't9')
        bare['labels'] = {}
        with self.assertRaises(ValueError):
            self.tools([bare]).report('t9', 'stuck', 'x')

    def test_a_decision_notifies_unless_switched_off(self):
        t = self.tools([row('a', 't1')])
        t.report('t1', 'need_decision', 'Which?')
        self.assertEqual(self.notes, [('a needs a decision', 'Which?')])
        settings.set_value('notify.needs_you', False)
        t.report('t1', 'need_decision', 'Again?')
        self.assertEqual(len(self.notes), 1)

    def test_answer_picks_a_menu_choice_and_refuses_others(self):
        built = {'panes': [pane(buttons=[button('b1', 'menu', 'Yes', '1'), button('b2', 'menu', 'No', '2'), button('r', 'reply', 'Reply')])]}
        t = self.tools([row('b', 't1')], built)
        self.assertTrue(t.answer('shop', 'b', 'yes')['ok'])
        self.assertEqual(self.brain.acted, [('b', 'f1', 'b1', 'you')])
        self.assertIn('Yes, No', t.answer('shop', 'b', 'maybe')['detail'])

    def test_answer_in_words_when_no_menu_and_refuses_when_not_asking(self):
        built = {'panes': [pane(buttons=[button('r', 'reply', 'Reply')])]}
        t = self.tools([row('b', 't1')], built)
        self.assertTrue(t.answer('shop', 'b', 'Use Postgres')['ok'])
        self.assertEqual(self.brain.rt.sent, [('t1', 'Use Postgres')])
        built['panes'][0]['state'] = 'working'
        self.assertFalse(t.answer('shop', 'b', 'x')['ok'])

    def test_fixes_follow_the_setting(self):
        nudge = button('n', 'action', 'Nudge', 'try', 'nudge')
        built = {'found': False, 'panes': [pane(state='stuck', buttons=[nudge])]}
        t = self.tools([row('b', 't1')], built)
        self.assertFalse(t.fix_check('shop', 'b', 'nudge')['auto'])
        settings.set_value('fixes.nudge', 'auto')
        self.assertTrue(t.fix_check('shop', 'b', 'nudge')['auto'])
        built['found'] = True
        self.assertFalse(t.fix_check('shop', 'b', 'nudge')['auto'], 'teams found outside Assieme always ask')
        self.assertFalse(t.fix_check('shop', 'b', 'compact')['available'], 'only what the app offers now')

    def test_compact_runs_alone_only_when_memory_is_full(self):
        built = {'found': False, 'panes': [pane(state='idle', buttons=[button('c', 'action', 'Compact', '/compact', 'compact')])]}
        t = self.tools([row('b', 't1')], built)
        settings.set_value('fixes.compact', 'auto')
        self.assertFalse(t.fix_check('shop', 'b', 'compact')['auto'])
        built['panes'][0]['memory_full'] = True
        self.assertTrue(t.fix_check('shop', 'b', 'compact')['auto'])

    def test_stop_agent_only_stops_a_live_agent_of_that_team(self):
        t = self.tools([row('b', 't1'), row('c', 't2', state='finished')])
        with self.assertRaises(ValueError):
            t.stop_agent('shop', 'c')
        self.assertTrue(t.stop_agent('shop', 'b')['ok'])
        self.assertEqual(self.brain.rt.killed, ['t1'])

    def repo(self):
        p = os.path.join(self.dir, 'repo')
        os.makedirs(p)
        subprocess.run(['git', 'init', '-q', p], check=True)
        subprocess.run(['git', '-C', p, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'first'], check=True)
        return p

    def test_a_draft_fills_in_its_kind_and_keeps_the_practices_on(self):
        t = self.tools()
        got = t.draft_save(self.repo(), [{'name': 'b', 'agent': 'claude', 'kind': 'builder'},
                                         {'name': 'r', 'agent': 'codex', 'kind': 'reviewer', 'parent': 'b'}], task='fix', why='small')
        self.assertTrue(got['opened'])
        self.assertEqual(t.draft_next(), got['id'])
        d = t.draft_take(got['id'])
        self.assertEqual(d['roles'][1]['checkout'], 'read-only')
        self.assertEqual(d['roles'][0]['brief'], newteam.kind_brief('builder'))
        self.assertEqual(d['practices'], {})  # New team treats an unlisted practice as on
        self.assertEqual((t.draft_next(), t.draft_take(got['id'])), (None, None))

    def test_a_bad_draft_is_refused_and_starts_nothing(self):
        t = self.tools()
        with self.assertRaises(newteam.NewTeamError):
            t.draft_save(os.path.join(self.dir, 'nowhere'), [{'name': 'b', 'agent': 'claude', 'kind': 'builder'}])
        with self.assertRaises(newteam.NewTeamError):
            t.draft_save(self.repo(), [{'name': 'Bad Name', 'agent': 'claude', 'kind': 'builder'}])
        with self.assertRaisesRegex(ValueError, 'Unknown practice'):
            t.draft_save(os.path.join(self.dir, 'repo'), [{'name': 'b', 'agent': 'claude', 'kind': 'builder'}], practices={'made-up': False})
        self.assertIsNone(t.draft_next())

    def test_a_folder_that_needs_setting_up_is_refused(self):
        plain = os.path.join(self.dir, 'plain')
        os.makedirs(plain)
        with self.assertRaisesRegex(newteam.NewTeamError, 'no saved version'):
            self.tools().draft_save(plain, [{'name': 'b', 'agent': 'claude', 'kind': 'builder'}])
        self.assertFalse(os.path.exists(os.path.join(plain, '.git')))

    def test_a_damaged_draft_doesnt_block_the_next(self):
        t = self.tools()
        good = t.draft_save(self.repo(), [{'name': 'b', 'agent': 'claude', 'kind': 'builder'}])['id']
        for name, body in (('bad1', '{not json'), ('bad2', '[1, 2]')):
            path = os.path.join(t._drafts(), f'{name}.json')
            with open(path, 'w') as f:
                f.write(body)
            os.utime(path, (1, 1))  # the oldest, so it comes first
            self.assertEqual(t.draft_next(), name)
            self.assertIsNone(t.draft_take(name))
            self.assertTrue(os.path.exists(path + '.damaged'))  # kept to look at, no longer offered
        self.assertEqual(t.draft_next(), good)

    def test_a_draft_taken_while_listing_is_not_an_error(self):
        t = self.tools()
        got = t.draft_save(self.repo(), [{'name': 'b', 'agent': 'claude', 'kind': 'builder'}])['id']
        real = os.path.getmtime
        def vanished(path):
            if path.endswith(f'{got}.json'):
                raise FileNotFoundError(path)
            return real(path)
        with mock.patch.object(os.path, 'getmtime', vanished):
            self.assertIsNone(t.draft_next())

    def test_take_cant_leave_the_drafts_folder(self):
        secret = os.path.join(self.dir, 'assieme', 'settings.json')
        settings.set_value('show_commands', True)
        self.assertIsNone(self.tools().draft_take('../settings'))
        self.assertTrue(os.path.exists(secret))


def text(result):
    return result.content[0].text


class ServerTest(State):
    """Through the SDK's real client, with the brain socket answered by `calls`."""

    def setUp(self):
        super().setUp()
        self.calls = []

        def call(method, **params):
            self.calls.append((method, params))
            if method == 'mcp.fix_check':
                return {'available': True, 'auto': self.auto, 'text': 'Seems stuck.'}
            return {'ok': True}
        self.auto = False
        p = mock.patch.object(mcpserver, 'call', call)
        p.start()
        self.addCleanup(p.stop)

    def run_with(self, server, body, elicit=None, mode='legacy'):
        async def go():
            async with Client(server, elicitation_callback=elicit, mode=mode) as c:
                return await body(c)
        return asyncio.run(go())

    def tool_names(self, server):
        async def body(c):
            return {t.name for t in (await c.list_tools()).tools}
        return self.run_with(server, body)

    def test_the_face_comes_from_the_terminal(self):
        self.assertEqual(mcpserver.agent_address({'HC_SESSION': 'assieme', 'HC_TERMINAL_ID': 't3'}), 't3')
        self.assertIsNone(mcpserver.agent_address({'HC_SESSION': 'default', 'HC_TERMINAL_ID': 't3'}))
        self.assertIsNone(mcpserver.agent_address({}))

    def test_an_agent_gets_no_control_tools(self):
        self.assertEqual(self.tool_names(mcpserver.agent_server('t1')), {'list_teammates', 'message_agent', 'report'})

    def test_the_control_face_has_every_roadmap_tool(self):
        self.assertEqual(self.tool_names(mcpserver.control_server()), {
            'list_teams', 'team_status', 'what_needs_you', 'read_terminal', 'recent_activity', 'answer', 'send_message',
            'nudge', 'compact', 'undo', 'start_team', 'add_agent', 'stop_agent', 'stop_team', 'draft_team'})

    MODES = ('legacy', 'auto')  # the classic protocol, and 2026-07-28 which Claude Code 2.1.319 speaks

    def act(self, name, args, elicit, mode='legacy'):
        async def body(c):
            return await c.call_tool(name, args)
        return self.run_with(mcpserver.control_server(), body, elicit, mode)

    def yes(self, seen=None):
        async def elicit(ctx, params):
            if seen is not None:
                seen.append(params.message)
            return ElicitResult(action='accept', content={'confirm': True})
        return elicit

    ACTIONS = [('send_message', {'team': 's', 'agent': 'b', 'text': 'hi'}, 'mcp.send'),
               ('answer', {'team': 's', 'agent': 'b', 'answer': 'yes'}, 'mcp.answer'),
               ('stop_agent', {'team': 's', 'agent': 'b'}, 'mcp.stop_agent'),
               ('stop_team', {'team': 's'}, 'team.stop'),
               ('start_team', {'team': 's'}, 'mcp.start_saved'),
               ('add_agent', {'team': 's', 'name': 'n', 'agent': 'claude', 'kind': 'builder'}, 'team.add_agent')]

    def test_every_action_runs_on_the_persons_yes(self):
        for mode in self.MODES:
            for tool, args, method in self.ACTIONS:
                with self.subTest(tool=tool, mode=mode):
                    seen = []
                    self.calls.clear()
                    self.act(tool, args, self.yes(seen), mode)
                    self.assertEqual(len(seen), 1)
                    self.assertEqual([m for m, _ in self.calls], [method])

    def test_every_action_stops_on_no(self):
        async def no(ctx, params):
            return ElicitResult(action='decline')
        for mode in self.MODES:
            for tool, args, _ in self.ACTIONS:
                with self.subTest(tool=tool, mode=mode):
                    self.calls.clear()
                    self.assertEqual(text(self.act(tool, args, no, mode)), mcpserver.NOT_DONE)
                    self.assertEqual(self.calls, [])

    def test_a_client_that_cant_ask_gets_not_done(self):
        for mode in self.MODES:
            for tool, args, _ in self.ACTIONS:
                with self.subTest(tool=tool, mode=mode):
                    self.calls.clear()
                    self.assertIn('Not done', text(self.act(tool, args, None, mode)))
                    self.assertEqual(self.calls, [])

    def test_an_answer_counts_only_for_its_own_question(self):
        class Ctx:
            protocol_version, client_capabilities = '2026-07-28', mock.Mock(elicitation=True)
            input_responses = {'yes': ElicitResult(action='accept', content={'confirm': True})}
            request_state = mcpserver._question_id('Stop b?')
        got = asyncio.run(mcpserver._with_yes(Ctx(), 'Stop everything?', 'team.stop'))
        self.assertEqual(self.calls, [])
        self.assertEqual(got.input_requests['yes'].params.message, 'Stop everything?')

    def test_a_fix_on_automatic_doesnt_ask_and_on_ask_first_does(self):
        self.auto = True
        self.act('nudge', {'team': 's', 'agent': 'b'}, None)
        self.assertEqual(self.calls[-1], ('mcp.fix', {'team': 's', 'role': 'b', 'action': 'nudge', 'by': 'auto'}))
        self.auto = False
        self.calls.clear()
        self.assertIn('Automatic in Settings', text(self.act('nudge', {'team': 's', 'agent': 'b'}, None)))
        self.assertEqual([m for m, _ in self.calls], ['mcp.fix_check'])
        self.act('nudge', {'team': 's', 'agent': 'b'}, self.yes(), 'auto')
        self.assertEqual(self.calls[-1][1]['by'], 'you')

    def test_a_draft_needs_no_question_and_says_who_starts_it(self):
        with mock.patch.object(mcpserver, 'call', return_value={'id': 'x', 'opened': True}) as call:
            got = text(self.act('draft_team', {'project': '/p', 'roles': [{'name': 'b'}]}, None))
        self.assertIn('clicks Start', got)
        self.assertEqual(call.call_args.args, ('mcp.draft_save',))

    def test_the_know_how_is_all_there(self):
        async def body(c):
            prompt = await c.get_prompt('design_my_team', {'project': '/p'})
            res = {str(r.uri) for r in (await c.list_resources()).resources}
            bodies = {u: (await c.read_resource(u)).contents[0].text for u in res}
            bodies['b'] = (await c.read_resource('assieme://briefs/builder')).contents[0].text
            return prompt, c.instructions if hasattr(c, 'instructions') else None, bodies
        prompt, instructions, bodies = self.run_with(mcpserver.control_server(), body)
        self.assertIn('draft_team', prompt.messages[0].content.text)
        self.assertEqual(set(bodies) - {'b'}, {'assieme://practices', 'assieme://role-templates', 'assieme://example-teams', 'assieme://numbers'})
        self.assertEqual(json.loads(bodies['assieme://practices'])['groups'][0]['id'], 'messages')
        self.assertIn('74%', bodies['assieme://numbers'])
        self.assertTrue(bodies['b'].strip())
        self.assertIn('Start small', instructions)


class NoBrainTest(State):
    def test_no_brain_is_said_in_plain_words(self):
        async def go():
            async with Client(mcpserver.control_server(), mode='legacy') as c:
                return await c.call_tool('list_teams', {})
        got = asyncio.run(go())
        self.assertTrue(got.is_error)
        self.assertIn("isn't running", text(got))


class Done:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class ConnectTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.home = d.name
        self.cfg = connect.desktop_config(self.home)
        self.have, self.ran, self.app_open = set(), [], False
        self.which = lambda c: c if c in ('claude', 'codex') else None

    def run_(self, args, **kw):
        self.ran.append(args)
        if args[0] == 'pgrep':
            return Done(0 if self.app_open else 1)
        client, verb = args[0], args[2]
        if verb == 'get':
            return Done(0 if client in self.have else 1)
        (self.have.add if verb == 'add' else self.have.discard)(client)
        return Done()

    def go(self, fn):
        return fn(run=self.run_, which=self.which, home=self.home)

    def test_disconnect_with_no_claude_app_settings_file_is_fine(self):
        os.makedirs(os.path.dirname(self.cfg))
        self.assertFalse(os.path.exists(self.cfg))
        self.assertEqual(self.go(connect.disconnect)['problems'], [])

    def test_disconnect_still_reports_a_damaged_settings_file(self):
        os.makedirs(os.path.dirname(self.cfg))
        with open(self.cfg, 'w') as f:
            f.write('{not json')
        self.assertEqual(len(self.go(connect.disconnect)['problems']), 1)

    def test_connect_adds_to_each_client_and_disconnect_removes(self):
        got = self.go(connect.connect)
        self.assertEqual([c['connected'] for c in got['clients']], [True, True])
        add = next(a for a in self.ran if a[:3] == ['claude', 'mcp', 'add'])
        self.assertEqual(add[3:6], ['--scope', 'user', 'assieme'])
        self.assertEqual(add[add.index('--') + 1:], connect.command())
        self.assertEqual([c['connected'] for c in self.go(connect.disconnect)['clients']], [False, False])

    def test_a_second_connect_replaces_an_old_path(self):
        self.go(connect.connect)
        self.ran.clear()
        self.go(connect.connect)
        self.assertIn(['codex', 'mcp', 'remove', 'assieme'], self.ran)

    def test_only_installed_clients(self):
        self.which = lambda c: None
        self.assertEqual(self.go(connect.connect), {'clients': [], 'problems': []})

    def test_the_claude_app_keeps_its_other_settings(self):
        os.makedirs(os.path.dirname(self.cfg))
        with open(self.cfg, 'w') as f:
            json.dump({'theme': 'dark', 'mcpServers': {'other': {'command': 'x'}}}, f)
        got = self.go(connect.connect)
        self.assertEqual(got['clients'][-1], {'id': 'desktop', 'label': 'the Claude app', 'connected': True})
        with open(self.cfg) as f:
            data = json.load(f)
        self.assertEqual((data['theme'], data['mcpServers']['other']), ('dark', {'command': 'x'}))
        self.assertEqual(data['mcpServers']['assieme']['args'], connect.command()[1:])
        self.go(connect.disconnect)
        with open(self.cfg) as f:
            self.assertEqual(json.load(f), {'theme': 'dark', 'mcpServers': {'other': {'command': 'x'}}})

    def test_the_claude_app_isnt_written_while_it_is_open(self):
        os.makedirs(os.path.dirname(self.cfg))
        with open(self.cfg, 'w') as f:
            json.dump({'theme': 'dark'}, f)
        self.app_open = True
        got = self.go(connect.connect)
        self.assertEqual(got['problems'], [])
        self.assertEqual(got['clients'][-1], {'id': 'desktop', 'label': 'the Claude app', 'connected': False, 'blocked': True})
        with open(self.cfg) as f:
            self.assertEqual(json.load(f), {'theme': 'dark'})
        self.app_open = False
        got = self.go(connect.connect)
        self.assertEqual(got['clients'][-1], {'id': 'desktop', 'label': 'the Claude app', 'connected': True})

    def test_a_damaged_config_is_left_alone_and_reported(self):
        os.makedirs(os.path.dirname(self.cfg))
        with open(self.cfg, 'w') as f:
            f.write('{not json')
        got = self.go(connect.connect)
        self.assertIn('the Claude app', got['problems'][0])
        with open(self.cfg) as f:
            self.assertEqual(f.read(), '{not json')
        self.assertEqual([c['connected'] for c in got['clients']], [True, True, False], 'the others still connect')

    def test_a_failing_client_is_reported_and_the_rest_go_on(self):
        real = self.run_

        def run(args, **kw):
            if args[:3] == ['claude', 'mcp', 'add']:
                return Done(1, stderr='nope')
            return real(args, **kw)
        got = connect.connect(run=run, which=self.which, home=self.home)
        self.assertEqual(got['problems'], ['Claude Code: nope'])
        self.assertEqual([c['connected'] for c in got['clients']], [False, True])
