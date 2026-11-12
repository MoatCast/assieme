"""One git worktree per role, under <project>/.assieme/worktrees/<role>. Uses the git command."""
import os
import subprocess


class WorktreeError(RuntimeError):
    pass


class NoCommitsError(WorktreeError):
    pass


def _git(cwd, *args):
    return subprocess.run(['git', '-C', cwd, *args], capture_output=True, text=True)


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
