"""Agents in containers: the docker command, the clone that replaces a worktree, and the bridge for team tools."""
import json
import os
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

from assieme import bridge, container, launch, mcpserver, worktree
from assieme.preset import Role, Team


def role(agent='gemini', **kw):
    return Role('builder-3', agent, 'b.md', 'brief', **kw)


def git(cwd, *args):
    return subprocess.run(['git', '-C', cwd, *args], capture_output=True, text=True, check=True).stdout.strip()


class Done:
    def __init__(self, returncode):
        self.returncode = returncode


class StateTest(unittest.TestCase):
    def test_no_tool(self):
        self.assertEqual(container.state('gemini', which=lambda n: None),
                         {'tool': None, 'running': False, 'image_ready': False})
        self.assertEqual(container.problem('gemini', which=lambda n: None), "Containers need Docker, and it isn't on this Mac.")

    def test_docker_not_running(self):
        which = lambda n: '/x/docker' if n == 'docker' else None
        run = lambda argv, **k: Done(1)
        self.assertEqual(container.problem('gemini', which, run), 'Docker is on this Mac but not running.')

    def test_ready_needs_the_kinds_image(self):
        which = lambda n: '/x/docker' if n == 'docker' else None
        seen = []
        run = lambda argv, **k: seen.append(argv) or Done(0)
        self.assertEqual(container.state('cursor', which, run), {'tool': 'docker', 'running': True, 'image_ready': True})
        self.assertEqual(seen[-1], ['/x/docker', 'image', 'inspect', 'assieme-agent-cursor:1'])


class RunArgvTest(unittest.TestCase):
    def setUp(self):
        self.home, self.state, self.cwd = tempfile.mkdtemp(), tempfile.mkdtemp(), tempfile.mkdtemp()
        p = mock.patch.dict(os.environ, {'XDG_STATE_HOME': self.state})
        p.start()
        self.addCleanup(p.stop)
        self.which = lambda n: '/x/docker' if n == 'docker' else None

    def argv(self, r, bridge='4321/tick'):
        return launch.argv(r, cwd=self.cwd, terminal='demo-builder-3', bridge=bridge, which=self.which)

    def test_sees_its_copy_and_assieme_only(self):
        a = self.argv(role(container=True))
        self.assertEqual(a[:8], ['/x/docker', 'run', '--rm', '-it', '--init', '--name', 'assieme-demo-builder-3', '-v'])
        mounts = [a[i + 1] for i, w in enumerate(a) if w == '-v']
        self.assertIn(f'{self.cwd}:{self.cwd}', mounts)
        self.assertIn(f'{container.PACKAGE}:{container.PACKAGE}:ro', mounts)
        self.assertTrue(any(m.startswith('assieme-home-gemini-') and m.endswith(':/home/node') for m in mounts))
        self.assertFalse(any(m.startswith(os.path.expanduser('~/.gemini')) for m in mounts))
        self.assertEqual(a[a.index('-w') + 1], self.cwd)
        env = dict(a[i + 1].split('=', 1) for i, w in enumerate(a) if w == '-e')
        self.assertEqual(env['ASSIEME_BRIDGE'], 'host.docker.internal:4321/tick')
        self.assertEqual(env['GIT_CONFIG_KEY_0'], launch.BLOCK_PUSH['GIT_CONFIG_KEY_0'])
        self.assertIn('assieme-agent-gemini:1', a)
        inner = a[a.index('assieme-agent-gemini:1') + 1:]
        self.assertEqual(inner, ['gemini'])  # its own program, by name, inside the image
        settings = json.load(open(os.path.join(self.cwd, '.gemini', 'settings.json')))
        self.assertTrue(settings['hooks']['AfterAgent'][0]['hooks'][0]['command'].startswith('python3 -P -m assieme.hook'))

    def test_claude_never_shares_a_signin(self):
        a = self.argv(role('claude', container=True, share_signin=True))
        self.assertFalse(any('/.claude:' in a[i + 1] for i, w in enumerate(a) if w == '-v'))

    def test_a_container_agent_needs_no_program_on_the_mac(self):
        self.assertEqual(launch.missing_clis([role(container=True)], which=lambda n: None), [])


class CloneTest(unittest.TestCase):
    def setUp(self):
        self.project = tempfile.mkdtemp()
        git(self.project, 'init', '-q', '-b', 'main')
        git(self.project, 'config', 'user.email', 'a@example.com')
        git(self.project, 'config', 'user.name', 'A')
        open(os.path.join(self.project, 'f.txt'), 'w').write('one\n')
        git(self.project, 'add', '.')
        git(self.project, 'commit', '-qm', 'first')
        self.team = Team('demo', self.project)

    def test_a_container_role_gets_a_clone_whose_commits_come_back(self):
        path = worktree.ensure(self.team, role(container=True))
        self.assertTrue(worktree.is_clone(path))
        self.assertEqual(git(path, 'branch', '--show-current'), 'assieme/demo/builder-3')
        open(os.path.join(path, 'f.txt'), 'w').write('two\n')
        git(path, 'commit', '-qam', 'agent work')
        worktree.sync(self.project)
        self.assertEqual(git(self.project, 'log', '-1', '--format=%s', 'assieme/demo/builder-3'), 'agent work')
        self.assertEqual(worktree.ensure(self.team, role(container=True)), path)  # reused while clean

    def test_git_never_takes_a_program_from_a_clones_config(self):
        path = worktree.ensure(self.team, role(container=True))
        marker = os.path.join(self.project, 'ran')
        git(path, 'config', 'core.fsmonitor', f'touch {marker}; false')
        worktree._git(path, 'status', '--porcelain')
        self.assertFalse(os.path.exists(marker))

    def test_sync_does_nothing_without_a_clone(self):
        with mock.patch.object(worktree, '_git') as g:
            worktree.sync(self.project)
        g.assert_not_called()


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        p = mock.patch.dict(os.environ, {'XDG_STATE_HOME': self.state})
        p.start()
        self.addCleanup(p.stop)
        self.seen = []
        handlers = {'mcp.message': lambda **k: self.seen.append(k) or {'ok': True},
                    'review.merge': lambda **k: self.fail('never reachable')}
        self.port = bridge.start(handlers)
        self.ticket = bridge.issue()
        bridge.bind(self.ticket, 't-7')

    def ask(self, request):
        with socket.create_connection(('127.0.0.1', self.port), timeout=5) as s, s.makefile('rwb') as f:
            f.write((json.dumps(request) + '\n').encode())
            f.flush()
            return json.loads(f.readline())

    def test_a_ticket_speaks_only_for_its_own_agent(self):
        got = self.ask({'id': 1, 'ticket': self.ticket, 'method': 'mcp.message',
                        'params': {'address': 't-1', 'to': 'planner', 'text': 'hi'}})
        self.assertEqual(got['result'], {'ok': True})
        self.assertEqual(self.seen, [{'address': 't-7', 'to': 'planner', 'text': 'hi'}])

    def test_no_ticket_or_another_method_gets_nothing(self):
        self.assertIn('error', self.ask({'id': 1, 'ticket': 'nope', 'method': 'mcp.message', 'params': {}}))
        self.assertIn('error', self.ask({'id': 1, 'ticket': self.ticket, 'method': 'review.merge', 'params': {}}))
        self.assertEqual(self.seen, [])
        self.assertEqual(os.stat(os.path.join(self.state, 'assieme', 'bridge-tickets.json')).st_mode & 0o777, 0o600)

    def test_the_mcp_server_inside_a_container_calls_through_the_bridge(self):
        with mock.patch.dict(os.environ, {'ASSIEME_BRIDGE': f'127.0.0.1:{self.port}/{self.ticket}'}):
            self.assertEqual(mcpserver.agent_address(), 'bridge')
            self.assertEqual(mcpserver.call('mcp.message', address='bridge', to='x', text='y'), {'ok': True})
        self.assertEqual(self.seen[-1]['address'], 't-7')
