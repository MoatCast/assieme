"""Remote machines (step 15): host lists, routing and merges with stubs, and one run through a real hc to a fake
remote: a fake `ssh` first on the core's PATH runs the other side as a second hc session with its own home."""
import base64
import io
import json
import os
import shutil
import signal
import socketserver
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest import mock

from assieme import remote, runtime, team


class SshHostsTest(unittest.TestCase):
    def test_hosts_with_where_they_go_and_no_patterns(self):
        with tempfile.NamedTemporaryFile('w', delete=False) as f:
            f.write('Host gpu-box\n  HostName 10.0.4.12\n  User dev\n  IdentityFile ~/.ssh/k\n'
                    'Host devbox build-01\n  HostName=dev.example.com\n  Port 2222\n  HostName ignored\n'
                    'Host *.internal !x\n  User nobody\nMatch host foo\n  User bar\n# Host commented\n')
        try:
            got = remote.ssh_hosts(f.name)
        finally:
            os.remove(f.name)
        self.assertEqual(got, [
            {'target': 'gpu-box', 'address': 'dev@10.0.4.12', 'from': 'ssh'},
            {'target': 'devbox', 'address': 'dev.example.com:2222', 'from': 'ssh'},
            {'target': 'build-01', 'address': 'dev.example.com:2222', 'from': 'ssh'}])

    def test_no_config_is_no_hosts(self):
        self.assertEqual(remote.ssh_hosts('/nonexistent/config'), [])


class TailscaleTest(unittest.TestCase):
    def test_peers_online_first_by_magicdns_name(self):
        out = json.dumps({'Peer': {'a': {'DNSName': 'old-box.tail1.ts.net.', 'Online': False},
                                   'b': {'DNSName': 'gpu-box.tail1.ts.net.', 'Online': True, 'HostName': 'GPU'}}})
        run = lambda *a, **k: subprocess.CompletedProcess(a, 0, out, '')
        got = remote.tailscale_hosts(run=run, which=lambda n: '/bin/tailscale')
        self.assertEqual([(h['name'], h['target'], h['online']) for h in got],
                         [('gpu-box', 'gpu-box.tail1.ts.net', True), ('old-box', 'old-box.tail1.ts.net', False)])

    def test_without_tailscale_nothing(self):
        self.assertEqual(remote.tailscale_hosts(which=lambda n: None, app='/nonexistent'), [])

    def test_the_mac_app_is_asked_only_while_it_runs(self):
        calls = []

        def run(cmd, **k):
            calls.append(cmd[0])
            return subprocess.CompletedProcess(cmd, 1, '', '')  # pgrep: not running
        self.assertEqual(remote.tailscale_hosts(run=run, which=lambda n: None, app=sys.executable), [])
        self.assertEqual(calls, ['pgrep'])


class AddressTest(unittest.TestCase):
    def test_addresses_and_undo_ids_carry_the_machine_there_and_back(self):
        reply = {'rows': [{'address': 't7', 'cwd': '/w'}, {'address': 'log:x'}], 'undo': 'abc', 'id': 'keep'}
        seen = remote._prefix(reply, 'gpu-box')
        self.assertEqual(seen, {'rows': [{'address': 'gpu-box/t7', 'cwd': '/w'}, {'address': 'log:x'}],
                                'undo': 'gpu-box/abc', 'id': 'keep'})
        self.assertEqual(remote._strip({'address': 'gpu-box/t7', 'id': 'gpu-box/abc', 'project': '/srv/x'}, 'gpu-box'),
                         {'address': 't7', 'id': 'abc', 'project': '/srv/x'})


class FailureTest(unittest.TestCase):
    def test_each_failure_in_plain_cases(self):
        e = lambda kind, msg: runtime.RuntimeError_(kind, msg)
        self.assertEqual(remote._failure('b', e('hc_missing', 'hc is not installed on b'))['result'], 'not_set_up')
        self.assertEqual(remote._failure('b', e('needs_login', 'b wants a login'))['result'], 'needs_login')
        self.assertEqual(remote._failure('b', e('unreachable', 'cannot reach b: Host key verification failed.'))['result'],
                         'new_host_key')
        got = remote._failure('b', e('unreachable', 'cannot reach b: ssh: Could not resolve hostname b'))
        self.assertEqual((got['result'], got['why']), ('unreachable', 'ssh: Could not resolve hostname b'))


class StubRt:
    """A core that knows one machine, `devbox`, and its terminals; `exec` answers as a brain there would."""
    sock, session = '/tmp/none.sock', 'assieme'

    def __init__(self):
        self.state, self.calls, self.local = 'connected', [], []
        self.there = [{'address': 'devbox/t2', 'state': 'live', 'labels': {team.TEAM: 'api', team.ROLE: 'builder'}},
                      {'address': 'devbox/t3', 'state': 'live', 'labels': {remote.SERVICE: remote.BRAIN}}]
        self.answers = {}

    def call(self, method, **p):
        self.calls.append((method, p))
        if method == 'machine.list':
            return {'machines': [{'name': 'devbox', 'target': 'devbox', 'session': 'assieme', 'state': self.state},
                                 {'name': 'theirs', 'target': 'x', 'session': None, 'state': 'connected'}]}
        if method == 'terminal.list':
            return {'terminals': self.local + (self.there if self.state == 'connected' else [])}
        if method == 'machine.exec':
            if self.state != 'connected':
                raise runtime.RuntimeError_('unavailable', 'devbox is unreachable')
            m = p['line'].split(' call ')[-1].strip("'")
            got = self.answers.get(m)
            reply = got(json.loads(base64.b64decode(p['input']))) if callable(got) else got
            return {'code': 0, 'stdout': base64.b64encode((json.dumps({'result': reply}) + '\n').encode()).decode()}
        if method == 'machine.connect':
            self.state = 'connected'
            return {}
        return {}

    def list(self, labels, all=False):
        return self.local


class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.rt = StubRt()
        self.rx = remote.Remotes(self.rt, store=os.path.join(self.dir, 'remote.json'))
        self.local = []
        self.h = self.rx.wrap({
            'team.view': lambda team: self.local.append(('view', team)) or {'team': team, 'here': True},
            'setup.status': lambda: {'here': True},
            'setup.terminal_state': lambda address: 'here',
            'team.start': lambda project, task, roles, practices, name=None: {'team': name},
            'brain.snapshot': lambda: {'teams': [], 'starting': []},
            'teams.list': lambda: {'teams': [{'name': 'web', 'kind': 'assieme', 'agents': 1}], 'found_agents': []},
            'activity.list': lambda team=None, filter='all': {'rows': [{'id': 'h1', 'day': '2026-11-13', 'at': '10:00'}]}})

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_local_calls_stay_here(self):
        self.assertEqual(self.h['team.view'](team='web'), {'team': 'web', 'here': True})
        self.assertEqual(self.h['setup.status'](), {'here': True})
        self.assertFalse(any(m == 'machine.exec' for m, _ in self.rt.calls))

    def test_a_team_there_goes_there_and_its_addresses_come_back_prefixed(self):
        self.rt.answers['team.view'] = lambda p: {'team': p['team'], 'panes': [{'address': 't2'}]}
        self.assertEqual(self.h['team.view'](team='api'), {'team': 'api', 'panes': [{'address': 'devbox/t2'}]})
        self.assertEqual(self.local, [])

    def test_machine_and_addresses_say_where(self):
        self.rt.answers['setup.status'] = {'git': True, 'agents': {}}
        self.assertEqual(self.h['setup.status'](machine='devbox'), {'git': True, 'agents': {}})
        self.rt.answers['setup.terminal_state'] = lambda p: p['address']
        self.assertEqual(self.h['setup.terminal_state'](address='devbox/t9'), 't9')  # sent without the machine

    def test_start_there_picks_a_name_free_on_every_machine(self):
        self.rt.answers['team.start'] = lambda p: {'team': p['name']}
        got = self.h['team.start'](project='~/api', task='', roles=[], practices={}, machine='devbox')
        self.assertEqual(got, {'team': 'api-2'})  # `api` runs on devbox already

    def test_snapshot_and_teams_add_remote_teams_and_keep_them_while_unreachable(self):
        snap = self.h['brain.snapshot']()
        self.assertEqual([(r['team'], r['address'], r['machine'], r['reachable']) for r in snap['teams']],
                         [('api', 'devbox/t2', 'devbox', True)])
        self.assertEqual([m['name'] for m in snap['machines']], ['devbox'])
        self.rt.state = 'unreachable'
        self.rx.raw_at = None
        snap = self.h['brain.snapshot']()
        self.assertEqual([(r['team'], r['reachable']) for r in snap['teams']], [('api', False)])
        listed = self.h['teams.list']()['teams']
        self.assertEqual([(t['name'], t.get('machine'), t.get('reachable')) for t in listed],
                         [('web', None, None), ('api', 'devbox', False)])
        # and after a restart of this brain, from what it saved
        again = remote.Remotes(self.rt, store=self.rx.store)
        self.assertEqual(again.teams(), {'api': 'devbox'})

    def test_activity_merges_newest_first_with_ids_that_go_back_there(self):
        self.rt.answers['activity.list'] = {'rows': [{'id': 'r1', 'day': '2026-11-13', 'at': '11:00'}]}
        rows = self.h['activity.list']()['rows']
        self.assertEqual([(r['id'], r.get('machine')) for r in rows], [('devbox/r1', 'devbox'), ('h1', None)])

    def test_a_team_view_there_is_kept_and_shown_while_its_machine_is_away(self):
        self.rt.answers['team.view'] = {'team': 'api', 'panes': [{'address': 't2'}]}
        self.assertNotIn('unreachable', self.h['team.view'](team='api'))
        self.rt.state = 'unreachable'
        self.rx.raw_at = None
        away = self.h['team.view'](team='api')
        self.assertEqual((away['panes'], away['unreachable']['name'], away['unreachable']['state']),
                         ([{'address': 'devbox/t2'}], 'devbox', 'unreachable'))
        with self.assertRaises(remote.RemoteError):  # never seen: nothing to show from before
            self.h['team.view'](team='api', role='x')

    def test_an_unreachable_machine_never_breaks_a_local_list(self):
        self.rt.state = 'unreachable'
        self.assertEqual(self.h['activity.list']()['rows'][0]['id'], 'h1')


class CallTest(unittest.TestCase):
    def test_errors_from_there_are_errors_here(self):
        rt = StubRt()
        rx = remote.Remotes(rt, store=os.path.join(tempfile.mkdtemp(), 'r.json'))
        rt.call = lambda m, **p: {'code': 0, 'stdout': base64.b64encode(b'{"error": {"message": "project: nope"}}\n').decode()}
        with self.assertRaisesRegex(remote.RemoteError, 'project: nope'):
            rx.call('devbox', 'project.check', {'path': '~/x'})
        rt.call = lambda m, **p: {'code': 127, 'stdout': '', 'stderr': base64.b64encode(b'sh: assieme: not found\n').decode()}
        with self.assertRaisesRegex(remote.RemoteError, "didn't answer: sh: assieme: not found"):
            rx.call('devbox', 'settings.get')


class _Brain(socketserver.StreamRequestHandler):
    def handle(self):
        req = json.loads(self.rfile.readline())
        self.wfile.write((json.dumps({'id': 1, 'result': {'got': req['method'], 'params': req['params']}}) + '\n').encode())


class CallMainTest(unittest.TestCase):
    def test_one_request_from_stdin_to_the_brain(self):
        d = tempfile.mkdtemp(dir='/tmp')
        sock = os.path.join(d, 'b.sock')
        server = socketserver.UnixStreamServer(sock, _Brain)
        threading.Thread(target=server.handle_request, daemon=True).start()
        out = io.StringIO()
        remote.call_main('team.view', io.StringIO('{"team": "api"}'), out, sock)
        server.server_close()
        shutil.rmtree(d)
        self.assertEqual(json.loads(out.getvalue())['result'], {'got': 'team.view', 'params': {'team': 'api'}})

    def test_no_brain_is_said_plainly(self):
        out = io.StringIO()
        remote.call_main('x', io.StringIO(''), out, '/nonexistent.sock')
        self.assertEqual(json.loads(out.getvalue())['error']['message'], "Assieme isn't running on this machine.")


class PackageTest(unittest.TestCase):
    def test_the_package_as_a_project_uv_can_install(self):
        data, digest = remote.package_tar()
        names = tarfile.open(fileobj=io.BytesIO(data)).getnames()
        self.assertIn('pyproject.toml', names)
        self.assertIn('assieme/remote.py', names)
        self.assertIn('assieme/practices.toml', names)
        self.assertFalse(any('__pycache__' in n for n in names))
        self.assertEqual(remote.package_digest(), digest)


class TickTest(unittest.TestCase):
    def test_after_sleep_it_dials_at_once_and_a_lost_brain_comes_back(self):
        rt = StubRt()
        now = [1000.0]
        rx = remote.Remotes(rt, store=os.path.join(tempfile.mkdtemp(), 'r.json'), clock=lambda: now[0],
                            thread=lambda target, daemon=True, args=(): type('T', (), {'start': lambda s: target(*args)})())
        started = []
        rx.start_brain = lambda m, restart=False: started.append(m)
        rx.tick()
        rt.state = 'unreachable'
        now[0] += 30
        rx.tick()  # a short gap: hc's own retry is enough
        self.assertFalse(any(m == 'machine.connect' for m, _ in rt.calls))
        now[0] += 600
        rx.tick()
        self.assertTrue(any(m == 'machine.connect' for m, _ in rt.calls))
        rx.set_up.add('devbox')
        rt.there = [t for t in rt.there if (t['labels'] or {}).get(remote.SERVICE) != remote.BRAIN]
        rx.raw_at = None
        rx.tick()
        self.assertEqual(started, ['devbox'])


@unittest.skipUnless(shutil.which('hc'), 'needs hc')
class FakeRemoteTest(unittest.TestCase):
    """Through a real hc core: the remote side is a second hc session with its own home, reached by a fake ssh."""

    def setUp(self):
        self.root = tempfile.mkdtemp(dir='/tmp', prefix='s15')
        self.here, self.there = os.path.join(self.root, 'l'), os.path.join(self.root, 'r')
        fakebin = os.path.join(self.root, 'bin')
        os.makedirs(fakebin)
        os.makedirs(os.path.join(self.here, 'assieme'))
        with open(os.path.join(fakebin, 'ssh'), 'w') as f:
            f.write(f'''#!/bin/sh
for last; do :; done
R={self.there}
[ -e $R/down ] && {{ echo "ssh: connect to host devbox port 22: Connection refused" >&2; exit 255; }}
exec env -i HOME=$R PATH=$R/.local/bin:/usr/bin:/bin XDG_STATE_HOME=$R/state XDG_CONFIG_HOME=$R/config sh -c "$last"
''')
        os.chmod(os.path.join(fakebin, 'ssh'), 0o755)
        self.sock = os.path.join(self.here, 'assieme', 'hc.sock')
        env = runtime.clean_env(os.environ) | {'HC_SESSION': 'assieme', 'HC_SOCKET': self.sock,
                                               'XDG_STATE_HOME': self.here, 'PATH': fakebin + ':' + os.environ['PATH']}
        subprocess.run(['hc', 'server', 'start'], env=env, check=True, capture_output=True)
        self.rt = runtime.Runtime(sock=self.sock)
        self.hc = shutil.which('hc')
        # `hc machine add --install` makes its own ssh calls: they reach the fake too.
        path = mock.patch.dict(os.environ, {'PATH': fakebin + ':' + os.environ['PATH']})
        path.start()
        self.addCleanup(path.stop)

        def install(rx, machine):
            # Stands in for uv: an `assieme` there that is this checkout, run by this Python.
            line = (f'mkdir -p "$HOME/.local/bin" && printf "#!/bin/sh\\nPYTHONPATH={os.getcwd()} exec {sys.executable} -P -m '
                    f'assieme \\"\\$@\\"\\n" > "$HOME/.local/bin/assieme" && chmod +x "$HOME/.local/bin/assieme"')
            code, _, err = rx.exec(machine, line)
            self.assertEqual(code, 0, err)
        self.rx = remote.Remotes(self.rt, store=os.path.join(self.here, 'remote.json'), install=install,
                                 thread=lambda target, args=(), daemon=True: type('T', (), {'start': lambda s: target(*args)})())

    def tearDown(self):
        for env in ({'HC_SOCKET': self.sock, 'HC_SESSION': 'assieme', 'XDG_STATE_HOME': self.here},
                    {'HOME': self.there, 'HC_SESSION': 'assieme', 'XDG_STATE_HOME': os.path.join(self.there, 'state')}):
            exe = os.path.join(self.there, '.local/bin/hc') if 'HOME' in env else self.hc
            if os.path.exists(exe):
                subprocess.run([exe, 'server', 'stop'], env={'PATH': '/usr/bin:/bin'} | env, capture_output=True)
        self._kill_mine()
        shutil.rmtree(self.root, ignore_errors=True)

    def _kill_mine(self):
        """Only processes under this test's own folder: the person's own cores and agents are never touched."""
        out = subprocess.run(['pgrep', '-f', self.root], capture_output=True, text=True).stdout.split()
        for pid in out:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                pass

    def wait(self, what, ok, s=20):
        deadline = time.monotonic() + s
        while not ok():
            if time.monotonic() > deadline:
                self.fail(f'timed out: {what}')
            time.sleep(0.2)

    def test_connect_set_up_run_drop_and_come_back(self):
        self.assertEqual(self.rx.test('devbox')['result'], 'not_set_up')
        self.rx.setup('devbox')
        job = self.rx._job('devbox')
        self.assertEqual((job['error'], [s['state'] for s in job['steps']]), (None, ['done', 'done', 'done']))
        self.assertTrue(os.path.exists(os.path.join(self.there, '.local/bin/hc')), 'hc copied there')
        c = self.rx.check('devbox')
        self.assertEqual((c['ready'], c['outdated']), (True, False))
        self.assertEqual(self.rx.test('devbox')['result'], 'ready')
        self.assertEqual([(m['name'], m['set_up']) for m in self.rx.machines_view()], [('devbox', True)])

        # An agent of a team there: listed from here as devbox's, never as this Mac's.
        self.rt.call('terminal.run', on='devbox', argv=['sleep', '300'], wait=False,
                     labels={team.TEAM: 'api', team.ROLE: 'builder'}, cwd=self.there)
        self.rx.raw_at = None
        self.assertEqual(self.rx.teams(), {'api': 'devbox'})
        self.assertEqual(team.status(self.rt), [])
        handlers = self.rx.wrap({'settings.get': lambda: {'here': True}, 'brain.snapshot': lambda: {'teams': [], 'starting': []}})
        self.assertNotIn('here', handlers['settings.get'](machine='devbox'))  # answered by the brain there

        # The line drops: the team stays, marked unreachable; Try now brings it back.
        open(os.path.join(self.there, 'down'), 'w').close()
        connect = subprocess.run(['pgrep', '-f', os.path.join(self.there, '.local/bin/herdr-core') + ' connect'],
                                 capture_output=True, text=True).stdout.split()
        for pid in connect:
            os.kill(int(pid), signal.SIGTERM)
        self.wait('unreachable', lambda: self.rx.machines()[0]['state'] == 'unreachable')
        self.rx.raw_at = None
        self.assertEqual([(r['team'], r['reachable']) for r in handlers['brain.snapshot']()['teams']], [('api', False)])
        os.remove(os.path.join(self.there, 'down'))
        self.rx.connect('devbox')
        self.assertEqual(self.rx.machines()[0]['state'], 'connected')
        self.rx.raw_at = None
        self.assertEqual([(r['team'], r['reachable']) for r in handlers['brain.snapshot']()['teams']], [('api', True)])


if __name__ == '__main__':
    unittest.main()
