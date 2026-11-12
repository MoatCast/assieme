"""Live preview: the project's dev server, run in one agent's worktree on a port of its own, so the person can look at
the branch before merging it.

Each preview runs in its own process group, and only that group is ever stopped. The groups are saved, so a brain that
restarts stops what it left running. Refusals raise ReviewError (or PreviewError) with a sentence for the screen.
"""
import hashlib
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
LOST_TIMEOUT_S = 10  # a running server with no port this long has stopped serving
LOCAL = {'HOST': '127.0.0.1', 'HOSTNAME': '127.0.0.1'}  # Next.js and others: this Mac only, not the network
TAIL = 12
SCRIPTS = ('dev', 'start', 'serve')
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
MISSING = re.compile(r"(?:^|\s)([\w@./-]+): command not found|Cannot find (?:module|package) '([^']+)'|"
                     r"No module named '?([\w.]+)", re.M)
IN_USE = re.compile(r'EADDRINUSE|address already in use', re.I)
SCRIPT = re.compile(r'^(?:npm|pnpm|yarn|bun)(?: run)? ([\w:.-]+)\s*$')
INSTALL_SCRIPTS = ('preinstall', 'install', 'postinstall', 'prepare')  # what a package install runs


class PreviewError(ValueError):
    pass


def _has(path, name):
    return os.path.isfile(os.path.join(path, name))


def _package_manager(path):
    for lock, pm in (('pnpm-lock.yaml', 'pnpm'), ('yarn.lock', 'yarn'), ('bun.lock', 'bun'), ('bun.lockb', 'bun')):
        if _has(path, lock):
            return pm
    return 'npm'


def _package(text):
    try:
        got = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _read(path, name):
    try:
        with open(os.path.join(path, name), encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return None


def _scripts(package):
    scripts = package.get('scripts')
    return scripts if isinstance(scripts, dict) else {}


def start_command(path):
    """The command that starts the project's dev server, read from what is in its folder (as `review.test_command`
    does for tests); None when there isn't one we can tell."""
    if _has(path, 'package.json'):
        scripts = _scripts(_package(_read(path, 'package.json')))
        for name in SCRIPTS:
            if isinstance(scripts.get(name), str) and scripts[name].strip():
                return f'{_package_manager(path)} run {name}'
    if _has(path, 'manage.py'):
        return 'python3 manage.py runserver $PORT'
    if _has(path, 'index.html'):
        return 'python3 -m http.server $PORT --bind 127.0.0.1'
    return None


def install_command(path):
    """The install that leaves the agent's lockfile as it is: a changed or new one would read as uncommitted work."""
    if not _has(path, 'package.json'):
        return None
    pm = _package_manager(path)
    if pm != 'npm':
        return f'{pm} install --frozen-lockfile'
    return 'npm ci' if _has(path, 'package-lock.json') or _has(path, 'npm-shrinkwrap.json') else 'npm install --no-package-lock'


def needs_install(path):
    """A Node project whose packages aren't in this worktree yet (a fresh worktree has none). Yarn Plug'n'Play keeps
    them without node_modules."""
    package = _package(_read(path, 'package.json'))
    wants = any(package.get(k) for k in ('dependencies', 'devDependencies'))
    return wants and not os.path.isdir(os.path.join(path, 'node_modules')) and not (
        _has(path, '.pnp.cjs') or _has(path, '.pnp.js'))


def script_of(command):
    """The package.json script a command runs (`npm run dev` -> 'dev'), or None."""
    m = SCRIPT.match(command or '')
    return m and m.group(1)


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
    if found or rc == 127 or needs_install(path):
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
        self.items, self.confirmed = {}, {}  # confirmed: (team, role) -> the fingerprint of what the person saw run
        self.lock = threading.RLock()
        self.lost_timeout = LOST_TIMEOUT_S

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

    def _what_runs(self, project, base, path, cmd, first):
        """What the person is shown before a first run, step by step: each command, with the content of the package
        scripts it runs that the branch changed against the base branch (the agent may have written them), and of the
        main script it runs. manage.py counts as a script when the branch changed it."""
        now = _scripts(_package(_read(path, 'package.json')))
        before = _scripts(_package(self._git_show(project, base, 'package.json')))
        script = lambda n, always=False: ([{'name': n, 'runs': str(now[n]), 'changed': now.get(n) != before.get(n)}]
                                          if n in now and (always or now.get(n) != before.get(n)) else [])
        name = script_of(cmd)
        steps = [{'line': first, 'scripts': [x for n in INSTALL_SCRIPTS for x in script(n)]}] if first else []
        mine = [*script(f'pre{name}'), *script(name, always=True), *script(f'post{name}')] if name else []
        managed = _read(path, 'manage.py') if 'manage.py' in cmd else None
        if managed is not None and managed != self._git_show(project, base, 'manage.py'):
            mine.append({'name': 'manage.py', 'runs': managed.strip(), 'changed': True})
        steps.append({'line': cmd, 'scripts': mine})
        changed = [x['name'] for st in steps for x in st['scripts'] if x['changed']]
        info = {'command': cmd, 'install': first, 'steps': steps, 'changed': changed}
        return info | {'fingerprint': hashlib.sha256(json.dumps([path, info], sort_keys=True).encode()).hexdigest()[:16]}

    def _git_show(self, project, base, name):
        r = self.run(['git', '-C', project, 'show', f'{base}:{name}'], capture_output=True, encoding='utf-8',
                     errors='replace')
        return r.stdout if r.returncode == 0 else None

    def _take(self, key):
        """Out of the list under the lock; the caller stops it outside, so a slow stop holds nobody up."""
        with self.lock:
            item = self.items.pop(key, None)
            if item:
                self._save()
            return item

    def start(self, team, role, command=None, install=False, confirm=None):
        project, base, _, path = self.reviews.worktree(team, role)
        if not os.path.isdir(path):
            raise PreviewError(f"{role}'s worktree isn't there any more, so there's nothing to preview.")
        typed = (command or '').strip()
        with self.lock:
            if typed:
                self._save(self.commands() | {project: typed})
            cmd = typed or self.commands().get(project) or start_command(path)
        if not cmd:
            return {'team': team, 'role': role, 'project': os.path.basename(project), 'state': 'needs_command'}
        first = install_command(path) if install or needs_install(path) else None
        shown = self._what_runs(project, base, path, cmd, first)
        if typed and not first and not any(st['scripts'] for st in shown['steps']):
            confirm = shown['fingerprint']  # the person just typed it, and it runs nothing an agent wrote
        if self.confirmed.get((team, role)) != shown['fingerprint']:
            if confirm != shown['fingerprint']:  # nothing the agent wrote runs before the person has seen it
                return {'team': team, 'role': role, 'project': os.path.basename(project), 'state': 'confirm', **shown}
            self.confirmed[(team, role)] = shown['fingerprint']
        old = self._take((team, role))  # one preview per agent: starting again replaces it
        if old:
            self._kill(old['pgid'], old['proc'])
        port = free_port()
        os.makedirs(self.logs, exist_ok=True)
        log = os.path.join(self.logs, f'{team}-{role}.log')
        env = runtime.clean_env(os.environ) | LOCAL | {'PORT': str(port), 'BROWSER': 'none'}  # the app opens it
        with open(log, 'wb') as out:
            try:
                proc = self.popen(['/bin/sh', '-c', f'{first} && {cmd}' if first else cmd], cwd=path, env=env,
                                  stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                  start_new_session=True)  # its own process group: Stop ends exactly this
            except OSError as e:
                raise PreviewError(f"Couldn't start {cmd}: {e}") from None
        item = {'team': team, 'role': role, 'project': project, 'path': path, 'command': cmd, 'port': port,
                'proc': proc, 'pgid': proc.pid, 'log': log, 'started': self.clock(), 'installed': bool(first),
                'timeout': INSTALL_TIMEOUT_S if first else self.start_timeout, 'state': 'starting', 'url': None,
                'reason': '', 'output': '', 'fix': None, 'lost': None,
                'team_live': self.reviews.team_live(team)}  # a stopped team's branch can be previewed too
        with self.lock:
            self.items[(team, role)] = item
            self._save()
        return self._view(self._refresh(item))

    def stop(self, team, role):
        item = self._take((team, role))
        if item:
            self._kill(item['pgid'], item['proc'])
        return {'stopped': bool(item)}

    def sweep(self):
        """Previews stop when their worktree is gone (merged, discarded, removed by hand) or their team was running
        and has stopped, however it was stopped."""
        with self.lock:
            items = list(self.items.items())
        live = {t: self.reviews.team_live(t) for t in {k[0] for k, i in items if i['team_live']}}
        for key, item in items:
            if not os.path.isdir(item['path']) or (item['team_live'] and not live[key[0]]):
                self.stop(*key)

    def list(self):
        self.sweep()
        with self.lock:
            items = list(self.items.values())
        return [self._view(self._refresh(i)) for i in items]  # lsof per preview, outside the lock

    def _refresh(self, item):
        if item['state'] == 'failed':
            return item
        rc = item['proc'].poll()
        ports = [] if rc is not None else self._ports(item['pgid'])
        now = self.clock()
        if ports:
            port = item['port'] if item['port'] in ports else ports[0]
            item.update(state='running', url=f'http://localhost:{port}', shown_port=port, lost=None)
            return item
        if rc is None and item['state'] == 'running':
            item['lost'] = item['lost'] or now
            if now - item['lost'] <= self.lost_timeout:
                return item  # a restart in the dev server's own watcher closes its port for a moment
        elif rc is None and now - item['started'] <= item['timeout']:
            return item
        lost = rc is None and item['state'] == 'running'
        self._kill(item['pgid'], item['proc'])
        output = _tail(item['log'])
        reason, fix = ((f'{item["command"]} stopped listening on its port, so it was stopped.', 'retry') if lost
                       else why(item['command'], item['role'], rc, output, item['path'], item['installed']))
        item.update(state='failed', url=None, reason=reason, fix=fix, output=output, pgid=None)
        with self.lock:
            self._save()
        return item

    def _view(self, i):
        return {'team': i['team'], 'role': i['role'], 'project': os.path.basename(i['project']), 'state': i['state'],
                'command': i['command'], 'url': i['url'], 'port': i.get('shown_port'), 'reason': i['reason'],
                'output': i['output'], 'fix': i['fix'],
                'install': install_command(i['path']) if i['fix'] == 'install' else None}  # named on the button: it downloads

    def handlers(self):
        return {'preview.list': self.list, 'preview.start': self.start, 'preview.stop': self.stop}
