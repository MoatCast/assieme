"""Runtime client for Assieme's core: a fake core over a Unix socket, plus a real asm when built."""
import json
import os
import shutil
import socketserver
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from assieme import runtime
from tests.asm_bin import ASM_DIR


class CleanEnvTest(unittest.TestCase):
    def test_drops_agent_markers(self):
        env = {'PATH': '/bin', 'CLAUDECODE': '1', 'CLAUDE_CODE_CHILD_SESSION': 'x', 'CLAUDE_CODE_SESSION_ID': 's',
               'CLAUDE_CODE_ENTRYPOINT': 'e', 'CODEX_SANDBOX': 'seatbelt', 'HOME': '/h'}
        self.assertEqual(runtime.clean_env(env), {'PATH': '/bin', 'HOME': '/h'})

    def test_keeps_agent_configuration(self):
        env = {'CODEX_HOME': '/c', 'CLAUDE_CODE_USE_BEDROCK': '1', 'CLAUDE_CODE_OAUTH_TOKEN': 'tok'}
        self.assertEqual(runtime.clean_env(env), env)


class SocketPathTest(unittest.TestCase):
    def test_default_socket_is_asm(self):
        with mock.patch.dict(os.environ, {'XDG_STATE_HOME': '/s'}):
            os.environ.pop('ASSIEME_ASM_SOCKET', None)
            self.assertEqual(runtime.socket_path(), '/s/assieme/asm.sock')

    def test_a_remote_brain_finds_its_core_by_asm_socket(self):
        with mock.patch.dict(os.environ, {'ASSIEME_ASM_SOCKET': '/r/core.sock'}):
            self.assertEqual(runtime.socket_path(), '/r/core.sock')

    def test_honours_xdg_state_home(self):
        with mock.patch.dict(os.environ, {'XDG_STATE_HOME': '/s'}):
            os.environ.pop('ASSIEME_ASM_SOCKET', None)
            self.assertEqual(runtime.socket_path(), '/s/assieme/asm.sock')


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            req = json.loads(line)
            self.server.requests.append(req)
            m, i = req['method'], req['id']
            if m == 'boom':
                msg = {'id': i, 'error': {'code': -32000, 'message': 'nope', 'data': {'kind': 'not_found', 'hint': 'asm ls'}}}
            else:
                msg = {'id': i, 'result': {'terminal.run': {'address': 't9'}, 'terminal.read': {'text': 'hi'}}.get(m, {})}
            self.wfile.write((json.dumps(msg) + '\n').encode())


class FakeCoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir='/tmp')
        self.sock = os.path.join(self.dir, 'asm.sock')
        self.server = socketserver.ThreadingUnixStreamServer(self.sock, _Handler)
        self.server.requests = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.rt = runtime.Runtime(sock=self.sock)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); shutil.rmtree(self.dir, ignore_errors=True)

    def test_start_returns_address_and_sends_labels_and_no_wait(self):
        t = self.rt.start(['sh'], cwd='/w', labels={'assieme.team': 'demo'}, parent='t1')
        self.assertEqual(t, 't9')
        p = self.server.requests[-1]['params']
        self.assertIs(p['outlive_parent'], True)  # merging a parent must not end the agents reporting to it
        self.assertEqual((p['argv'], p['cwd'], p['labels'], p['wait'], p['parent']), (['sh'], '/w', {'assieme.team': 'demo'}, False, 't1'))

    def test_label_sets_labels(self):
        self.rt.label('t9', {'assieme.briefed': '1'})
        self.assertEqual(self.server.requests[-1]['params'], {'terminal': 't9', 'labels': {'assieme.briefed': '1'}})

    def test_read_returns_text(self):
        self.assertEqual(self.rt.read('t9'), 'hi')

    def test_error_carries_kind(self):
        with self.assertRaises(runtime.RuntimeError_) as e:
            self.rt.call('boom')
        self.assertEqual((e.exception.kind, e.exception.hint), ('not_found', 'asm ls'))


class EnsureCoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir='/tmp')
        self.sock = os.path.join(self.dir, 'asm.sock')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_runtime_starts_asm_server(self):
        with mock.patch.dict(os.environ, {'HC_SESSION': 'theirs', 'HC_SOCKET': '/h.sock', 'PATH': os.environ['PATH']}), \
             mock.patch('shutil.which', return_value='/bin/asm'), \
             mock.patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            runtime.Runtime(sock=self.sock).ensure_core()
        env = run.call_args.kwargs['env']
        self.assertEqual(run.call_args.args[0], ['asm', 'server', 'start'])
        self.assertEqual((env['ASM_SESSION'], env['ASM_SOCKET']), ('assieme', self.sock))
        self.assertEqual([k for k in env if k.startswith('HC_')], [])

    def test_missing_socket_starts_core_with_clean_env(self):
        with mock.patch.dict(os.environ, {'CLAUDECODE': '1', 'PATH': os.environ['PATH']}), \
             mock.patch('shutil.which', return_value='/bin/asm'), \
             mock.patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            runtime.Runtime(sock=self.sock).ensure_core()
        env = run.call_args.kwargs['env']
        self.assertEqual((env['ASM_SESSION'], env['ASM_SOCKET']), ('assieme', self.sock))
        self.assertNotIn('CLAUDECODE', env)

    def test_stale_socket_file_is_removed_before_starting(self):
        open(self.sock, 'w').close()  # a leftover file nobody listens on
        with mock.patch('shutil.which', return_value='/bin/asm'), \
             mock.patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            runtime.Runtime(sock=self.sock).ensure_core()
        self.assertFalse(os.path.exists(self.sock))

    def test_asm_not_installed(self):
        with mock.patch('shutil.which', return_value=None):
            with self.assertRaises(runtime.RuntimeError_) as e:
                runtime.Runtime(sock=self.sock).ensure_core()
        self.assertEqual(e.exception.kind, 'asm_missing')
        self.assertIn('reinstall Assieme', e.exception.hint)


@unittest.skipUnless(ASM_DIR, 'no asm: build runtime/ first')
class RealCoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir='/tmp')
        path = mock.patch.dict(os.environ, {'PATH': ASM_DIR + os.pathsep + os.environ['PATH']})
        path.start()
        self.addCleanup(path.stop)
        self.rt = runtime.Runtime(session='assieme-test', sock=os.path.join(self.dir, 'asm.sock'))

    def tearDown(self):
        env = runtime.clean_env(os.environ) | {'ASM_SESSION': 'assieme-test', 'ASM_SOCKET': self.rt.sock}
        subprocess.run(['asm', 'server', 'stop'], env=env, capture_output=True)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_shell_program_runs_and_is_listed_by_label(self):
        t = self.rt.start(['sh', '-c', 'echo ready; sleep 30'], cwd=self.dir, labels={'assieme.team': 'x'})
        self.assertIn(t, [x['address'] for x in self.rt.list({'assieme.team': 'x'})])
        self.rt.label(t, {'assieme.briefed': '1'})
        self.assertEqual([x['address'] for x in self.rt.list({'assieme.briefed': '1'})], [t])
        self.rt.kill(t)
