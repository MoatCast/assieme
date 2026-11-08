"""The one Connect button: adds `assieme mcp` to every installed client, using each client's own command where it has
one. The Claude app has only a config file: one that doesn't parse is left alone and reported. While the app is open it
rewrites that file from memory within seconds, dropping anything added (seen on 8 November 2026), so it is only written
with the app closed; until then the client is `blocked`."""
import json
import os
import shutil
import subprocess
import sys

from . import quiet

NAME = 'assieme'
TIMEOUT_S = 30
LABELS = {'claude': 'Claude Code', 'codex': 'Codex', 'desktop': 'the Claude app'}


def command():
    return [sys.executable, '-P', '-m', 'assieme', 'mcp']  # -P: never import from a worktree


def desktop_config(home=None):
    return os.path.join(home or os.path.expanduser('~'), 'Library', 'Application Support', 'Claude', 'claude_desktop_config.json')


def _load(path):
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError('not a settings object')
    return data


def _desktop_open(run):
    try:
        return run(['pgrep', '-x', 'Claude'], capture_output=True, text=True, timeout=TIMEOUT_S).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _desktop_add(path):
    try:
        data = _load(path)
    except FileNotFoundError:
        data = {}
    cmd = command()
    data.setdefault('mcpServers', {})[NAME] = {'command': cmd[0], 'args': cmd[1:]}
    quiet._write_json(path, data)


def _desktop_remove(path):
    data = _load(path)
    if NAME in data.get('mcpServers', {}):
        del data['mcpServers'][NAME]
        quiet._write_json(path, data)


def _desktop_has(path):
    try:
        return NAME in _load(path).get('mcpServers', {})
    except (OSError, ValueError, AttributeError):
        return False


def installed(which=shutil.which, home=None):
    found = [c for c in ('claude', 'codex') if which(c)]
    return found + (['desktop'] if os.path.isdir(os.path.dirname(desktop_config(home))) else [])


def _cli(args, run):
    r = run(args, capture_output=True, text=True, timeout=TIMEOUT_S)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[:200] or 'it failed')


def _has(client, run, home):
    if client == 'desktop':
        return _desktop_has(desktop_config(home))
    try:
        return run([client, 'mcp', 'get', NAME], capture_output=True, text=True, timeout=TIMEOUT_S).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def status(run=subprocess.run, which=shutil.which, home=None):
    """Each installed client, and whether the server is in it."""
    out = [{'id': c, 'label': LABELS[c], 'connected': _has(c, run, home)} for c in installed(which, home)]
    for c in out:
        if c['id'] == 'desktop' and not c['connected'] and _desktop_open(run):
            c['blocked'] = True
    return out


def _each(action, run, which, home):
    problems = []
    for c in installed(which, home):
        try:
            action(c)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
            problems.append(f"{LABELS[c]}: {e}")
    return {'clients': status(run, which, home), 'problems': problems}


def connect(run=subprocess.run, which=shutil.which, home=None):
    def add(c):
        if c == 'desktop':
            return None if _desktop_open(run) else _desktop_add(desktop_config(home))
        if _has(c, run, home):  # an older copy of the app may have left another path behind
            _cli([c, 'mcp', 'remove', NAME], run)
        scope = ['--scope', 'user'] if c == 'claude' else []
        _cli([c, 'mcp', 'add', *scope, NAME, '--', *command()], run)
    return _each(add, run, which, home)


def disconnect(run=subprocess.run, which=shutil.which, home=None):
    def remove(c):
        if c == 'desktop':
            return _desktop_remove(desktop_config(home))
        if _has(c, run, home):
            _cli([c, 'mcp', 'remove', *(['--scope', 'user'] if c == 'claude' else []), NAME], run)
    return _each(remove, run, which, home)


def handlers():
    return {'assistants.status': lambda: {'clients': status(), 'problems': []}, 'assistants.connect': connect, 'assistants.disconnect': disconnect}
