"""First-run setup: Apple's developer tools, where the agents are installed, and whether they are signed in.

Must not import `launch`: launch uses `agent_path` as its default lookup.
"""
import os
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

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
    paths = {name: agent_path(name) for name in STATUS}
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


# What Set up this folder leaves out of the first version, by name.
EXCLUDE = ('.env', '.env.*', '*.pem', '*.key', '*.p12', 'id_rsa*', 'id_ed25519*', 'credentials*', 'secrets*',
           'node_modules', '.venv', 'venv', '__pycache__', '.DS_Store')
MAX_FILES = 20000
LIST_TIMEOUT_S = 15
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


def changed_files(project, untracked='all'):
    """Paths `git status --porcelain -z` reports, leaving out .assieme/."""
    entries = _names(_git(project, 'status', '--porcelain', '-z', f'--untracked-files={untracked}')[0])
    paths, skip = [], False
    for e in entries:
        if skip:  # the old name of a rename or copy
            skip = False
            continue
        skip = e[0] in 'RC'
        paths.append(e[3:])
    return [p for p in paths if p != ASSIEME_DIR and not p.startswith(ASSIEME_DIR + '/')]


def _plan(project):
    """The one list of what a save commits, so the preview, the warning and the save cannot disagree.
    Reads only: not even a .git, index or exclude file is written."""
    if too_broad(project):
        raise TooLarge('This folder is too large.')
    in_repo = _is_repo(project)
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
    plan = _plan(project)
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
    plan = _plan(project)  # refuses a too-broad folder even if the app already checked
    first = plan['first']
    if not first and not plan['files']:
        return {'ok': True}  # nothing a save would commit
    if not _is_repo(project):
        _git(project, 'init', '-q')
    _add_exclusions(project)
    if plan['files']:  # -A with a pathspec also stages deletions of those paths
        _git(project, '--literal-pathspecs', 'add', '-A', '--pathspec-from-file=-', '--pathspec-file-nul',
             stdin=b'\0'.join(f.encode() for f in plan['files']))
    who = []
    if not all(_config(project, k) for k in ('user.name', 'user.email')):
        who = ['-c', 'user.name=Assieme', '-c', 'user.email=assieme@localhost']  # this one commit only
    _git(project, *who, 'commit', '-q', *(['--allow-empty'] if first else []), '-m', FIRST_MESSAGE if first else BEFORE_TEAM_MESSAGE)
    return {'ok': True}


def _config(project, key):
    try:
        return _git(project, 'config', key)[0].strip()
    except SetupError:
        return b''


def folder_create(path):
    if os.path.lexists(path):
        raise SetupError('That folder already exists.')
    try:
        os.makedirs(path)
    except OSError as e:
        raise SetupError(e.strerror or str(e))
    return {'path': path}


def handlers(rt):
    return {'setup.status': status, 'setup.install_tools': install_tools, 'setup.folder_preview': folder_preview,
            'setup.folder_save': folder_save, 'setup.folder_create': folder_create}
