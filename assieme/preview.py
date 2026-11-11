"""Live preview: the project's dev server, run in one agent's worktree on a port of its own, so the person can look at
the branch before merging it.

Each preview runs in its own process group, and only that group is ever stopped. The groups are saved, so a brain that
restarts stops what it left running. Refusals raise ReviewError (or PreviewError) with a sentence for the screen.
"""
import json
import os
import re
import signal
import socket
import subprocess
import threading
import time

from . import quiet, runtime

START_TIMEOUT_S = 90
INSTALL_TIMEOUT_S = 300
TAIL = 12
SCRIPTS = ('dev', 'start', 'serve')
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
MISSING = re.compile(r"(?:^|\s)([\w@./-]+): command not found|Cannot find (?:module|package) '([^']+)'|"
                     r"No module named '?([\w.]+)", re.M)
IN_USE = re.compile(r'EADDRINUSE|address already in use', re.I)


class PreviewError(ValueError):
    pass


def _has(path, name):
    return os.path.isfile(os.path.join(path, name))


def _package_manager(path):
    for lock, pm in (('pnpm-lock.yaml', 'pnpm'), ('yarn.lock', 'yarn'), ('bun.lock', 'bun'), ('bun.lockb', 'bun')):
        if _has(path, lock):
            return pm
    return 'npm'


def start_command(path):
    """The command that starts the project's dev server, read from what is in its folder (as `review.test_command`
    does for tests); None when there isn't one we can tell."""
    if _has(path, 'package.json'):
        try:
            with open(os.path.join(path, 'package.json')) as f:
                scripts = json.load(f).get('scripts') or {}
        except (OSError, ValueError, AttributeError):
            scripts = {}
        for name in SCRIPTS:
            if isinstance(scripts.get(name), str) and scripts[name].strip():
                return f'{_package_manager(path)} run {name}'
    if _has(path, 'manage.py'):
        return 'python3 manage.py runserver $PORT'
    if _has(path, 'index.html'):
        return 'python3 -m http.server $PORT --bind 127.0.0.1'
    return None


def install_command(path):
    return f'{_package_manager(path)} install' if _has(path, 'package.json') else None


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _tail(log):
    try:
        with open(log, encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError:
        return ''
    lines = [line for line in ANSI.sub('', text).replace('\r', '\n').splitlines() if line.strip()]
    return '\n'.join(lines[-TAIL:])


def why(command, role, rc, output, path, installed):
    """What went wrong, in words, and the fix the screen offers: 'install', 'retry' or 'command'."""
    if rc is None:
        return f"{command} is running but didn't open a port, so it was stopped.", 'command'
    if IN_USE.search(output):
        return f'{command} stopped: its port was already taken. Try again to get another one.', 'retry'
    found = MISSING.search(output)
    no_packages = _has(path, 'package.json') and not os.path.isdir(os.path.join(path, 'node_modules'))
    if found or rc == 127 or no_packages:
        what = next((g for g in found.groups() if g), None) if found else None
        thing = f"{what} isn't installed" if what else "something it needs isn't installed"
        if installed or not install_command(path):
            return f"{command} stopped: {thing} in {role}'s worktree. Check the start command.", 'command'
        return f"{command} stopped: {thing} in {role}'s worktree. Install the project's packages there, then try again.", 'install'
    return f'{command} stopped before it opened a port.', 'command'


class Previews:
    def __init__(self, reviews, popen=subprocess.Popen, run=subprocess.run, file=None, logs=None, clock=time.monotonic,
                 start_timeout=START_TIMEOUT_S):
        self.reviews, self.popen, self.run, self.clock, self.start_timeout = reviews, popen, run, clock, start_timeout
        self.file = file or os.path.join(quiet.state_dir(), 'previews.json')
        self.logs = logs or os.path.join(quiet.state_dir(), 'previews')
        self.items = {}
        self.lock = threading.RLock()

    # --- saved state: the start command per project, and the process groups running now

    def _load(self):
        try:
            with open(self.file) as f:
                got = json.load(f)
        except (OSError, ValueError):
            return {'commands': {}, 'running': {}}
        ok = lambda k: got.get(k) if isinstance(got, dict) and isinstance(got.get(k), dict) else {}
        return {'commands': ok('commands'), 'running': ok('running')}

    def _save(self, commands=None):
        saved = self._load()
        if commands is not None:
            saved['commands'] = commands
        saved['running'] = {f'{t}/{r}': {'pgid': i['pgid'], 'path': i['path']} for (t, r), i in self.items.items()
                            if i['pgid']}
        quiet._write_json(self.file, saved)

    def commands(self):
        return self._load()['commands']

    # --- processes

    def _ports(self, pgid):
        r = self.run(['lsof', '-a', '-nP', '-iTCP', '-sTCP:LISTEN', '-g', str(pgid), '-Fn'], capture_output=True,
                     encoding='utf-8', errors='replace')
        return sorted({int(line.rsplit(':', 1)[1]) for line in r.stdout.splitlines()
                       if line.startswith('n') and line.rsplit(':', 1)[-1].isdigit()})

    def _cwds(self, pgid):
        r = self.run(['lsof', '-a', '-g', str(pgid), '-d', 'cwd', '-Fn'], capture_output=True, encoding='utf-8',
                     errors='replace')
        return {line[1:] for line in r.stdout.splitlines() if line.startswith('n')}

    def _kill(self, pgid, proc=None):
        """TERM to the preview's own process group, KILL to whatever is left after 3 seconds."""
        if not pgid:
            return  # already stopped: the id may belong to someone else by now
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        for _ in range(30):
            if proc:
                proc.poll()  # reap the shell, or it counts as alive
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                pass  # macOS says this while the group is still exiting
            time.sleep(0.1)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        if proc:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def stop_left_behind(self):
        """At brain start: stop the previews a previous brain left running, if their processes still run in the same
        worktree (a reused process group id is someone else's)."""
        with self.lock:
            for entry in self._load()['running'].values():
                pgid, path = entry.get('pgid'), entry.get('path')
                if isinstance(pgid, int) and pgid > 1 and isinstance(path, str) and os.path.realpath(path) in {
                        os.path.realpath(c) for c in self._cwds(pgid)}:
                    self._kill(pgid)
            self._save()

    # --- what the app calls

    def start(self, team, role, command=None, install=False):
        project, _, _, path = self.reviews._ctx(team, role)
        if not os.path.isdir(path):
            raise PreviewError(f"{role}'s worktree isn't there any more, so there's nothing to preview.")
        typed = (command or '').strip()
        with self.lock:
            if typed:
                self._save(self.commands() | {project: typed})
            cmd = typed or self.commands().get(project) or start_command(path)
            if not cmd:
                return {'team': team, 'role': role, 'project': os.path.basename(project), 'state': 'needs_command'}
            self.stop(team, role)  # one preview per agent: starting again replaces it
            first = install_command(path) if install else None
            port = free_port()
            os.makedirs(self.logs, exist_ok=True)
            log = os.path.join(self.logs, f'{team}-{role}.log')
            env = runtime.clean_env(os.environ) | {'PORT': str(port), 'BROWSER': 'none'}  # the app opens it, not the tool
            with open(log, 'wb') as out:
                try:
                    proc = self.popen(['/bin/sh', '-c', f'{first} && {cmd}' if first else cmd], cwd=path, env=env,
                                      stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                      start_new_session=True)  # its own process group: Stop ends exactly this
                except OSError as e:
                    raise PreviewError(f"Couldn't start {cmd}: {e}") from None
            self.items[(team, role)] = {
                'team': team, 'role': role, 'project': project, 'path': path, 'command': cmd, 'port': port,
                'proc': proc, 'pgid': proc.pid, 'log': log, 'started': self.clock(), 'installed': bool(first),
                'timeout': INSTALL_TIMEOUT_S if first else self.start_timeout, 'state': 'starting', 'url': None,
                'reason': '', 'output': '', 'fix': None,
                'team_live': self._team_live(team)}  # a stopped team's branch can be previewed too
            self._save()
            return self._view(self._refresh(self.items[(team, role)]))

    def stop(self, team, role):
        with self.lock:
            item = self.items.pop((team, role), None)
            if item:
                self._kill(item['pgid'], item['proc'])
                self._save()
            return {'stopped': bool(item)}

    def _team_live(self, team):
        return any(r.get('state') == 'live' for r in self.reviews._rows(team))

    def sweep(self):
        """Previews stop when their worktree is gone (merged, discarded, removed by hand) or their team was running
        and has stopped, however it was stopped."""
        with self.lock:
            live = {}
            for key, item in list(self.items.items()):
                if item['team_live'] and key[0] not in live:
                    live[key[0]] = self._team_live(key[0])
                if not os.path.isdir(item['path']) or (item['team_live'] and not live[key[0]]):
                    self.stop(*key)

    def list(self):
        with self.lock:
            self.sweep()
            return [self._view(self._refresh(i)) for i in self.items.values()]

    def _refresh(self, item):
        if item['state'] == 'failed':
            return item
        rc = item['proc'].poll()
        ports = [] if rc is not None else self._ports(item['pgid'])
        if ports:
            port = item['port'] if item['port'] in ports else ports[0]
            item.update(state='running', url=f'http://localhost:{port}', shown_port=port)
        elif rc is not None or (item['state'] == 'starting' and self.clock() - item['started'] > item['timeout']):
            self._kill(item['pgid'], item['proc'])
            output = _tail(item['log'])
            reason, fix = why(item['command'], item['role'], rc, output, item['path'], item['installed'])
            item.update(state='failed', url=None, reason=reason, fix=fix, output=output, pgid=None)
            self._save()
        return item

    def _view(self, i):
        return {'team': i['team'], 'role': i['role'], 'project': os.path.basename(i['project']), 'state': i['state'],
                'command': i['command'], 'url': i['url'], 'port': i.get('shown_port'), 'reason': i['reason'],
                'output': i['output'], 'fix': i['fix'],
                'install': install_command(i['path']) if i['fix'] == 'install' else None}  # named on the button: it downloads

    def handlers(self):
        return {'preview.list': self.list, 'preview.start': self.start, 'preview.stop': self.stop}
