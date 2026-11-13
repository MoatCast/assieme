"""Bring your team over (step 30): agent work from other tools on this Mac, as a New team draft.

Two layers. The general scan works for any tool that uses git worktrees: it looks in the repositories Claude Code and
Codex worked in, and lists worktrees with work in them, with the agent and conversation found for each folder. On top,
one reader per tool we know adds what only that tool records (names, scripts, tasks).

Everything here only reads. It never writes, moves or deletes the other tool's files, opens databases read-only, and
never sends to or stops another tool's terminals."""
import glob
import json
import os
import sqlite3
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


def _branch(folder):
    r = subprocess.run(['git', '-C', folder, 'branch', '--show-current'], capture_output=True, text=True, timeout=LIMIT_S)
    return r.stdout.strip() if r.returncode == 0 else ''


def _project_of(path):
    """The main checkout of the repository `path` is in, or None."""
    try:
        main = _main_checkout(path, time.monotonic() + LIMIT_S) if os.path.isdir(path) else None
    except (OSError, subprocess.TimeoutExpired):
        return None
    return os.path.realpath(main) if main else None


def _kind_of(program):
    """Assieme's agent kind for a command line another tool ran, or None."""
    first = os.path.basename((program or '').split()[0]) if (program or '').split() else ''
    return AGENT_PROGRAMS.get(first) or (first if first in AGENT_PROGRAMS.values() else None)


# --- Claude Squad: ~/.claude-squad/state.json ----------------------------------------------------------------------

CS_RUNNING_UNTIL = 3  # its statuses: 0 running, 1 ready, 2 loading, 3 paused (worktree removed, branch kept)


def read_claude_squad(home):
    """(found, problem)."""
    path = os.path.join(home, '.claude-squad', 'state.json')
    if not os.path.isfile(path):
        return [], None
    try:
        with open(path) as f:
            instances = json.load(f).get('instances') or []
        if isinstance(instances, str):  # some versions keep the list as a JSON string
            instances = json.loads(instances)
    except (OSError, ValueError, AttributeError):
        return [], "Couldn't read Claude Squad's sessions. Its worktrees are still listed under Other agent work."
    out = []
    for i in instances if isinstance(instances, list) else []:
        wt = i.get('worktree') if isinstance(i, dict) else None
        if not isinstance(wt, dict) or not wt.get('repo_path'):
            continue
        folder = wt.get('worktree_path') if wt.get('worktree_path') and os.path.isdir(wt['worktree_path']) else None
        project = _project_of(folder or wt['repo_path']) or os.path.realpath(wt['repo_path'])
        kind = _kind_of(i.get('program'))
        notes = [] if kind else [f"It ran {(i.get('program') or 'an unknown program').split()[0]}, which Assieme "
                                 "doesn't run yet."]
        out.append(Found('claude-squad', project, i.get('title') or os.path.basename(folder or ''),
                         os.path.realpath(folder) if folder else None, wt.get('branch_name') or i.get('branch') or '',
                         kind=kind, running_elsewhere=i.get('status', CS_RUNNING_UNTIL) < CS_RUNNING_UNTIL,
                         notes=notes))
    return out, None


# --- Vibe Kanban: a SQLite database, opened read-only --------------------------------------------------------------

VK_NEEDS = {'projects': {'id', 'name'},
            'repos': {'id', 'path', 'setup_script', 'archive_script', 'dev_server_script'},
            'tasks': {'id', 'project_id', 'title', 'description', 'status'},
            'workspaces': {'id', 'task_id', 'container_ref', 'branch', 'archived', 'worktree_deleted'},
            'workspace_repos': {'workspace_id', 'repo_id'},
            'sessions': {'id', 'workspace_id', 'executor'}}
VK_STATUS = {'todo': 'todo', 'inprogress': 'doing', 'inreview': 'review', 'done': 'done'}  # cancelled: left out
VK_AGENTS = {'CLAUDE_CODE': 'claude', 'CODEX': 'codex', 'GEMINI': 'gemini', 'OPENCODE': 'opencode',
             'CURSOR_AGENT': 'cursor', 'CURSOR': 'cursor'}
VK_UNSUPPORTED = "This Vibe Kanban version isn't supported yet. Its worktrees are still listed under Other agent work."


def read_vibe_kanban(home):
    """(found, tasks per project, scripts per project, problem). Tasks are {title, description, status, branch}."""
    path = os.path.join(home, 'Library', 'Application Support', 'ai.bloop.vibe-kanban', 'db.v2.sqlite')
    if not os.path.isfile(path):
        return [], {}, {}, None
    try:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=LIMIT_S)
    except sqlite3.Error:
        return [], {}, {}, VK_UNSUPPORTED
    try:
        cols = lambda t: {r[1] for r in con.execute(f'PRAGMA table_info({t})')}
        if any(not need <= cols(t) for t, need in VK_NEEDS.items()):
            return [], {}, {}, VK_UNSUPPORTED
        q = lambda sql, *a: con.execute(sql, a).fetchall()
        repos = {r[0]: r[1:] for r in q('SELECT id, path, setup_script, archive_script, dev_server_script FROM repos')}
        scripts, tasks, found = {}, {}, []
        for path_, setup, archive, run in repos.values():
            scripts[os.path.realpath(path_)] = {k: v for k, v in (('setup', setup), ('run', run), ('archive', archive)) if v}
        resumable = cols('execution_processes') >= {'id', 'session_id'} and cols('coding_agent_turns') >= {
            'execution_process_id', 'agent_session_id'}
        task_rows = {t[0]: t[1:] for t in q('SELECT id, project_id, title, description, status FROM tasks')}
        project_repo = {}  # a task's project → the repository its workspaces use
        newest_branch = {}
        for wid, tid, ref, branch, archived, deleted in q(
                'SELECT id, task_id, container_ref, branch, archived, worktree_deleted FROM workspaces ORDER BY rowid'):
            rid = next((r[0] for r in q('SELECT repo_id FROM workspace_repos WHERE workspace_id = ?', wid)), None)
            repo = repos.get(rid)
            if not repo:
                continue
            project = os.path.realpath(repo[0])
            if tid in task_rows:
                project_repo[task_rows[tid][0]] = project
                newest_branch[tid] = branch
            if archived:
                continue
            folder = ref if ref and os.path.isdir(ref) and not deleted else None
            if folder and os.path.isdir(os.path.join(folder, os.path.basename(project))):
                folder = os.path.join(folder, os.path.basename(project))  # a workspace over several repositories
            sess = q('SELECT id, executor FROM sessions WHERE workspace_id = ? ORDER BY rowid DESC LIMIT 1', wid)
            kind = VK_AGENTS.get((sess[0][1] or '').upper()) if sess else None
            session = None
            if sess and resumable:
                turn = q('SELECT t.agent_session_id FROM coding_agent_turns t JOIN execution_processes e '
                         'ON t.execution_process_id = e.id WHERE e.session_id = ? AND t.agent_session_id IS NOT NULL '
                         'ORDER BY t.rowid DESC LIMIT 1', sess[0][0])
                session = turn[0][0] if turn else None
            title = task_rows.get(tid, (None, None))[1]
            found.append(Found('vibe-kanban', project, title or os.path.basename(ref or branch),
                               os.path.realpath(folder) if folder else None, branch, kind=kind, session=session))
        for tid, (pid, title, description, status) in task_rows.items():
            if pid in project_repo and status in VK_STATUS:
                tasks.setdefault(project_repo[pid], []).append(
                    {'title': title, 'description': description or '', 'status': VK_STATUS[status],
                     'branch': newest_branch.get(tid)})
        return found, tasks, scripts, None
    except sqlite3.Error:
        return [], {}, {}, VK_UNSUPPORTED
    finally:
        con.close()


# --- Conductor: ~/conductor/workspaces/<repo>/<city>, older .conductor/<city>; scripts in conductor.json -------

def read_conductor(home):
    """(found, scripts per project, problem). Its own database isn't documented, so it isn't read: the agent and
    its conversation come from the general scan."""
    folders = glob.glob(os.path.join(home, 'conductor', 'workspaces', '*', '*'))
    mains = {p for p in (_project_of(f) for f in folders + glob.glob(os.path.join(home, 'conductor', 'repos', '*'))) if p}
    folders += [f for m in mains for f in glob.glob(os.path.join(m, '.conductor', '*'))]
    found, scripts = [], {}
    for folder in sorted(set(folders)):
        project = _project_of(folder) if os.path.isdir(folder) else None
        if not project or os.path.realpath(folder) == project:
            continue
        found.append(Found('conductor', project, os.path.basename(folder), os.path.realpath(folder), _branch(folder)))
    for project in sorted({f.project for f in found} | mains):
        try:
            with open(os.path.join(project, 'conductor.json')) as f:
                given = json.load(f).get('scripts') or {}
        except (OSError, ValueError, AttributeError):
            continue
        scripts[project] = {k: v for k, v in given.items() if k in ('setup', 'run', 'archive') and isinstance(v, str) and v}
    return found, scripts, None


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
