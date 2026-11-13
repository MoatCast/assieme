"""Setup, run and archive scripts a team brought over from another tool (step 30).

Each script runs only after the person has said yes to that exact line for that team, in its agent's copy, with the
same clean environment as the live preview."""
import json
import os
import re
import subprocess

from . import runtime

WHICH = ('setup', 'run', 'archive')
VAR = re.compile(r'\$(?:\{(CONDUCTOR_[A-Z_]+)\}|(CONDUCTOR_[A-Z_]+))')
LOCAL = {'HOST': '127.0.0.1', 'HOSTNAME': '127.0.0.1'}  # as the preview: this Mac only


def rewrite(line):
    """(line, unknown): Conductor's variables as Assieme's, still as variables, so quoting in the line keeps working:
    the port is $PORT, the agent's folder is $PWD (scripts run in it), and the project folder stays
    $CONDUCTOR_ROOT_PATH, which `env` sets. Variables Assieme has no match for are kept as they are and named."""
    known = {'CONDUCTOR_PORT': '${PORT}', 'CONDUCTOR_WORKSPACE_PATH': '${PWD}', 'CONDUCTOR_ROOT_PATH': '${CONDUCTOR_ROOT_PATH}'}
    unknown = []

    def swap(m):
        name = m.group(1) or m.group(2)
        if name in known:
            return known[name]
        if name not in unknown:
            unknown.append(name)
        return m.group(0)
    return VAR.sub(swap, line), unknown


def _file():
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'assieme', 'scripts-ok.json')


def _load():
    try:
        with open(_file()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def allowed(team, which, line):
    return _load().get(team, {}).get(which) == line


def allow(team, which, line):
    """The person said yes to running this exact line for this team."""
    if which not in WHICH:
        raise ValueError(f'Unknown script {which!r}.')
    data = _load()
    data.setdefault(team, {})[which] = line
    os.makedirs(os.path.dirname(_file()), exist_ok=True)
    tmp = _file() + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, _file())


def env(project):
    """What a brought-over script expects beside Assieme's own: the project folder under Conductor's name."""
    return {'CONDUCTOR_ROOT_PATH': project}


def run(line, folder, project, port=None, timeout=300):
    """{ok, output}: the line, rewritten, run with /bin/sh in `folder`; its output kept (the last 4000 characters)."""
    line = rewrite(line)[0]
    variables = runtime.clean_env(os.environ) | LOCAL | env(project) | ({'PORT': str(port)} if port else {})
    try:
        r = subprocess.run(['/bin/sh', '-c', line], cwd=folder, env=variables, stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=timeout, encoding='utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        return {'ok': False, 'output': f'It took longer than {timeout:g} seconds, so it was stopped.'}
    except OSError as e:
        return {'ok': False, 'output': str(e)}
    return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
