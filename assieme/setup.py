"""First-run setup: Apple's developer tools, where the agents are installed, and whether they are signed in.

Must not import `launch`: launch uses `agent_path` as its default lookup.
"""
import os
import shutil
import subprocess
import time

# Exit code 1 is what both status commands give when signed out (measured); anything else unclear counts as signed in.
STATUS = {'claude': ['auth', 'status'], 'codex': ['login', 'status']}
TIMEOUT_S = 5
LOGIN_PATH_TTL_S = 60
_login_cache = {}  # login_path function -> (read at, value)


def git_ready(which=shutil.which, run=subprocess.run):
    """Git works. /usr/bin/git is Apple's stub, which opens an installer dialog if run, so it is only asked about."""
    found = which('git')
    if found and found != '/usr/bin/git':
        return True
    try:
        return run(['xcode-select', '-p'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT_S).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _login_path():
    """The PATH the person's login shell has; an app opened from Finder only has the short system one."""
    try:
        r = subprocess.run([os.environ.get('SHELL') or '/bin/zsh', '-lc', 'echo $PATH'], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=TIMEOUT_S)
        return r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def agent_path(name, which=shutil.which, login_path=_login_path, clock=time.monotonic):
    """Where an agent program is installed, or None: this process's PATH, the login shell's, then ~/.local/bin."""
    if found := which(name):
        return found
    at, value = _login_cache.get(login_path, (None, ''))
    if at is None or clock() - at >= LOGIN_PATH_TTL_S:
        at, value = clock(), login_path()
        _login_cache[login_path] = (at, value)
    for d in [*value.split(':'), os.path.expanduser('~/.local/bin')]:
        p = os.path.join(d, name)
        if d and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _signed_in(path, name, run):
    try:
        return run([path, *STATUS[name]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT_S).returncode != 1
    except (OSError, subprocess.TimeoutExpired):
        return True


def status(run=subprocess.run):
    agents = {}
    for name in STATUS:
        path = agent_path(name)
        agents[name] = {'installed': bool(path), 'signed_in': bool(path) and _signed_in(path, name, run)}
    return {'git': git_ready(), 'agents': agents}


def install_tools(run=subprocess.run):
    """Open Apple's own installer for the command line developer tools."""
    run(['xcode-select', '--install'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT_S)
    return {'ok': True}


def handlers(rt):
    return {'setup.status': status, 'setup.install_tools': install_tools}
