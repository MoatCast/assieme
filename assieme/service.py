"""The brain as a launchd user agent (macOS): `assieme service install|uninstall|status`.

launchd starts `assieme serve` at login and restarts it if it stops, so the brain keeps watching agents with the
app closed. Its environment is a short, clean list: no agent-session markers from the shell that installed it.
"""
import os
import plistlib
import subprocess
import sys

from . import runtime

LABEL = 'dev.assieme.brain'
_KEEP = ('PATH', 'HOME', 'USER', 'LANG', 'XDG_STATE_HOME')


def plist_path():
    return os.path.join(os.environ.get('HOME', os.path.expanduser('~')), 'Library', 'LaunchAgents', f'{LABEL}.plist')


def _target():
    return f'gui/{os.getuid()}'


def plist(python, env):
    env = {k: v for k, v in runtime.clean_env(env).items() if k in _KEEP}
    state = env.get('XDG_STATE_HOME') or os.path.join(env.get('HOME', os.path.expanduser('~')), '.local', 'state')
    log = os.path.join(state, 'assieme', 'brain.log')
    return plistlib.dumps({'Label': LABEL, 'ProgramArguments': [python, '-P', '-m', 'assieme', 'serve'],
                           'KeepAlive': True, 'RunAtLoad': True, 'EnvironmentVariables': env,
                           'StandardOutPath': log, 'StandardErrorPath': log})


def install(run=subprocess.run, python=None):
    path = plist_path()
    data = plist(python or sys.executable, dict(os.environ))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.makedirs(os.path.dirname(plistlib.loads(data)['StandardOutPath']), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)
    run(['launchctl', 'bootout', f'{_target()}/{LABEL}'], capture_output=True)  # not loaded yet is fine
    r = run(['launchctl', 'bootstrap', _target(), path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'launchctl bootstrap failed ({r.returncode}): {(r.stderr or "").strip()}')
    return path


def uninstall(run=subprocess.run):
    run(['launchctl', 'bootout', f'{_target()}/{LABEL}'], capture_output=True)
    if os.path.exists(plist_path()):
        os.remove(plist_path())


def running(run=subprocess.run):
    return run(['launchctl', 'print', f'{_target()}/{LABEL}'], capture_output=True).returncode == 0
