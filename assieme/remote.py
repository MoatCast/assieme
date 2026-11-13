"""Remote machines (step 15): another machine's agents and brain, reached only through hc's machine line.

Each machine runs its own brain beside its agents, in a terminal of its own `assieme` core, so a remote team keeps
being coordinated while this Mac sleeps. In the Mac's brain this module lists hosts, tests and sets up machines,
forwards the app's calls to the brain where the team lives (`machine.exec` running `assieme call` there), and adds
remote teams to the calls that list every team. Assieme never runs ssh itself: hc does, with the person's own ssh,
SSH config and agent, and nothing here reads a key or a password.
"""
import base64
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import live, newteam, quiet, runtime, team

SESSION = 'assieme'                      # the core Assieme uses there, as here
SERVICE, BRAIN = 'assieme.service', 'brain'
BRAIN_NAME = 'assieme-brain'
ASSIEME = '"$HOME/.local/bin/assieme"'   # where `uv tool install` puts it
HOME_DIR = '.local/share/assieme'         # from the home folder there
CALL_S = 18                               # under the app's own 20 s, so its error is ours, not a timeout
SLOW_S = {'team.start': 115, 'team.add_agent': 115, 'team.suggest': 175, 'review.get': 55, 'review.merge': 55,
          'review.undo': 55, 'review.discard': 55, 'review.pull_request': 255, 'review.ask': 85, 'review.note': 85}
KEPT = ('team.map', 'team.view', 'agent.detail', 'board.get', 'brief.get')  # shown from their last answer while away
LIST_S = 8                               # a remote that lists slower than this is shown from its last answer
BRAIN_WAIT_S = 30
WOKE_S = 60                               # a longer gap between ticks: the Mac slept
TAILSCALE_APP = '/Applications/Tailscale.app/Contents/MacOS/Tailscale'
TARGET = re.compile(r'^[A-Za-z0-9_.@:\[\]%-]+$')  # one ssh argument: never an option, never two words


class RemoteError(RuntimeError):
    pass


# --- hosts the person already has ---

def ssh_hosts(path=None):
    """Hosts named in ~/.ssh/config, with where each goes. Only Host, HostName, User and Port are read."""
    try:
        with open(path or os.path.expanduser('~/.ssh/config')) as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    hosts, current = {}, []
    for line in lines:
        words = re.split(r'\s*=\s*|\s+', line.strip(), maxsplit=1)
        key, value = words[0].lower(), (words[1].strip() if len(words) > 1 else '')
        if key == 'host':
            current = [hosts.setdefault(n, {'target': n}) for n in value.split() if not set(n) & set('*?!')]
        elif key == 'match':
            current = []
        elif key in ('hostname', 'user', 'port'):
            for h in current:
                h.setdefault(key, value)  # as ssh does: the first value wins
    out = []
    for h in hosts.values():
        where = (f"{h['user']}@" if h.get('user') else '') + h.get('hostname', h['target'])
        out.append({'target': h['target'], 'address': where + (f":{h['port']}" if h.get('port') else ''), 'from': 'ssh'})
    return out


def tailscale_hosts(run=subprocess.run, which=shutil.which, app=TAILSCALE_APP):
    """Machines on the person's tailnet, online first, by their MagicDNS name. Nothing when Tailscale isn't here."""
    exe = which('tailscale') or (app if os.path.exists(app) else None)
    if not exe:
        return []
    try:
        data = json.loads(run([exe, 'status', '--json'], capture_output=True, text=True, timeout=5).stdout)
        peers = (data.get('Peer') or {}).values()
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        return []
    out = []
    for p in peers:
        dns = (p.get('DNSName') or '').rstrip('.')
        name = dns.split('.')[0] or p.get('HostName') or ''
        if name and TARGET.match(dns or name):
            out.append({'target': dns or name, 'name': name, 'address': dns or name, 'online': bool(p.get('Online')),
                        'from': 'tailscale'})
    return sorted(out, key=lambda h: (not h['online'], h['name']))


# --- this app's own package, for the machine there ---

PYPROJECT = '''[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "assieme"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["mcp>=2.3,<3"]

[project.scripts]
assieme = "assieme.cli:main"

[tool.setuptools]
packages = ["assieme"]

[tool.setuptools.package-data]
assieme = ["*", "**/*"]
'''


def _package_files(pkg=None):
    pkg = pkg or os.path.dirname(os.path.abspath(__file__))
    out = []
    for root, dirs, files in os.walk(pkg):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        out += [os.path.join(root, f) for f in sorted(files) if not f.endswith('.pyc')]
    return pkg, out


def package_tar(pkg=None):
    """(tar bytes, digest): the package as a project uv can install. The digest says whether the copy there is ours."""
    pkg, files = _package_files(pkg)
    digest, buf = hashlib.sha256(PYPROJECT.encode()), io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        def add(name, data):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        add('pyproject.toml', PYPROJECT.encode())
        for path in files:
            rel = os.path.relpath(path, pkg)
            with open(path, 'rb') as f:
                data = f.read()
            digest.update(rel.encode() + b'\0' + data)
            add(os.path.join('assieme', rel), data)
    return buf.getvalue(), digest.hexdigest()[:16]


def package_digest(pkg=None):
    return package_tar(pkg)[1]


INSTALL = '''set -e
d="$HOME/{home}"; rm -rf "$d/src"; mkdir -p "$d/src"; tar -xf "$d/assieme.tar" -C "$d/src"
uv=$(command -v uv || echo "$HOME/.local/bin/uv")
if [ ! -x "$uv" ]; then
  command -v curl >/dev/null || {{ echo "this machine needs curl to install uv" >&2; exit 3; }}
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >&2
  uv="$HOME/.local/bin/uv"
fi
"$uv" tool install --force --python 3.12 "$d/src" >&2
echo {digest} > "$d/installed"
'''


def install_assieme(rx, machine):
    """Copy this app's package there and install it with uv, in the home folder. Skipped when it's there already."""
    data, digest = package_tar()
    if rx.exec(machine, f'cat "$HOME/{HOME_DIR}/installed" 2>/dev/null || true')[1].strip() == digest:
        return
    rx.rt.call('file.write', on=machine, path=f'{HOME_DIR}/assieme.tar', data=base64.b64encode(data).decode())
    code, _, err = rx.exec(machine, INSTALL.format(home=HOME_DIR, digest=digest), timeout_s=600)
    if code != 0:
        last = (err.strip().splitlines() or ['no reason given'])[-1]
        raise RemoteError(f"Couldn't install Assieme on {machine}: {last}")


# --- addresses and ids in replies from there ---

def _prefix(v, m):
    """A reply from there, as seen from here: `t7` is `gpu-box/t7`, and so is a merge's undo id."""
    if isinstance(v, dict):
        return {k: (f'{m}/{x}' if k in ('address', 'undo') and isinstance(x, str) and x and '/' not in x
                    and not x.startswith('log:') else _prefix(x, m)) for k, x in v.items()}
    if isinstance(v, list):
        return [_prefix(x, m) for x in v]
    return v


def _strip(v, m):
    if isinstance(v, str):
        return v.removeprefix(f'{m}/')
    if isinstance(v, dict):
        return {k: _strip(x, m) for k, x in v.items()}
    if isinstance(v, list):
        return [_strip(x, m) for x in v]
    return v


class _Listed:
    """A runtime stand-in that lists given terminals, so `team.status` formats remote rows as it does local ones."""

    def __init__(self, terminals):
        self.terminals = terminals

    def list(self, labels, all=False):
        return self.terminals


def _failure(target, e):
    """What Test connection says when the machine can't be added. `result` is what the app shows."""
    why = str(e)
    if e.kind in ('hc_missing', 'incompatible', 'needs_update'):
        return {'result': 'not_set_up', 'target': target}
    if e.kind == 'needs_login':
        return {'result': 'needs_login', 'target': target}
    if 'IDENTIFICATION HAS CHANGED' in why:
        return {'result': 'host_key_changed', 'target': target}
    if 'Host key verification failed' in why:
        return {'result': 'new_host_key', 'target': target}
    return {'result': 'unreachable', 'target': target, 'why': why.split(': ', 1)[-1]}


class Remotes:
    def __init__(self, rt, store=None, install=install_assieme, thread=threading.Thread, clock=time.monotonic,
                 notify=live.notify, hc=None, opener=None, sleep=time.sleep):
        self.rt, self.install, self.thread, self.clock, self.notify, self.sleep = rt, install, thread, clock, notify, sleep
        self.store = store or os.path.join(quiet.state_dir(), 'remote.json')
        self.hc = hc or (lambda: shutil.which('hc') or 'hc')
        self.opener = opener or (lambda path: subprocess.run(['open', '-a', 'Terminal', path], capture_output=True, timeout=10))
        self.lock, self.jobs, self.last_tick, self.raw_at, self.starts = threading.Lock(), {}, None, None, set()
        self.last = {}  # (machine, method) -> its last answer, shown while it can't be reached
        try:
            with open(self.store) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = {}
        self.rows = saved.get('rows', {}) if isinstance(saved, dict) else {}       # machine -> its team rows
        self.set_up = set(saved.get('set_up', [])) if isinstance(saved, dict) else set()

    def _save(self):
        quiet._write_json(self.store, {'rows': self.rows, 'set_up': sorted(self.set_up)})

    # --- what hc knows ---

    def _raw(self):
        """The core's list with every machine's terminals, read at most once a second."""
        with self.lock:
            if self.raw_at is not None and self.clock() - self.raw_at[0] < 1:
                return self.raw_at[1]
        try:
            raw = self.rt.call('terminal.list', labels={}, all=True)
        except runtime.RuntimeError_:
            raw = {'terminals': [], 'machines': []}
        with self.lock:
            self.raw_at = (self.clock(), raw)
        return raw

    def machines(self):
        """The machines Assieme added, with their line: connecting, connected, needs_login or unreachable."""
        try:
            ms = self.rt.call('machine.list')['machines']
        except runtime.RuntimeError_:
            return []
        return [{'name': m['name'], 'target': m['target'], 'state': m.get('state', 'connecting'),
                 'since_ms': m.get('since_ms'), 'why': m.get('why')} for m in ms if m.get('session') == SESSION]

    def remote_rows(self):
        """{machine: (team rows, reachable)}: live rows from the core, the last seen ones while a line is down."""
        ms = {m['name']: m for m in self.machines()}
        if not ms:
            return {}
        terminals = [t for t in self._raw().get('terminals', []) if '/' in t.get('address', '')]
        out, changed = {}, False
        for name, m in ms.items():
            if m['state'] == 'connected':
                mine = [t for t in terminals if t['address'].split('/', 1)[0] == name]
                rows = [r | {'machine': name} for r in team.status(_Listed(mine))]
                if self.rows.get(name) != rows:
                    self.rows[name], changed = rows, True
                out[name] = (rows, True)
            else:
                out[name] = (self.rows.get(name, []), False)
        if changed:
            self._save()
        return out

    def _running(self):
        """Remote teams with agents, as `teams.list` lists them, each with its machine."""
        out = []
        for m, (rows, ok) in self.remote_rows().items():
            for name in dict.fromkeys(x['team'] for x in rows):
                n = sum(x['team'] == name and x['state'] == 'live' for x in rows)
                if n:
                    out.append({'name': name, 'kind': 'assieme', 'agents': n, 'machine': m, 'reachable': ok})
        return out

    def teams(self):
        """Team name -> the machine it runs on."""
        return {r['team']: m for m, (rows, _) in self.remote_rows().items() for r in rows}

    # --- calls there ---

    def exec(self, machine, line, timeout_s=CALL_S, stdin=b''):
        """(exit code, stdout, stderr) of a shell line there."""
        try:
            r = self.rt.call('machine.exec', on=machine, line=line, timeout=timeout_s,
                             **({'input': base64.b64encode(stdin).decode()} if stdin else {}))
        except runtime.RuntimeError_ as e:
            raise RemoteError(f"Can't reach {machine}: {e}") from None
        text = lambda k: base64.b64decode(r.get(k) or '').decode(errors='replace')
        if r.get('timed_out'):
            raise RemoteError(f'{machine} took too long to answer.')
        return r.get('code'), text('stdout'), text('stderr')

    def call(self, machine, method, params=None, timeout_s=None):
        """A brain method there, its reply as seen from here."""
        timeout_s = timeout_s or SLOW_S.get(method, CALL_S)
        code, out, err = self.exec(machine, f'exec {ASSIEME} call {shlex.quote(method)}', timeout_s,
                                   json.dumps(_strip(params or {}, machine)).encode() or b'{}')
        try:
            reply = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            last = (err.strip().splitlines() or [''])[-1]
            raise RemoteError(f"Assieme on {machine} didn't answer" + (f': {last}' if last else '.')) from None
        if 'error' in reply:
            raise RemoteError(reply['error'].get('message') or 'failed')
        return _prefix(reply.get('result'), machine)

    def _each(self, method, params=None, timeout_s=LIST_S):
        """{machine: answer} from every machine with a team, side by side; one that can't answer gives its last."""
        names = [m for m, (rows, _) in self.remote_rows().items() if rows]
        if not names:
            return {}

        def one(m):
            try:
                got = self.call(m, method, params, timeout_s)
                self.last[(m, method)] = got
                return m, got, True
            except RemoteError:
                return m, self.last.get((m, method)), False
        with ThreadPoolExecutor(len(names)) as pool:
            return {m: (got, ok) for m, got, ok in pool.map(one, names) if got is not None}

    # --- routing the app's calls ---

    def _where(self, params):
        """The machine a call is for, or None for this Mac. `machine` says so; a team or an address shows it."""
        m = params.pop('machine', None)
        if m:
            return m
        t = params.get('team')
        if isinstance(t, str) and t:
            where = self.teams().get(t)
            if where:
                return where
        heads = [v.split('/', 1)[0] for v in params.values() if isinstance(v, str) and '/' in v and not v.startswith('/')]
        if heads:
            known = {m['name'] for m in self.machines()}
            return next((h for h in heads if h in known), None)
        return None

    def _routed(self, name, fn):
        def handler(**params):
            m = self._where(params)
            if not m:
                return fn(**params)
            if name == 'team.start':
                if not params.get('name'):  # one name across every machine: a team is found by its name
                    taken = set(self.teams()) | {r['team'] for r in team.status(self.rt) if r['state'] == 'live'}
                    params['name'] = newteam.team_name(params.get('project') or 'team', taken)
                with self.lock:
                    self.starts.add(m)  # its progress shows from that brain's records
            key = (m, name, json.dumps(params, sort_keys=True))
            try:
                got = self.call(m, name, params)
            except RemoteError:
                if key not in self.last:
                    raise
                line = next((x for x in self.machines() if x['name'] == m), {'name': m, 'state': 'unreachable'})
                return self.last[key] | {'unreachable': line}  # what the team was doing when it was last seen
            if name in KEPT and isinstance(got, dict):
                self.last[key] = got
            return got
        return handler

    def _merges(self):
        def snapshot(fn):
            def h(**p):
                s = fn(**p)
                for m, (rows, ok) in self.remote_rows().items():
                    s['teams'] = s.get('teams', []) + [r | {'reachable': ok} for r in rows]
                s['machines'] = self.machines()
                s['starting'] = s.get('starting', []) + self._starting()
                return s
            return h

        def teams_list(fn):
            return lambda **p: (lambda r: r | {'teams': r['teams'] + self._running()})(fn(**p))

        def panel(r, got):
            for m, (x, ok) in got.items():
                x = x.get('panel', x)
                r['teams'] += [t | {'where': m, 'reachable': ok} for t in x.get('teams', [])]
                r['count'] += x.get('count', 0) if ok else 0
                if r.get('spend_tokens') is not None and x.get('spend_tokens') is not None:
                    r['spend_tokens'] += x['spend_tokens']
            r['teams'].sort(key=lambda t: -t['need'])
            return r

        def menu_state(fn):
            return lambda **p: panel(fn(**p), self._each('menu.state'))

        def menu_poll(fn):
            def h(**p):
                r, got = fn(**p), self._each('menu.poll')
                panel(r['panel'], got)
                r['count'] = r['panel']['count']
                for m, (x, ok) in got.items():
                    if ok:
                        r['events'] += [e | {'key': f"{m}|{e['key']}", 'machine': m} for e in x.get('events', [])]
                return r
            return h

        def listed(method, key=None, sort=None, newest_first=False):
            """A list across teams: each remote's rows added, marked with their machine. An id gets the machine in
            front, so the button that sends it back (Undo) goes there."""
            def wrap(fn):
                def h(**p):
                    r = fn(**p)
                    if p.get('team'):
                        return r
                    rows = r[key] if key else r
                    for m, (x, ok) in self._each(method, p).items():
                        rows += [y | {'machine': m} | ({'id': f"{m}/{y['id']}"} if y.get('id') else {})
                                 for y in (x[key] if key else x)]
                    if sort:
                        rows.sort(key=sort, reverse=newest_first)
                    return r
                return h
            return wrap

        def assistants_list(fn):  # what Claude or ChatGPT, connected to this Mac, sees running
            return lambda **p: (lambda r: r | {'running': r['running'] + self._running()})(fn(**p))

        return {'brain.snapshot': snapshot, 'teams.list': teams_list, 'menu.state': menu_state, 'menu.poll': menu_poll,
                'mcp.list_teams': assistants_list,
                'review.waiting': listed('review.waiting', sort=lambda w: (w['team'], w['role'])),
                'activity.list': listed('activity.list', 'rows', sort=lambda r: (r.get('day', ''), r.get('at', '')),
                                        newest_first=True),
                'mcp.needs_you': listed('mcp.needs_you', 'needs_you')}

    def wrap(self, handlers):
        """The brain's handlers, each going where its team lives, plus the machine handlers. Lists add remote rows."""
        out = {n: self._routed(n, fn) for n, fn in handlers.items()}
        for n, merge in self._merges().items():
            if n in out:
                out[n] = merge(out[n])
        return out | self.handlers()

    # --- starting a team there: its progress comes from that brain ---

    def _starting(self):
        """Teams starting on a machine, from that brain, until they have all finished starting."""
        with self.lock:
            pending = set(self.starts)
        out = []
        for m in pending:
            try:
                got = self.call(m, 'brain.snapshot', timeout_s=LIST_S).get('starting', [])
            except RemoteError:
                continue
            out += [r | {'machine': m} for r in got]
            if all(r['done'] for r in got):
                with self.lock:
                    self.starts.discard(m)
        return out

    # --- the machine handlers (here only) ---

    def check(self, machine):
        """A machine's line, and when it answers: whether Assieme there is current, and its Claude Code and Codex."""
        m = next((x for x in self.machines() if x['name'] == machine), None)
        if m is None:
            raise RemoteError(f'No machine named {machine}.')
        out = m | {'job': self._job(m['target'])}
        if m['state'] != 'connected':
            return out | {'ready': False}
        try:
            version = self.call(machine, 'remote.version', timeout_s=15)
            st = self.call(machine, 'setup.status', timeout_s=CALL_S)
        except RemoteError as e:
            return out | {'ready': False, 'problem': str(e)}
        return out | {'ready': True, 'outdated': version != package_digest(), 'git': st['git'], 'agents': st['agents']}

    def test(self, target):
        """Test connection: add the machine (hc dials it) and say what is there, in one of the `result`s."""
        target = (target or '').strip()
        if not TARGET.match(target) or target.startswith('-'):
            raise RemoteError('Type a host from your SSH config, or user@address.')
        known = next((m for m in self.machines() if m['target'] == target), None)
        if known:
            name = known['name']
            if known['state'] != 'connected':
                try:
                    self.rt.call('machine.connect', machine=name)
                except runtime.RuntimeError_ as e:
                    return _failure(target, e) | {'name': name}
        else:
            try:
                name = self.rt.call('machine.add', target=target, session=SESSION)['name']
            except runtime.RuntimeError_ as e:
                return _failure(target, e)
        c = self.check(name)
        if c['ready'] and name not in self.set_up:  # found ready (set up from another Mac): its brain is looked after too
            with self.lock:
                self.set_up.add(name)
            self._save()
        return c | {'result': 'ready' if c['ready'] else 'not_set_up' if c['state'] == 'connected' else c['state']}

    def _job(self, target):
        with self.lock:
            j = self.jobs.get('setup', {}).get(target)
            return json.loads(json.dumps(j)) if j else None

    def setup(self, target):
        """Set up Assieme there in the background: the runtime, Assieme itself, then its brain. Progress in `job`."""
        target = (target or '').strip()
        if not TARGET.match(target) or target.startswith('-'):
            raise RemoteError('Type a host from your SSH config, or user@address.')
        with self.lock:
            jobs = self.jobs.setdefault('setup', {})
            if jobs.get(target) and not jobs[target]['done']:
                return {'job': json.loads(json.dumps(jobs[target]))}
            job = jobs[target] = {'target': target, 'machine': None, 'done': False, 'error': None, 'steps': [
                {'id': 'runtime', 'state': 'waiting'}, {'id': 'assieme', 'state': 'waiting'},
                {'id': 'brain', 'state': 'waiting'}]}
        self.thread(target=self._setup, args=(job,), daemon=True).start()
        return {'job': self._job(target)}

    def _setup(self, job):
        steps = {'runtime': lambda: self._add_installing(job), 'assieme': lambda: self.install(self, job['machine']),
                 'brain': lambda: self.start_brain(job['machine'], restart=True)}
        try:
            for s in job['steps']:
                with self.lock:
                    s['state'] = 'doing'
                steps[s['id']]()
                with self.lock:
                    s['state'] = 'done'
            with self.lock:
                self.set_up.add(job['machine'])
            self._save()
        except Exception as e:  # shown on the sheet, with Try again
            with self.lock:
                for s in job['steps']:
                    if s['state'] == 'doing':
                        s['state'] = 'failed'
                job['error'] = str(e)
        finally:
            with self.lock:
                job['done'] = True

    def _hc_cli(self, *args):
        env = runtime.clean_env(os.environ) | {'HC_SOCKET': self.rt.sock, 'HC_SESSION': self.rt.session}
        r = subprocess.run([self.hc(), *args, '--json'], capture_output=True, text=True, env=env, timeout=600)
        try:
            out = json.loads((r.stdout.strip() or r.stderr.strip()).splitlines()[-1])
        except (ValueError, IndexError):
            raise RemoteError((r.stderr or r.stdout).strip() or 'hc failed') from None
        if r.returncode != 0 or 'error' in out:
            e = out.get('error') or {}
            raise RemoteError(e.get('message') or 'hc failed')
        return out

    def _add_installing(self, job):
        """The machine, added with hc on it: hc copies itself there (same OS and CPU) or downloads its own release."""
        known = next((m for m in self.machines() if m['target'] == job['target']), None)
        if known and known['state'] == 'needs_update':
            self._hc_cli('machine', 'update', known['name'])
        elif not known:
            known = self._hc_cli('machine', 'add', job['target'], '--session', SESSION, '--install')
        with self.lock:
            job['machine'] = known['name']

    def _brains(self, machine):
        return [t for t in self._fresh_raw().get('terminals', []) if t.get('address', '').startswith(machine + '/')
                and (t.get('labels') or {}).get(SERVICE) == BRAIN and t.get('state') == 'live']

    def _fresh_raw(self):
        with self.lock:
            self.raw_at = None
        return self._raw()

    def start_brain(self, machine, restart=False):
        """Assieme's brain there, in a terminal of that machine's core, answering before this returns."""
        live_ = self._brains(machine)
        if live_ and not restart:
            return
        for t in live_:
            self.rt.kill(t['address'])
        # It finds its core through the variable hc gives every program in its terminals.
        self.rt.call('terminal.run', on=machine, name=BRAIN_NAME, labels={SERVICE: BRAIN}, wait=False, no_parent=True,
                     line=f'ASSIEME_HC_SOCKET="$HC_TERMINAL_SOCKET" exec {ASSIEME} serve')
        deadline = self.clock() + BRAIN_WAIT_S
        while True:
            try:
                self.call(machine, 'settings.get', timeout_s=5)
                return
            except RemoteError as e:
                if self.clock() >= deadline:
                    raise RemoteError(f"Assieme started on {machine} but doesn't answer: {e}") from None
                self.sleep(1)

    def connect(self, machine):
        try:
            return self.rt.call('machine.connect', machine=machine)
        except runtime.RuntimeError_ as e:
            raise RemoteError(str(e)) from None

    def forget(self, machine):
        """This Mac stops showing it. Its agents keep running there."""
        self.rt.call('machine.rm', machine=machine)
        self.rows.pop(machine, None)
        self.set_up.discard(machine)
        self._save()
        return {'ok': True}

    def open_terminal(self, target=None, machine=None):
        """Terminal, on `ssh <target>` (a password, a new host key) or on hc's login for a machine already added.
        The person answers ssh there; Assieme sees none of it."""
        if machine:
            m = next((x for x in self.machines() if x['name'] == machine), None)
            if m is None:
                raise RemoteError(f'No machine named {machine}.')
            line = (f'HC_SOCKET={shlex.quote(self.rt.sock)} HC_SESSION={shlex.quote(self.rt.session)} '
                    f'exec {shlex.quote(self.hc())} machine login {shlex.quote(machine)}')
        else:
            target = (target or '').strip()
            if not TARGET.match(target) or target.startswith('-'):
                raise RemoteError('Type a host from your SSH config, or user@address.')
            line = f'ssh -- {shlex.quote(target)} exit && echo "Logged in. You can close this window and go back to Assieme."'
        path = os.path.join(quiet.state_dir(), 'ssh-login.command')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(f'#!/bin/sh\n{line}\n')
        os.chmod(path, 0o700)
        self.opener(path)
        return {'ok': True}

    def machines_view(self):
        return [m | {'job': self._job(m['target']), 'set_up': m['name'] in self.set_up} for m in self.machines()]

    def handlers(self):
        return {'remote.hosts': lambda: {'ssh': ssh_hosts(), 'tailscale': tailscale_hosts(), 'machines': self.machines_view()},
                'remote.machines': self.machines_view,
                'remote.check': self.check,
                'remote.test': self.test,
                'remote.setup': self.setup,
                'remote.job': lambda target: {'job': self._job(target)},
                'remote.connect': self.connect,
                'remote.forget': self.forget,
                'remote.terminal': self.open_terminal}

    # --- the brain's tick ---

    def tick(self, watching=True):
        """After the Mac slept, dial every machine at once instead of at hc's next retry. A machine Assieme set up
        whose brain is gone (the machine restarted) gets it back. With the app closed, remote news notifies here."""
        now = self.clock()
        slept = self.last_tick is not None and now - self.last_tick > WOKE_S
        self.last_tick = now
        for m in self.machines():
            if m['state'] != 'connected':
                if slept:
                    self.thread(target=lambda n=m['name']: self._quietly(self.connect, n), daemon=True).start()
                continue
            busy = (self._job(m['target']) or {'done': True})['done'] is False
            if m['name'] in self.set_up and not busy and not self._brains(m['name']):
                self._quietly(self.start_brain, m['name'])
        if not watching:
            for m, (x, ok) in self._each('menu.poll').items():
                for e in x.get('events', []) if ok else []:
                    self.notify(e['title'], e['body'])

    @staticmethod
    def _quietly(fn, *args):
        try:
            fn(*args)
        except (RemoteError, runtime.RuntimeError_):
            pass  # the next tick tries again


# --- there: what the Mac's brain asks for ---

def repos(home=None, depth=3):
    """Git repositories in the home folder here, for New team's project field when this is the remote."""
    home = home or os.path.expanduser('~')
    out = []
    for root, dirs, _ in os.walk(home):
        if '.git' in dirs:
            out.append(root)
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in ('node_modules', 'Library'))
        if root[len(home):].count(os.sep) >= depth - 1:
            dirs[:] = []
    return sorted('~' + r[len(home):] if r.startswith(home) else r for r in out)


def remote_handlers():
    return {'remote.version': package_digest, 'remote.repos': repos}


def brain_socket_path():
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'assieme', 'brain.sock')


def call_main(method, stdin=None, stdout=None, sock=None):
    """`assieme call <method>`: one request to this machine's brain, the parameters as JSON on stdin, the reply on
    stdout. The Mac's brain reaches a remote brain this way, over hc's line."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        raw = stdin.read()
        params = json.loads(raw) if raw.strip() else {}
        s = socket.socket(socket.AF_UNIX)
        s.connect(sock or brain_socket_path())
        with s, s.makefile('rwb') as f:
            f.write((json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}) + '\n').encode())
            f.flush()
            line = f.readline().decode()
    except OSError:
        line = json.dumps({'error': {'message': "Assieme isn't running on this machine."}})
    except ValueError:
        line = json.dumps({'error': {'message': 'The parameters were not JSON.'}})
    stdout.write(line.rstrip('\n') + '\n')
    return 0
