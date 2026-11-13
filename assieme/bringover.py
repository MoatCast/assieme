"""Bring your team over (step 30): agent work from other tools on this Mac, as a New team draft.

Two layers. The general scan works for any tool that uses git worktrees: it looks in the repositories Claude Code and
Codex worked in, and lists worktrees with work in them, with the agent and conversation found for each folder. On top,
one reader per tool we know adds what only that tool records (names, scripts, tasks).

Everything here only reads. It never writes, moves or deletes the other tool's files, opens databases read-only, and
never sends to or stops another tool's terminals."""
import glob
import json
import os
import subprocess
import time
from dataclasses import dataclass, field

LIMIT_S = 5  # per repository: a slow or broken one is listed as unreadable and skipped
DAYS = 30
AGENT_PROGRAMS = {'claude': 'claude', 'codex': 'codex', 'gemini': 'gemini', 'opencode': 'opencode',
                  'cursor-agent': 'cursor', 'agy': 'antigravity'}


@dataclass
class Found:
    tool: str            # 'conductor' | 'claude-squad' | 'vibe-kanban' | 'herdr' | 'other'
    project: str         # the repository's main checkout
    name: str            # the workspace's own name, e.g. 'tokyo'
    folder: str | None   # None when only its branch is left
    branch: str
    ahead: int = 0       # commits not on the default branch
    dirty: bool = False  # uncommitted changes
    kind: str | None = None
    model: str | None = None
    session: str | None = None
    running_elsewhere: bool = False
    notes: list = field(default_factory=list)


# --- what agents ran where -------------------------------------------------------------------------------------

def _rows(path, n=200):
    """The first `n` JSON rows of a log; bad lines are skipped."""
    out = []
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
                if len(out) >= n:
                    break
    except OSError:
        pass
    return out


def history(claude_dir, codex_dir, now, days=DAYS):
    """[{kind, cwd, session, model, at}] for each Claude Code and Codex session used in the last `days`."""
    cutoff, out = now.timestamp() - days * 86400, []
    for path in glob.glob(os.path.join(claude_dir, '*', '*.jsonl')):
        at = os.path.getmtime(path)
        if at < cutoff:
            continue
        rows = _rows(path)
        cwd = next((r['cwd'] for r in rows if isinstance(r, dict) and isinstance(r.get('cwd'), str)), None)
        model = next((r['message']['model'] for r in rows if isinstance(r, dict) and isinstance(r.get('message'), dict)
                      and isinstance(r['message'].get('model'), str)), None)
        if cwd:
            out.append({'kind': 'claude', 'cwd': cwd, 'session': os.path.basename(path)[:-len('.jsonl')],
                        'model': model, 'at': at})
    for path in glob.glob(os.path.join(codex_dir, '*', '*', '*', '*.jsonl')):
        at = os.path.getmtime(path)
        if at < cutoff:
            continue
        rows = [r for r in _rows(path, 50) if isinstance(r, dict) and isinstance(r.get('payload'), dict)]
        meta = next((r['payload'] for r in rows if r.get('type') == 'session_meta'), {})
        model = next((r['payload'].get('model') for r in rows if r.get('type') == 'turn_context'), None)
        if isinstance(meta.get('cwd'), str) and meta.get('id'):
            out.append({'kind': 'codex', 'cwd': meta['cwd'], 'session': str(meta['id']), 'model': model, 'at': at})
    return out


def running_agents(run=subprocess.run):
    """[{pid, kind, cwd}] of the agent programs Assieme knows, from their working folders."""
    args = ['lsof', '-a', '-d', 'cwd', '-Fpcn'] + [x for p in AGENT_PROGRAMS for x in ('-c', p)]
    try:
        r = run(args, capture_output=True, text=True, timeout=LIMIT_S)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out, cur = [], {}
    for line in r.stdout.splitlines():
        tag, value = line[:1], line[1:]
        if tag == 'p':
            cur = {'pid': int(value)}
        elif tag == 'c':
            cur['kind'] = next((k for p, k in AGENT_PROGRAMS.items() if value.startswith(p[:9])), None)
        elif tag == 'n' and cur.get('kind'):
            out.append(cur | {'cwd': value})
    return out


# --- repositories and their worktrees ---------------------------------------------------------------------------

def _git(cwd, *args, deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise subprocess.TimeoutExpired(['git', *args], 0)
    return subprocess.run(['git', '-C', cwd, *args], capture_output=True, text=True, timeout=left)


def _main_checkout(folder, deadline):
    r = _git(folder, 'rev-parse', '--path-format=absolute', '--git-common-dir', deadline=deadline)
    if r.returncode != 0:
        return None
    common = r.stdout.strip()
    return os.path.dirname(common) if os.path.basename(common) == '.git' else None


def repos_from_history(sessions):
    """The main checkout of each repository the sessions ran in, once each, in the order first seen."""
    out = []
    for s in sessions:
        cwd = s.get('cwd')
        if not cwd or not os.path.isdir(cwd):
            continue
        try:
            main = _main_checkout(cwd, time.monotonic() + LIMIT_S)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if main and os.path.realpath(main) not in out:
            out.append(os.path.realpath(main))
    return out


def tool_of(folder, home):
    """Which tool a worktree belongs to, from where it is."""
    f = folder.rstrip('/') + '/'
    if f.startswith(os.path.join(home, 'conductor') + '/') or '/.conductor/' in f:
        return 'conductor'
    if f.startswith(os.path.join(home, '.claude-squad') + '/'):
        return 'claude-squad'
    if '/vibe-kanban/worktrees/' in f or '/.vibe-kanban-workspaces/' in f:
        return 'vibe-kanban'
    return 'other'


def _base(repo, deadline):
    head = _git(repo, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD', deadline=deadline)
    names = [head.stdout.strip().removeprefix('origin/')] if head.returncode == 0 else []
    for name in names + ['main', 'master']:
        if _git(repo, 'rev-parse', '--verify', '--quiet', f'refs/heads/{name}', deadline=deadline).returncode == 0:
            return name
    return None


def _worktrees(repo, deadline):
    """[(path, branch)] of the repository's worktrees on a branch, its main checkout first."""
    r = _git(repo, 'worktree', 'list', '--porcelain', deadline=deadline)
    if r.returncode != 0:
        raise OSError(r.stderr.strip())
    out, path = [], None
    for line in r.stdout.splitlines():
        if line.startswith('worktree '):
            path = line.removeprefix('worktree ')
        elif line.startswith('branch refs/heads/') and path:
            out.append((path, line.removeprefix('branch refs/heads/')))
    return out


def _inside(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder + os.sep)


def scan(repos, sessions, procs, home=None, limit_s=LIMIT_S):
    """(found, unreadable). A worktree is listed when it is not the main checkout or Assieme's own, has work in it,
    and either sits where a known tool keeps worktrees or had an agent in it."""
    home = os.path.realpath(home or os.path.expanduser('~'))
    found, unreadable = [], []
    for repo in repos:
        deadline = time.monotonic() + limit_s
        try:
            base = _base(repo, deadline)
            trees = _worktrees(repo, deadline)
            mine = []
            for path, branch in trees[1:]:
                if '/.assieme/worktrees/' in path + '/' or branch == base:
                    continue
                ahead = 0
                if base:
                    r = _git(repo, 'rev-list', '--count', f'{base}..{branch}', deadline=deadline)
                    ahead = int(r.stdout.strip() or 0) if r.returncode == 0 else 0
                dirty = bool(_git(path, 'status', '--porcelain', deadline=deadline).stdout.strip())
                if not (ahead or dirty):
                    continue
                here = sorted((s for s in sessions if s.get('cwd') and _inside(s['cwd'], path)), key=lambda s: s['at'])
                there = [p for p in procs if p.get('cwd') and _inside(p['cwd'], path)]
                tool = tool_of(os.path.realpath(path), home)
                if tool == 'other' and not (here or there):
                    continue  # the person's own worktree: no agent worked there
                last = here[-1] if here else {}
                mine.append(Found(tool, os.path.realpath(trees[0][0]), os.path.basename(path), os.path.realpath(path),
                                  branch, ahead, dirty, last.get('kind') or (there[0]['kind'] if there else None),
                                  last.get('model'), last.get('session'), bool(there)))
        except (OSError, subprocess.TimeoutExpired, ValueError):
            unreadable.append(repo)
            continue
        found += mine
    return found, unreadable
