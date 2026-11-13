"""First-run setup: Apple's developer tools, where the agents are installed, and whether they are signed in.

Must not import `launch`: launch uses `agent_path` as its default lookup.
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

from . import runtime

# Exit code 1 is what both status commands give when signed out (measured); anything else unclear counts as signed in.
STATUS = {'claude': ['auth', 'status'], 'codex': ['login', 'status'], 'gemini': None, 'opencode': ['auth', 'list'],
          'cursor': ['status']}
PROGRAM = {'cursor': 'cursor-agent'}  # the others' program is their kind
# Exit 0 either way: their words say it. Read in memory and dropped (they can name the account).
SIGNED_OUT = {'opencode': re.compile(r'\b0 credentials'), 'cursor': re.compile(r'not logged in', re.I)}
GEMINI_SIGNIN = ('~/.gemini/oauth_creds.json', '~/.gemini/google_accounts.json')  # only whether they exist
TIMEOUT_S = 5
LOGIN_PATH_TTL_S = 60
_login_cache = {}  # login_path function -> (read at, value)


def git_ready(which=shutil.which, run=subprocess.run):
    """Git works. /usr/bin/git is Apple's stub, which opens an installer dialog if run, so it is only asked about."""
    found = which('git')
    if found and (found != '/usr/bin/git' or sys.platform != 'darwin'):  # Linux's /usr/bin/git is git itself
        return True
    try:
        return run(['xcode-select', '-p'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT_S).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


PATH_MARK = '__assieme_path__='


def _login_path():
    """The PATH the person's login shell has; an app opened from Finder only has the short system one."""
    # -i too: nvm, where npm puts Claude Code and Codex, loads in .zshrc. A marker, since rc files may print banners.
    try:
        r = subprocess.run([os.environ.get('SHELL') or '/bin/zsh', '-ilc', f'echo {PATH_MARK}$PATH'],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return ''
    lines = [ln.split(PATH_MARK, 1)[1] for ln in r.stdout.splitlines() if PATH_MARK in ln]
    return lines[-1].strip() if lines else ''


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
    if STATUS[name] is None:  # Gemini CLI has no status command
        return any(os.path.exists(os.path.expanduser(f)) for f in GEMINI_SIGNIN)
    try:
        if name in SIGNED_OUT:
            r = run([path, *STATUS[name]], capture_output=True, text=True, timeout=TIMEOUT_S, env={**os.environ, 'NO_COLOR': '1'})
            return not SIGNED_OUT[name].search((r.stdout or '') + (r.stderr or ''))
        return run([path, *STATUS[name]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT_S).returncode != 1
    except (OSError, subprocess.TimeoutExpired):
        return True


def status(run=subprocess.run):
    paths = {name: agent_path(PROGRAM.get(name, name)) for name in STATUS}
    with ThreadPoolExecutor(len(STATUS)) as pool:  # side by side, so two hanging commands still answer in one timeout
        signed = {n: pool.submit(_signed_in, p, n, run) if p else None for n, p in paths.items()}
    agents = {n: {'installed': bool(paths[n]), 'signed_in': bool(f and f.result())} for n, f in signed.items()}
    return {'git': git_ready(), 'agents': agents}


def install_tools(run=subprocess.run):
    """Open Apple's own installer for the command line developer tools."""
    run(['xcode-select', '--install'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT_S)
    return {'ok': True}


class SetupError(RuntimeError):
    """A failed setup call; the serve loop sends str(e) to the app, so it holds git's own words."""


class TooLarge(SetupError):
    pass


class Unfinished(SetupError):
    pass


# What Set up this folder leaves out of the first version, by name.
EXCLUDE = ('.env', '.env.*', '*.pem', '*.key', '*.p12', 'id_rsa*', 'id_ed25519*', 'credentials*', 'secrets*',
           'node_modules', '.venv', 'venv', '__pycache__', '.DS_Store')
MAX_FILES = 20000
LIST_TIMEOUT_S = 15
SAVE_TIMEOUT_S = 110  # add and commit may run the person's hooks; just under the app's own 120 s
# What git leaves in its folder while a merge or similar is unfinished.
UNFINISHED = ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'BISECT_LOG', 'rebase-merge', 'rebase-apply')
ASSIEME_DIR = '.assieme'
FIRST_MESSAGE = 'First version, saved by Assieme'
BEFORE_TEAM_MESSAGE = 'Saved by Assieme before starting a team'


def too_broad(project):
    """The home folder or `/`, by resolved path, so `~/`, a trailing slash and a symlink all count."""
    real = os.path.realpath(os.path.expanduser(project))
    return real in ('/', os.path.realpath(os.path.expanduser('~')))


def _git(cwd, *args, timeout=LIST_TIMEOUT_S, stdin=None):
    """Runs git; returns (stdout bytes, stderr text). Optional locks off, so a read never refreshes the index."""
    try:
        r = subprocess.run(['git', *args], cwd=cwd, capture_output=True, timeout=timeout, input=stdin,
                           env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
    except subprocess.TimeoutExpired:
        raise TooLarge('Listing this folder took too long.')
    except OSError as e:
        raise SetupError(str(e))
    if r.returncode != 0:
        raise SetupError(r.stderr.decode('utf-8', 'replace').strip() or f'git {args[0]} failed.')
    return r.stdout, r.stderr


def _names(out):
    return [p.decode('utf-8', 'replace') for p in out.split(b'\0') if p]


def _is_repo(project):
    try:
        _git(project, 'rev-parse', '--show-toplevel')
        return True
    except TooLarge:
        raise
    except SetupError:
        return False


def _has_commits(project):
    try:
        _git(project, 'rev-parse', '--verify', '--quiet', 'HEAD')
        return True
    except TooLarge:
        raise
    except SetupError:
        return False


def _repo_state(project):
    """(git folder, this folder's path inside the repository, ending in `/` or empty)."""
    gitdir, prefix = _git(project, 'rev-parse', '--absolute-git-dir', '--show-prefix')[0].decode().split('\n')[:2]
    return gitdir, prefix


def changed_files(project, untracked='all'):
    """Paths `git status --porcelain -z` reports inside this folder, relative to it, leaving out .assieme/."""
    prefix = _repo_state(project)[1]
    entries = _names(_git(project, 'status', '--porcelain', '-z', f'--untracked-files={untracked}', '--', '.')[0])
    paths, skip = [], False
    for e in entries:
        if skip:  # the old name of a rename or copy
            skip = False
            continue
        skip = e[0] in 'RC'
        paths.append(e[3:].removeprefix(prefix))  # status paths start at the repository's root
    return [p for p in paths if p != ASSIEME_DIR and not p.startswith(ASSIEME_DIR + '/')]


def _plan(project):
    """The one list of what a save commits, so the preview, the warning and the save cannot disagree.
    Reads only: not even a .git, index or exclude file is written."""
    if too_broad(project):
        raise TooLarge('This folder is too large.')
    in_repo = _is_repo(project)
    if in_repo:
        gitdir = _repo_state(project)[0]
        if any(os.path.exists(os.path.join(gitdir, m)) for m in UNFINISHED) or _git(project, 'ls-files', '-u')[0]:
            raise Unfinished('A git merge or similar is in progress in this folder. Finish it first, then come back.')
    x = [a for p in EXCLUDE for a in ('-x', p)]
    with tempfile.TemporaryDirectory() as tmp:
        if in_repo:
            where = []
            # Tracked files stay tracked whatever the exclusions say, so their changes are files too.
            tracked = changed_files(project, 'no')
        else:
            where = [f'--git-dir={tmp}/.git', f'--work-tree={os.path.abspath(project)}']  # a throwaway repository
            _git(tmp, 'init', '-q', tmp)
            tracked = []
        kept = _names(_git(project, *where, 'ls-files', '-o', '--exclude-standard', *x, '-x', ASSIEME_DIR + '/', '-z')[0])
        left = _names(_git(project, *where, 'ls-files', '-o', '-i', '--directory', *x, '-z')[0])
    files = sorted(set(tracked) | set(kept))
    if len(files) > MAX_FILES:
        raise TooLarge('This folder has too many files.')
    return {'files': files, 'left_out': sorted(p.rstrip('/') for p in left),
            'first': not in_repo or not _has_commits(project)}


def folder_preview(project):
    plan = _plan(os.path.expanduser(project))
    files = plan['files']
    return {'files': len(files), 'sample': files[:500], 'left_out': plan['left_out'], 'empty': not files, 'first': plan['first']}


def uncommitted(project):
    """How many files Save them first would commit."""
    return len(_plan(project)['files'])


def _add_exclusions(project):
    path = _git(project, 'rev-parse', '--git-path', 'info/exclude')[0].decode().strip()
    path = path if os.path.isabs(path) else os.path.join(project, path)
    have = open(path).read().splitlines() if os.path.exists(path) else []
    missing = [p for p in (*EXCLUDE, ASSIEME_DIR + '/') if p not in have]
    if missing:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a') as f:
            f.write(('\n' if have and have[-1] else '') + '\n'.join(missing) + '\n')


def folder_save(project):
    """git init if needed, the exclusions, stage exactly the previewed files, one commit. Safe to repeat after a failure."""
    project = os.path.expanduser(project)
    plan = _plan(project)  # refuses a too-broad folder even if the app already checked
    first = plan['first']
    if not first and not plan['files']:
        return {'ok': True}  # nothing a save would commit
    if not _is_repo(project):
        _git(project, 'init', '-q')
    _add_exclusions(project)
    lock = os.path.join(_repo_state(project)[0], 'index.lock')
    names = b'\0'.join(f.encode() for f in plan['files'])
    if plan['files']:  # -A with a pathspec also stages deletions of those paths
        _write(project, lock, '--literal-pathspecs', 'add', '-A', '--pathspec-from-file=-', '--pathspec-file-nul', stdin=names)
    who = []
    if not all(_config(project, k) for k in ('user.name', 'user.email')):
        who = ['-c', 'user.name=Assieme', '-c', 'user.email=assieme@localhost']  # this one commit only
    # Named paths: anything the person staged elsewhere in the repository stays out of this commit.
    only = ['--literal-pathspecs'] if plan['files'] else []
    tail = ['--pathspec-from-file=-', '--pathspec-file-nul'] if plan['files'] else []
    _write(project, lock, *only, *who, 'commit', '-q', *(['--allow-empty'] if first else []),
           '-m', FIRST_MESSAGE if first else BEFORE_TEAM_MESSAGE, *tail, stdin=names)
    return {'ok': True}


def _write(project, lock, *args, stdin=None):
    """A git call that changes the repository. Killed for taking too long, git leaves .git/index.lock, which would fail
    every later git command, so one that was not there before the call is removed."""
    had = os.path.exists(lock)
    try:
        return _git(project, *args, timeout=SAVE_TIMEOUT_S, stdin=stdin)
    except TooLarge:  # the only way _git raises it is the timeout
        if not had:
            try:
                os.remove(lock)
            except OSError:
                pass
        raise SetupError('Saving took too long. Check for a slow git hook in this folder, then try again.')


def _config(project, key):
    try:
        return _git(project, 'config', key)[0].strip()
    except SetupError:
        return b''


def folder_create(path):
    path = os.path.expanduser(path)
    if os.path.lexists(path):
        raise SetupError('That folder already exists.')
    try:
        os.makedirs(path)
    except OSError as e:
        raise SetupError(e.strerror or str(e))
    return {'path': path}


LABEL = 'assieme.setup'  # on every install and sign-in terminal; nothing else is ever stopped or read through setup.*
# The commands shown to the person are these strings, and these strings are what run (spec section 3).
AGENTS = {
    'claude': {'name': 'Claude Code', 'install': 'curl -fsSL https://claude.ai/install.sh | bash',
               'signin': 'claude auth login', 'status': 'claude auth status'},
    'codex': {'name': 'Codex', 'install': 'curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh',
              'signin': 'codex login', 'status': 'codex login status'},
    'gemini': {'name': 'Gemini CLI', 'install': 'npm install -g @google/gemini-cli', 'signin': 'gemini', 'status': ''},
    'opencode': {'name': 'OpenCode', 'install': 'npm install -g --allow-scripts=opencode-ai opencode-ai',
                 'signin': 'opencode auth login', 'status': 'opencode auth list'},
    'cursor': {'name': 'Cursor', 'install': 'curl -fsS https://cursor.com/install | bash',
               'signin': 'cursor-agent login', 'status': 'cursor-agent status'},
}


def commands():
    return AGENTS


def _start(rt, agent, kind, command):
    """One terminal per (agent, kind): pressing the button again reattaches to the one still running."""
    value = f'{agent}-{kind}'
    for x in rt.list({LABEL: value}):
        if x.get('state') == 'live':
            return {'address': x['address']}
    return {'address': rt.start(['/bin/sh', '-c', command], os.path.expanduser('~'), {LABEL: value}, name=f'setup-{value}')}


def _known(agent):
    if agent not in AGENTS:
        raise SetupError('Unknown agent.')


def install_agent(rt, agent):
    _known(agent)
    command = AGENTS[agent]['install']
    if command.startswith('npm '):  # nvm's npm and node aren't on a running core's PATH
        npm = agent_path('npm')
        if not npm:
            raise SetupError(f"{AGENTS[agent]['name']} installs with npm, which comes with Node.js. Install Node.js first.")
        command = f'PATH={shlex.quote(os.path.dirname(npm))}:"$PATH" {command}'
    return _start(rt, agent, 'install', command)


def signin_agent(rt, agent):
    """The shown string with its program resolved to an absolute path: a running core keeps its old PATH."""
    _known(agent)
    path = agent_path(PROGRAM.get(agent, agent))
    if not path:
        raise SetupError(f"{AGENTS[agent]['name']} isn't installed yet.")
    rest = AGENTS[agent]['signin'].split(' ', 1)[1:]
    return _start(rt, agent, 'signin', ' '.join([shlex.quote(path), *rest]))


def _setup_terminal(rt, address):
    """The terminal, or None if it is gone. Anything not started for setup is refused: the address comes from the app."""
    try:
        info = rt.get(address)
    except runtime.RuntimeError_:
        return None
    if LABEL not in (info.get('labels') or {}):
        raise SetupError('That is not a setup terminal.')
    return info


def terminal_state(rt, address):
    info = _setup_terminal(rt, address)
    return 'running' if info and info.get('state') == 'live' else 'finished'


def install_stop(rt, address):
    info = _setup_terminal(rt, address)
    if info and info.get('state') == 'live':
        rt.kill(address)
    return {'ok': True}


def handlers(rt):
    return {'setup.commands': commands, 'setup.install_agent': lambda agent: install_agent(rt, agent),
            'setup.signin_agent': lambda agent: signin_agent(rt, agent),
            'setup.terminal_state': lambda address: terminal_state(rt, address),
            'setup.install_stop': lambda address: install_stop(rt, address),
            'setup.status': status, 'setup.install_tools': install_tools, 'setup.folder_preview': folder_preview,
            'setup.folder_save': folder_save, 'setup.folder_create': folder_create,
            'container.status': container_status, 'container.build': lambda agent: container_build(rt, agent),
            'container.open': container_open}


def container_status(agent):
    from . import container
    _known(agent)
    return container.state(agent)


def container_build(rt, agent):
    """Download and build: in a setup terminal, like an install, so it can be watched and survives the app."""
    from . import container
    _known(agent)
    try:
        return _start(rt, agent, 'container', container.build_command(agent))
    except container.ContainerError as e:
        raise SetupError(str(e)) from None


def container_open(run=subprocess.run):
    """Open Docker Desktop, which starts its engine. Never installs anything."""
    run(['open', '-a', 'Docker'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT_S)
    return {'ok': True}
