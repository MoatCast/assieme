"""Live preview, with real tiny servers (`python3 -m http.server`) in real git worktrees. Every process a test starts
is stopped by the test."""
import json
import os
import shutil
import subprocess
import tempfile
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


class Running(Base):
    def setUp(self):
        super().setUp()
        self.p = Previews(self.r, start_timeout=5)
        self.addCleanup(lambda: [self.p.stop(t, r) for t, r in list(self.p.items)])

    def one(self, role):
        return next((x for x in self.p.list() if x['role'] == role), None)

    def test_two_agents_each_on_their_own_port_then_both_stop(self):
        self.branch('builder-1', {'index.html': 'one'})
        self.branch('builder-2', {'index.html': 'two'})
        self.p.start('demo', 'builder-1')
        self.p.start('demo', 'builder-2')
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
        self.p.start('demo', 'builder-1', command='python3 -m http.server $PORT --bind 127.0.0.1')
        wait_for(lambda: self.one('builder-1'), 'running')
        self.p.stop('demo', 'builder-1')
        self.assertEqual(self.p.commands(), {self.repo: 'python3 -m http.server $PORT --bind 127.0.0.1'})
        self.branch('builder-2', {'d.py': 'x'})
        self.assertEqual(self.p.start('demo', 'builder-2')['state'], 'starting')  # the saved command, for every agent

    def test_a_server_that_exits_says_why_with_its_last_lines(self):
        self.branch('builder-1', {'c.py': 'x'})
        self.p.start('demo', 'builder-1', command='echo starting up; nosuchtool --serve')
        got = wait_for(lambda: self.one('builder-1'), 'failed')
        self.assertIn("nosuchtool isn't installed", got['reason'])
        self.assertIn('starting up', got['output'])
        self.assertEqual(got['fix'], 'command')  # nothing to install for this project
        self.assertIsNone(got['install'])

    def test_install_and_try_again_names_and_runs_the_install_first(self):
        self.branch('builder-1', {'package.json': '{"scripts": {"dev": "nosuchtool"}}'})
        self.p.start('demo', 'builder-1')
        got = wait_for(lambda: self.one('builder-1'), 'failed')
        self.assertEqual((got['fix'], got['install']), ('install', 'npm install'))
        self.p.popen = lambda args, **kw: subprocess.Popen(['/bin/sh', '-c', 'echo "$0"; exit 3', args[2]], **kw)
        self.p.start('demo', 'builder-1', install=True)
        got = wait_for(lambda: self.one('builder-1'), 'failed')
        self.assertIn('npm install && npm run dev', got['output'])  # what would have run, shown by the stand-in

    def test_a_server_that_never_opens_a_port_is_stopped(self):
        self.branch('builder-1', {'c.py': 'x'})
        self.p.start('demo', 'builder-1', command='sleep 60')
        pgid = self.p.items[('demo', 'builder-1')]['pgid']
        got = wait_for(lambda: self.one('builder-1'), 'failed', timeout=15)
        self.assertIn("didn't open a port", got['reason'])
        self.assertFalse(alive(pgid))

    def test_merging_or_discarding_stops_it_before_the_worktree_goes(self):
        tip = self.branch('builder-1', {'index.html': 'one'})
        self.r.on_archive = self.p.stop
        self.p.start('demo', 'builder-1')
        wait_for(lambda: self.one('builder-1'), 'running')
        pgid = self.p.items[('demo', 'builder-1')]['pgid']
        self.r.discard('demo', 'builder-1', tip)
        self.assertFalse(alive(pgid))
        self.assertEqual(self.p.list(), [])
        tip = self.branch('builder-2', {'index.html': 'two'})
        self.p.start('demo', 'builder-2')
        wait_for(lambda: self.one('builder-2'), 'running')
        self.assertTrue(self.r.merge('demo', 'builder-2', tip)['preview_stopped'])
        self.assertEqual(self.p.list(), [])

    def test_a_running_team_that_stops_takes_its_previews_with_it(self):
        self.branch('builder-1', {'index.html': 'one'})
        self.rt.agent('builder-1')
        self.p.start('demo', 'builder-1')
        wait_for(lambda: self.one('builder-1'), 'running')
        self.rt.rows[0]['state'] = 'finished'
        self.p.sweep()
        self.assertEqual(self.p.list(), [])

    def test_a_stopped_teams_branch_can_be_previewed(self):
        self.branch('builder-1', {'index.html': 'one'})
        self.p.start('demo', 'builder-1')
        wait_for(lambda: self.one('builder-1'), 'running')
        self.p.sweep()
        self.assertEqual(len(self.p.list()), 1)

    def test_a_new_brain_stops_what_the_old_one_left_running(self):
        self.branch('builder-1', {'index.html': 'one'})
        self.p.start('demo', 'builder-1')
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


if __name__ == '__main__':
    unittest.main()
