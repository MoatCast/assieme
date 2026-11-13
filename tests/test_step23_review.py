"""The step 23 review's findings, one test each."""
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from assieme import bridge, container, launch, worktree
from assieme.preset import Role


def role(agent, **kw):
    return Role('builder-1', agent, 'b.md', 'b', **kw)


def repo():
    d = tempfile.mkdtemp()
    subprocess.run(['git', 'init', '-q', d], check=True)
    return d


def words(a):
    return a[a.index(next(w for w in a if os.path.basename(w) in ('cursor-agent', 'gemini'))):]


DOCKER = lambda n: '/x/docker' if n == 'docker' else None


class CursorMcpTest(unittest.TestCase):  # 1
    def test_mcp_servers_are_approved_only_when_assieme_is_the_only_one(self):
        cwd = repo()
        self.assertIn('--approve-mcps', launch.argv(role('cursor'), which=lambda n: None, cwd=cwd))
        cwd = repo()
        os.makedirs(os.path.join(cwd, '.cursor'))
        json.dump({'mcpServers': {'theirs': {'command': 'x'}}}, open(os.path.join(cwd, '.cursor', 'mcp.json'), 'w'))
        self.assertNotIn('--approve-mcps', launch.argv(role('cursor'), which=lambda n: None, cwd=cwd))


class HomeVolumeTest(unittest.TestCase):  # 2
    def test_each_project_gets_its_own_home(self):
        vols = []
        for project in ('/p/shop', '/p/blog'):
            path = f'{project}/.assieme/worktrees/builder-1'
            a = container.run_argv(role('codex', container=True), ['codex'], {}, path, 't', which=DOCKER)
            vols += [a[i + 1] for i, w in enumerate(a) if w == '-v' and a[i + 1].endswith(':/home/node')]
        self.assertEqual(len(set(vols)), 2)


class ShareSigninTest(unittest.TestCase):  # 3
    def test_only_the_sign_in_file_read_only(self):
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, '.codex', 'sessions'))
        open(os.path.join(home, '.codex', 'auth.json'), 'w').write('{}')
        a = container.run_argv(role('codex', container=True, share_signin=True), ['codex'], {}, '/p/.assieme/worktrees/b',
                               't', which=DOCKER, home=home)
        mounts = [a[i + 1] for i, w in enumerate(a) if w == '-v' and home in a[i + 1]]
        self.assertEqual(mounts, [f'{home}/.codex/auth.json:/home/node/.codex/auth.json:ro'])

    def test_kinds_whose_sign_in_cant_be_shared_mount_nothing(self):
        for kind in ('claude', 'cursor'):
            self.assertNotIn(kind, container.SIGNIN)


class AppleContainerTest(unittest.TestCase):  # 4
    def test_apples_tool_is_not_offered_until_tried(self):
        self.assertIsNone(container.tool(lambda n: '/x/container' if n == 'container' else None))


class CwdTest(unittest.TestCase):  # 5
    def test_kinds_that_write_files_need_the_agents_folder(self):
        for kind in ('gemini', 'cursor'):
            with self.assertRaises(ValueError):
                launch.argv(role(kind), which=lambda n: None)


class MergeTest(unittest.TestCase):  # 6
    def test_a_projects_own_settings_keep_theirs_and_gain_ours_once(self):
        cwd = repo()
        os.makedirs(os.path.join(cwd, '.gemini'))
        path = os.path.join(cwd, '.gemini', 'settings.json')
        json.dump({'theme': 'dark', 'mcpServers': {'theirs': {'command': 'x'}}}, open(path, 'w'))
        subprocess.run(['git', '-C', cwd, 'add', '.'], check=True)
        subprocess.run(['git', '-C', cwd, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'x'], check=True)
        for _ in range(2):
            launch.argv(role('gemini'), which=lambda n: None, cwd=cwd)
        s = json.load(open(path))
        self.assertEqual(s['theme'], 'dark')
        self.assertEqual(set(s['mcpServers']), {'theirs', 'assieme'})
        self.assertEqual(len(s['hooks']['AfterAgent']), 1)
        st = subprocess.run(['git', '-C', cwd, 'status', '--porcelain'], capture_output=True, text=True).stdout
        self.assertEqual(st, '')  # the agent's commits never carry Assieme's entries

    def test_a_file_that_isnt_plain_json_is_left_alone(self):
        cwd = repo()
        os.makedirs(os.path.join(cwd, '.cursor'))
        open(os.path.join(cwd, '.cursor', 'hooks.json'), 'w').write('// comments\n{}')
        launch.argv(role('cursor'), which=lambda n: None, cwd=cwd)
        self.assertEqual(open(os.path.join(cwd, '.cursor', 'hooks.json')).read(), '// comments\n{}')


class SafeGitTest(unittest.TestCase):  # 7
    def test_only_container_copies_lose_hooks(self):
        plain = repo()
        self.assertFalse(worktree.is_clone(plain))
        copy = os.path.join(repo(), '.assieme', 'worktrees', 'b')
        os.makedirs(copy)
        subprocess.run(['git', 'init', '-q', copy], check=True)
        self.assertTrue(worktree.is_clone(copy))


class TicketTest(unittest.TestCase):  # 8
    def setUp(self):
        p = mock.patch.dict(os.environ, {'XDG_STATE_HOME': tempfile.mkdtemp()})
        p.start()
        self.addCleanup(p.stop)

    def test_tickets_of_gone_terminals_are_dropped(self):
        a, b = bridge.issue(), bridge.issue()
        bridge.bind(a, 't1')
        bridge.bind(b, 't2')
        bridge.prune({'t2'})
        self.assertIsNone(bridge._load().get(a))
        self.assertEqual(bridge._load().get(b), 't2')

    def test_stopping_a_team_drops_its_tickets(self):
        from assieme import team
        t = bridge.issue()
        bridge.bind(t, 't9')
        rt = mock.Mock()
        rt.list.return_value = [{'address': 't9', 'state': 'live', 'labels': {team.TEAM: 'demo'}}]
        team.stop(rt, 'demo')
        self.assertIsNone(bridge._load().get(t))
