"""The brain service: snapshot, request handling and a private Unix socket."""
import datetime as dt
import json
import os
import re
import shutil
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from assieme import live, runtime, serve
from assieme.status import Agent

NOW = dt.datetime(2026, 11, 7, 12, 0, tzinfo=dt.timezone.utc)


class FakeRt:
    def __init__(self, rows=(), fail=False):
        self.rows, self.fail = list(rows), fail

    def list(self, labels, all=False):
        if self.fail:
            raise runtime.RuntimeError_('unavailable', 'no core')
        return self.rows


def agent(name, cwd):
    a = Agent(name, 'claude', f'/logs/{name}.jsonl')
    a.cwd, a.last = cwd, NOW
    return a


class WatchOnceTest(unittest.TestCase):
    def test_watch_once_announces_and_delivers(self):
        with mock.patch.object(live.status, 'collect', return_value=[]), \
             mock.patch.object(live.status, 'running_cwds', return_value=set()), \
             mock.patch.object(live.status, 'herdr_terminals', return_value=[]), \
             mock.patch.object(live, 'announce', return_value=[('builder waits', 'on you')]), \
             mock.patch.object(live.quiet, 'deliver', return_value=[('coord', 2, 'idle')]):
            lines = live.watch_once('/c', '/x', 12, True)
        self.assertEqual(len(lines), 2)
        self.assertIn('builder waits: on you', lines[0])
        self.assertIn('delivered 2 quiet note(s) to coord', lines[1])


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(serve.status, 'collect', return_value=[agent('builder', '/p'), agent('coord', '/p')])
        p.start()
        self.addCleanup(p.stop)
        for name in ('herdr_terminals', 'running_cwds'):
            q = mock.patch.object(serve.status, name, return_value=[] if name == 'herdr_terminals' else set())
            q.start()
            self.addCleanup(q.stop)

    def test_snapshot_shape(self):
        rows = [{'address': 't1', 'state': 'live', 'status': {'state': 'idle'},
                 'labels': {'assieme.team': 'demo', 'assieme.role': 'coordinator'}}]
        s = serve.snapshot('/c', '/x', NOW, FakeRt(rows), which=lambda c: None if c == 'codex' else '/bin/' + c)
        self.assertEqual(s['clis'], {'claude': True, 'codex': False, 'gemini': True, 'opencode': True, 'cursor': True,
                                     'hc': True, 'herdr': True})
        self.assertEqual(set(s), {'at', 'clis', 'agents', 'alerts', 'queue', 'stalls', 'teams', 'starting', 'found_teams', 'first_run_done'})
        self.assertEqual(s['starting'], [])
        self.assertEqual([a['agent'] for a in s['agents']], ['builder', 'coord'])
        self.assertEqual(s['agents'][0]['cwd'], '/p')
        self.assertEqual(s['teams'][0]['role'], 'coordinator')
        json.dumps(s)  # must be JSON-serialisable as is

    def test_snapshot_includes_starting(self):
        s = serve.snapshot('/c', '/x', NOW, FakeRt(), which=lambda c: '/bin/' + c, starting=[{'team': 'x'}])
        self.assertEqual(s['starting'], [{'team': 'x'}])

    def test_snapshot_without_assieme_core(self):
        s = serve.snapshot('/c', '/x', NOW, FakeRt(fail=True), which=lambda c: '/bin/' + c)
        self.assertEqual(s['teams'], [])


class ClientLeftTest(unittest.TestCase):
    def go(self, error):
        class Out:
            def write(self, _):
                raise error
        h = serve._Handler.__new__(serve._Handler)
        h.rfile, h.wfile = [b'{"id": 1, "method": "x"}\n'], Out()
        h.server = mock.Mock(handlers={})
        h.handle()

    def test_a_client_that_hung_up_is_not_an_error(self):
        self.go(BrokenPipeError())
        self.go(ConnectionResetError())

    def test_other_write_errors_still_surface(self):
        with self.assertRaises(OSError):
            self.go(OSError('disk'))


class HandleTest(unittest.TestCase):
    def test_handle_unknown_method(self):
        r = serve.handle({'id': 3, 'method': 'nope'}, {})
        self.assertEqual((r['id'], r['error']['code']), (3, -32601))

    def test_handle_calls_handler_with_params(self):
        r = serve.handle({'id': 4, 'method': 'echo', 'params': {'x': 1}}, {'echo': lambda x: {'got': x}})
        self.assertEqual(r, {'jsonrpc': '2.0', 'id': 4, 'result': {'got': 1}})

    def test_handler_error_is_a_reply_not_a_crash(self):
        def boom():
            raise ValueError('bad')
        r = serve.handle({'id': 5, 'method': 'boom'}, {'boom': boom})
        self.assertEqual(r['error']['message'], 'bad')


class SocketTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir='/tmp')
        self.sock = os.path.join(self.dir, 'brain.sock')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def start(self):
        threading.Thread(target=serve.serve, args=(self.sock, {'ok': lambda: {'ok': True}}), daemon=True).start()
        for _ in range(100):
            if os.path.exists(self.sock) and not os.path.isfile(self.sock):
                break
            time.sleep(0.02)

    def ask(self, line):
        for _ in range(100):  # the file appears at bind, a moment before listen
            s = socket.socket(socket.AF_UNIX)
            try:
                s.connect(self.sock)
                break
            except ConnectionRefusedError:
                s.close()
                time.sleep(0.02)
        with s, s.makefile('rwb') as f:
            f.write(line + b'\n')
            f.flush()
            return json.loads(f.readline())

    def test_socket_round_trip_and_mode(self):
        self.start()
        self.assertEqual(self.ask(b'{"id": 1, "method": "ok"}')['result'], {'ok': True})
        self.assertEqual(os.stat(self.sock).st_mode & 0o777, 0o600)

    def test_stale_socket_file_replaced(self):
        open(self.sock, 'w').close()
        self.start()
        self.assertEqual(self.ask(b'{"id": 2, "method": "ok"}')['result'], {'ok': True})

    def test_bad_json_gets_parse_error(self):
        self.start()
        self.assertEqual(self.ask(b'not json')['error']['code'], -32700)


class SocketPathTest(unittest.TestCase):
    def test_brain_socket_path(self):
        with mock.patch.dict(os.environ, {'XDG_STATE_HOME': '/s'}):
            self.assertEqual(serve.brain_socket_path(), '/s/assieme/brain.sock')


class CliServeTest(unittest.TestCase):
    def test_cli_serve_wires_snapshot_and_watch(self):
        from assieme import cli
        with mock.patch.object(cli.serve, 'serve') as srv, mock.patch.object(cli.serve, 'brain_socket_path', return_value='/s/b.sock'):
            cli.main(['serve'])
        sock, handlers = srv.call_args.args
        self.assertEqual(sock, '/s/b.sock')
        self.assertTrue({'brain.snapshot', 'pane.act', 'project.check', 'team.presets', 'team.start', 'team.suggest', 'team.view', 'team.map',
                         'activity.undo', 'settings.set', 'review.merge', 'review.undo'} <= set(handlers))
        self.assertTrue(callable(srv.call_args.kwargs['tick']))


class HerdrAddressTest(unittest.TestCase):
    def test_apply_herdr_records_terminal_address(self):
        from assieme import status
        a = agent('builder', '/p')
        status._apply_herdr([a], [{'cwd': '/p', 'state': 'live', 'address': 't9', 'status': {'state': 'idle'}}])
        self.assertEqual(a.herdr_address, 't9')

    def test_a_folder_reached_through_a_symlink_is_the_same_agent(self):
        import tempfile
        from assieme import status
        with tempfile.TemporaryDirectory() as d:
            real = os.path.join(os.path.realpath(d), 'proj')
            os.makedirs(real)
            os.symlink(real, os.path.join(d, 'link'))
            a = status.Agent('proj', 'claude-code', f"/logs/{re.sub(r'[^A-Za-z0-9]', '-', real)}/s.jsonl")  # Claude Code's resolved name
            status._apply_herdr([a], [{'cwd': os.path.join(d, 'link'), 'state': 'live', 'address': 't9', 'status': {'state': 'idle'}}])
            self.assertEqual(a.herdr_address, 't9')

    def test_snapshot_passes_herdr_address(self):
        a = agent('builder', '/p')
        a.herdr_address = 't9'
        with mock.patch.object(serve.status, 'collect', return_value=[a]), \
             mock.patch.object(serve.status, 'herdr_terminals', return_value=[]), \
             mock.patch.object(serve.status, 'running_cwds', return_value=set()):
            s = serve.snapshot('/c', '/x', NOW, FakeRt(), which=lambda c: '/bin/' + c)
        self.assertEqual(s['agents'][0]['herdr_address'], 't9')
