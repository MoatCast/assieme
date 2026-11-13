"""An agent in a container that sees only its own copy of the project, with Docker.

Assieme never installs either. The copy, Assieme's own code and the agent's settings file are mounted at the same
paths they have on the Mac, so the agent's command line is the one `launch` builds. Its home folder is a volume per
kind and project, so it signs in once per project; with share_signin, that kind's sign-in files from the Mac are
mounted read-only.
"""
import importlib.metadata
import os
import subprocess

from . import setup

# Apple's `container` isn't offered until it has been tried: its containers reach the Mac on an address the bridge
# doesn't listen on.
TOOLS = ('docker',)
HOSTS = {'docker': 'host.docker.internal'}  # how a container reaches this Mac
IMAGE = 'assieme-agent-{kind}:1'
RECIPE = os.path.join(os.path.dirname(__file__), 'container')
PACKAGE = os.path.dirname(os.path.abspath(__file__))
HOME = '/home/node'
# The sign-in files that can be shared, each read-only. Claude Code and Cursor keep theirs in the Mac's Keychain:
# they sign in inside. Read-only: a sign-in that needs renewing asks again inside the container.
SIGNIN = {'codex': ['.codex/auth.json'], 'gemini': ['.gemini/oauth_creds.json', '.gemini/google_accounts.json'],
          'opencode': ['.local/share/opencode/auth.json']}
TIMEOUT_S = 5
NOT_RUNNING = 'Docker is on this Mac but not running.'
NONE = "Containers need Docker, and it isn't on this Mac."
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
        return NOT_RUNNING
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


def _project_key(path):
    """The project an agent's copy belongs to (<project>/.assieme/worktrees/<role>), as a short name for its volume."""
    import hashlib
    project = path.split(os.sep + '.assieme' + os.sep + 'worktrees' + os.sep)[0]
    return hashlib.sha256(os.path.realpath(project).encode()).hexdigest()[:12]


def run_argv(role, inner, env, path, terminal, bridge=None, which=setup.agent_path, home=None):
    """`docker run` around the agent's own command line. `inner` and `env` are what `launch` built for it."""
    t = tool(which)
    if not t:
        raise ContainerError(NONE)
    name, prog = t
    home = home or os.path.expanduser('~')
    a = [prog, 'run', '--rm', '-it', '--init', '--name', f'assieme-{terminal}',
         '-v', f'{path}:{path}', '-w', path, '-v', f'{PACKAGE}:{PACKAGE}:ro',
         '-v', f'assieme-home-{role.agent}-{_project_key(path)}:{HOME}']  # one per project: no agent reads another's
    if role.share_signin:
        for rel in SIGNIN.get(role.agent, []):
            mine = os.path.join(home, rel)
            if os.path.isfile(mine):
                a += ['-v', f'{mine}:{HOME}/{rel}:ro']
    env = {**env, 'PYTHONPATH': os.path.dirname(PACKAGE)}
    if bridge:  # 'port/ticket'
        env['ASSIEME_BRIDGE'] = f'{HOSTS[name]}:{bridge}'
    for k, v in env.items():
        a += ['-e', f'{k}={v}']
    return a + [IMAGE.format(kind=role.agent), *inner]
