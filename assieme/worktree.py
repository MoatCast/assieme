"""One git worktree per role, under <project>/.assieme/worktrees/<role>. Uses the git command.

A role that runs in a container gets a clone at that path instead: a worktree's `.git` points into the project's
own, and the container could write hooks there that later run on the Mac. Its commits come back with `sync`.
"""
import os
import subprocess


class WorktreeError(RuntimeError):
    """message: what happened and what to do, in plain words. detail: git's own text or the path, for Details."""

    def __init__(self, message, detail=''):
        super().__init__(message)
        self.detail = detail


class NoCommitsError(WorktreeError):
    pass


# A clone's own config is the container's to write: git run in one never takes a program from it.
SAFE = ('-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null')


def is_clone(path):
    """A container agent's copy: a repository of its own at <project>/.assieme/worktrees/<role>."""
    parent = os.path.dirname(os.path.realpath(path))
    return (os.path.basename(parent) == 'worktrees' and os.path.basename(os.path.dirname(parent)) == '.assieme'
            and os.path.isdir(os.path.join(path, '.git')))


def _git(cwd, *args):
    return subprocess.run(['git', *(SAFE if is_clone(cwd) else ()), '-C', cwd, *args], capture_output=True, text=True)


def check_repo(project):
    r = _git(project, 'rev-parse', '--show-toplevel')
    if r.returncode != 0:
        raise WorktreeError(f"{_name(project)} isn't set up to keep versions yet. Choose it in New team to set it up.")
    if _git(project, 'rev-parse', '--verify', '--quiet', 'HEAD').returncode != 0:
        raise NoCommitsError(f'{_name(project)} has no saved version yet. '
                             'Choose it in New team to save a first version.')


def _name(project):
    return os.path.basename(os.path.normpath(project))


def exclude(project, line='.assieme/'):
    """Keep a file Assieme writes out of every commit: one line in the repository's own info/exclude."""
    path = _git(project, 'rev-parse', '--git-path', 'info/exclude').stdout.strip()
    if not path:
        return
    path = os.path.join(project, path) if not os.path.isabs(path) else path
    lines = open(path).read().splitlines() if os.path.exists(path) else []
    if line not in lines:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a') as f:
            f.write(('\n' if lines and lines[-1] else '') + line + '\n')


def folder(team, role):
    """Where the role works."""
    return os.path.join(team.project, '.assieme', 'worktrees', role.name)


def _kept(role):
    """A brought-over role's own folder (step 30), while it is still there."""
    return role.worktree if role.worktree and os.path.isdir(role.worktree) else None


TOOL_PATHS = (('/conductor/', 'Conductor'), ('/.conductor/', 'Conductor'), ('/.claude-squad/', 'Claude Squad'),
              ('/vibe-kanban/', 'Vibe Kanban'), ('/.vibe-kanban-workspaces/', 'Vibe Kanban'))


def tool_name(folder):
    """The tool a brought-over folder belongs to, from where it is; None when the path doesn't say."""
    return next((name for part, name in TOOL_PATHS if part in folder + '/'), None)


def branch_of(team, role):
    """The branch the role works on: the one it was brought over with while its folder is there, else Assieme's."""
    return role.branch if _kept(role) and role.branch else f'assieme/{team.name}/{role.name}'


def _same_repo(project, path):
    common = lambda p: _git(p, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    a, b = common(project), common(path)
    return a.returncode == 0 and b.returncode == 0 and os.path.realpath(a.stdout.strip()) == os.path.realpath(b.stdout.strip())


def ensure(team, role):
    """The role's worktree path: created on first use, reused while clean, never reset. A brought-over role works
    in its own folder as it is, uncommitted work included; when that folder has gone, it gets an Assieme copy that
    starts from its branch."""
    check_repo(team.project)
    kept = _kept(role)
    if kept:
        if not _same_repo(team.project, kept):
            raise WorktreeError(f"{kept} isn't a copy of this project. Bring it over again from its own project.")
        now = _git(kept, 'branch', '--show-current').stdout.strip()
        if role.branch and now != role.branch:  # Review would follow the old branch and miss the agent's commits
            raise WorktreeError(f'{os.path.basename(kept)} is on {now or "no branch"} now, not {role.branch}. '
                                'Switch it back, or bring it over again.')
        return kept
    path = folder(team, role)
    branch = f'assieme/{team.name}/{role.name}'
    start = role.branch if role.branch and not os.path.isdir(path) and _git(
        team.project, 'rev-parse', '--verify', '--quiet', f'refs/heads/{role.branch}').returncode == 0 else 'HEAD'
    exclude(team.project)
    if os.path.isdir(path):
        top = _git(path, 'rev-parse', '--show-toplevel')
        if top.returncode != 0 or os.path.realpath(top.stdout.strip()) != os.path.realpath(path):
            raise WorktreeError(f"A folder is in the way of {role.name}'s copy. Move it, then start again.", path)
        st = _git(path, 'status', '--porcelain')
        if st.returncode != 0 or st.stdout.strip():
            raise WorktreeError(f"{role.name}'s copy has changes that aren't saved yet. "
                                'Save or remove them, then start again.', path)
        return path
    if role.container:
        return _clone(team.project, path, branch, role.checkout == 'read-only', role.name)
    if role.checkout == 'read-only':
        args = ['worktree', 'add', '--detach', path, 'HEAD']
    else:
        if _git(team.project, 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}').returncode == 0:
            raise WorktreeError(f'A copy named {branch} is left over from an earlier team. '
                                'Rename this team, or remove the old copy.', f'git branch -D {branch}')
        args = ['worktree', 'add', '-b', branch, path, start]
    r = _git(team.project, *args)
    if r.returncode != 0:
        raise _failed(role.name, args, r)
    return path


def _failed(name, args, r):
    return WorktreeError(f"Assieme couldn't make {name}'s copy.", f'git {" ".join(args)}\n{r.stderr.strip()}')


def _clone(project, path, branch, read_only, name):
    """The container's copy: its own branch, made in the project first so Review knows it."""
    # A branch already there (its work brought back by an undo) is where the copy starts.
    if not read_only and _git(project, 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}').returncode != 0:
        r = _git(project, 'branch', branch, 'HEAD')
        if r.returncode != 0:
            raise _failed(name, ['branch', branch, 'HEAD'], r)
    # No hard links: the container must not be able to change the project's own object files.
    args = ['clone', '--quiet', '--no-hardlinks', *([] if read_only else ['--branch', branch]), project, path]
    r = _git(project, *args)
    if r.returncode != 0:
        raise _failed(name, args, r)
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
