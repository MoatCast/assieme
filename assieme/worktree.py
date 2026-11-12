"""One git worktree per role, under <project>/.assieme/worktrees/<role>. Uses the git command.

A role that runs in a container gets a clone at that path instead: a worktree's `.git` points into the project's
own, and the container could write hooks there that later run on the Mac. Its commits come back with `sync`.
"""
import os
import subprocess


class WorktreeError(RuntimeError):
    pass


class NoCommitsError(WorktreeError):
    pass


# A clone's own config is the container's to write: git run in one never takes a program from it.
SAFE = ('-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null')


def is_clone(path):
    return os.path.isdir(os.path.join(path, '.git'))


def _git(cwd, *args):
    return subprocess.run(['git', *(SAFE if is_clone(cwd) else ()), '-C', cwd, *args], capture_output=True, text=True)


def check_repo(project):
    r = _git(project, 'rev-parse', '--show-toplevel')
    if r.returncode != 0:
        raise WorktreeError(f'{project} is not a git repository. Make it one with: git init {project} '
                            '(and make a first commit)')
    if _git(project, 'rev-parse', '--verify', '--quiet', 'HEAD').returncode != 0:
        raise NoCommitsError(f'{project} has no commits yet. Make a first commit, then start again.')


def _exclude(project, pattern='.assieme/'):
    """Keep `pattern` out of git in this repository, without touching any tracked file."""
    path = _git(project, 'rev-parse', '--git-path', 'info/exclude').stdout.strip()
    if not path:
        return
    path = os.path.join(project, path) if not os.path.isabs(path) else path
    lines = open(path).read().splitlines() if os.path.exists(path) else []
    if pattern not in lines:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a') as f:
            f.write(('\n' if lines and lines[-1] else '') + pattern + '\n')


def ensure(team, role):
    """The role's worktree path: created on first use, reused while clean, never reset."""
    check_repo(team.project)
    path = os.path.join(team.project, '.assieme', 'worktrees', role.name)
    branch = f'assieme/{team.name}/{role.name}'
    _exclude(team.project)
    if os.path.isdir(path):
        top = _git(path, 'rev-parse', '--show-toplevel')
        if top.returncode != 0 or os.path.realpath(top.stdout.strip()) != os.path.realpath(path):
            raise WorktreeError(f'{path} exists but is not a git worktree. Move it away, then start again.')
        st = _git(path, 'status', '--porcelain')
        if st.returncode != 0 or st.stdout.strip():
            raise WorktreeError(f'{path} has uncommitted changes. Commit or remove them, then start again.')
        return path
    if role.container:
        return _clone(team.project, path, branch, role.checkout == 'read-only')
    if role.checkout == 'read-only':
        args = ['worktree', 'add', '--detach', path, 'HEAD']
    else:
        if _git(team.project, 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}').returncode == 0:
            raise WorktreeError(f'branch {branch} already exists but has no worktree at {path}. '
                                f'Delete it (git branch -D {branch}) or rename the team.')
        args = ['worktree', 'add', '-b', branch, path]
    r = _git(team.project, *args)
    if r.returncode != 0:
        raise WorktreeError(f'git {" ".join(args)} failed: {r.stderr.strip()}')
    return path


def _clone(project, path, branch, read_only):
    """The container's copy: its own branch, made in the project first so Review knows it."""
    # A branch already there (its work brought back by an undo) is where the copy starts.
    if not read_only and _git(project, 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}').returncode != 0:
        r = _git(project, 'branch', branch, 'HEAD')
        if r.returncode != 0:
            raise WorktreeError(f'git branch {branch} failed: {r.stderr.strip()}')
    # No hard links: the container must not be able to change the project's own object files.
    r = _git(project, 'clone', '--quiet', '--no-hardlinks', *([] if read_only else ['--branch', branch]), project, path)
    if r.returncode != 0:
        raise WorktreeError(f'git clone failed: {r.stderr.strip()}')
    if read_only:
        _git(path, 'checkout', '--quiet', '--detach')
    for key in ('user.name', 'user.email'):  # who the agent's commits are by, as in a worktree
        value = _git(project, 'config', key).stdout.strip()
        if value:
            _git(path, 'config', key, value)
    return path


def sync(project):
    """Fetch each container agent's branch from its clone into the project, so Review and the board see its commits."""
    base = os.path.join(project, '.assieme', 'worktrees')
    try:
        if not any(is_clone(os.path.join(base, n)) for n in os.listdir(base)):
            return  # the usual case: no git call at all
    except OSError:
        return
    for ref in _git(project, 'for-each-ref', '--format=%(refname)', 'refs/heads/assieme/').stdout.split():
        path = os.path.join(base, ref.rsplit('/', 1)[-1])
        if is_clone(path):
            _git(project, 'fetch', '--quiet', '--no-tags', path, f'+{ref}:{ref}')
