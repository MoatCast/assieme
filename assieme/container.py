"""An agent in a container that sees only its own copy of the project: Docker, else Apple's `container`.

Assieme never installs either. The copy, Assieme's own code and the agent's settings file are mounted at the same
paths they have on the Mac, so the agent's command line is the one `launch` builds. Its home folder is a volume per
kind, so it signs in once; with share_signin, that kind's sign-in folder from the Mac is mounted over it.
"""
import importlib.metadata
import os
import subprocess

from . import setup

TOOLS = ('docker', 'container')
HOSTS = {'docker': 'host.docker.internal', 'container': '192.168.64.1'}  # how a container reaches this Mac
IMAGE = 'assieme-agent-{kind}:1'
RECIPE = os.path.join(os.path.dirname(__file__), 'container')
PACKAGE = os.path.dirname(os.path.abspath(__file__))
HOME = '/home/node'
# Sign-in folders that can be shared. Claude Code keeps its sign-in in the Mac's Keychain: it signs in inside.
SIGNIN = {'codex': '.codex', 'gemini': '.gemini', 'opencode': '.local/share/opencode', 'cursor': '.cursor'}
TIMEOUT_S = 5
NOT_RUNNING = 'Docker is on this Mac but not running.'
NONE = "Containers need Docker or Apple's container tool, and neither is on this Mac."
NO_IMAGE = 'The container for {name} is not built yet. Choose Download and build in Add agent.'


class ContainerError(RuntimeError):
    pass


def tool(which=setup.agent_path):
    """(name, path) of the container tool on this Mac, or None."""
    for name in TOOLS:
        path = which(name)
        if path:
            return name, path
    return None


def _ok(argv, run):
    try:
        return run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT_S).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def state(kind, which=setup.agent_path, run=subprocess.run):
    """What Add agent shows under "A container": {'tool', 'running', 'image_ready'}."""
    t = tool(which)
    if not t:
        return {'tool': None, 'running': False, 'image_ready': False}
    name, path = t
    running = _ok([path, 'info'] if name == 'docker' else [path, 'system', 'status'], run)
    ready = running and _ok([path, 'image', 'inspect', IMAGE.format(kind=kind)], run)
    return {'tool': name, 'running': running, 'image_ready': ready}


def problem(kind, which=setup.agent_path, run=subprocess.run):
    """Why an agent of this kind can't start in a container now, in Paper's words, or None."""
    s = state(kind, which, run)
    if not s['tool']:
        return NONE
    if not s['running']:
        return NOT_RUNNING if s['tool'] == 'docker' else "Apple's container tool is on this Mac but not running."
    if not s['image_ready']:
        from . import launch
        return NO_IMAGE.format(name=launch.NAMES.get(kind, kind))
    return None


def build_command(kind, which=setup.agent_path):
    """The shell command that builds this kind's image (shown and run in a setup terminal)."""
    t = tool(which)
    if not t:
        raise ContainerError(NONE)
    import shlex
    return ' '.join(shlex.quote(w) for w in [t[1], 'build', '-t', IMAGE.format(kind=kind), '--build-arg', f'KIND={kind}',
                                             '--build-arg', f"MCP_VERSION={importlib.metadata.version('mcp')}", RECIPE])


def run_argv(role, inner, env, path, terminal, bridge=None, which=setup.agent_path, home=None):
    """`docker run` around the agent's own command line. `inner` and `env` are what `launch` built for it."""
    t = tool(which)
    if not t:
        raise ContainerError(NONE)
    name, prog = t
    home = home or os.path.expanduser('~')
    a = [prog, 'run', '--rm', '-it', '--init', '--name', f'assieme-{terminal}',
         '-v', f'{path}:{path}', '-w', path, '-v', f'{PACKAGE}:{PACKAGE}:ro',
         '-v', f'assieme-home-{role.agent}:{HOME}']
    state_dir = os.path.join(os.environ.get('XDG_STATE_HOME') or os.path.join(home, '.local', 'state'), 'assieme',
                             role.agent)
    if os.path.isdir(state_dir):  # Gemini's settings file lives here
        a += ['-v', f'{state_dir}:{state_dir}:ro']
    if role.share_signin and role.agent in SIGNIN:
        mine = os.path.join(home, SIGNIN[role.agent])
        os.makedirs(mine, exist_ok=True)
        a += ['-v', f'{mine}:{HOME}/{SIGNIN[role.agent]}']
    env = {**env, 'PYTHONPATH': os.path.dirname(PACKAGE)}
    if bridge:  # 'port/ticket'
        env['ASSIEME_BRIDGE'] = f'{HOSTS[name]}:{bridge}'
    for k, v in env.items():
        a += ['-e', f'{k}={v}']
    return a + [IMAGE.format(kind=role.agent), *inner]
