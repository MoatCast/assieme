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
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import herdr, launch, newteam, scripts

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
VK_STATUS = {'todo': 'todo', 'inprogress': 'doing', 'inreview': 'review', 'done': 'done', 'cancelled': 'cancelled'}
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


# --- what the sheet shows, and the New team draft ---------------------------------------------------------------

TOOLS = {'conductor': 'Conductor', 'vibe-kanban': 'Vibe Kanban', 'claude-squad': 'Claude Squad', 'herdr': 'herdr',
         'other': 'Other agent work'}
LOOKED_FOR = ['Conductor', 'Vibe Kanban', 'Claude Squad', 'herdr']
SCRIPT_WORDS = {'setup': 'runs in each new agent’s copy', 'run': 'becomes the live preview',
                'archive': 'runs before a copy is archived'}
RESUMES = ('claude', 'codex')  # the kinds whose conversation Assieme picks up again (launch.argv)
ORDER = ('conductor', 'vibe-kanban', 'claude-squad', 'other')
_last = {}  # id → group, from the latest find: the sheet's next steps use what it showed


def _adopted(teams):
    """Folders saved teams already work in: brought over before, so not listed again."""
    out = set()
    for path in glob.glob(os.path.join(teams, '*', 'team.toml')):
        try:
            with open(path, 'rb') as f:
                out |= {os.path.realpath(r['worktree']) for r in tomllib.load(f).get('role', []) if r.get('worktree')}
        except (OSError, ValueError):
            continue
    return out


def _fill(f, base_of):
    """Work counts for a reader's workspace, from git; a folder now on another branch uses that branch."""
    try:
        if f.folder:
            now = _branch(f.folder)
            if now and now != f.branch:
                f.notes.append(f'{f.name} was on {f.branch}; its folder is on {now} now, so that branch is used.')
                f.branch = now
            f.dirty = bool(subprocess.run(['git', '-C', f.folder, 'status', '--porcelain'], capture_output=True,
                                          text=True, timeout=LIMIT_S).stdout.strip())
        base = base_of(f.project)
        if base and f.branch:
            r = subprocess.run(['git', '-C', f.project, 'rev-list', '--count', f'{base}..{f.branch}'],
                               capture_output=True, text=True, timeout=LIMIT_S)
            f.ahead = int(r.stdout.strip() or 0) if r.returncode == 0 else 0
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass


def _summary(items):
    n, kinds = len(items), []
    for f in items:
        name = launch.NAMES.get(f.kind) if f.kind else None
        if name and name not in kinds:
            kinds.append(name)
    unmerged = sum(1 for f in items if f.ahead)
    noun = 'session' if items[0].tool == 'claude-squad' else 'workspace'
    parts = [f'{n} {noun}{"s" if n != 1 else ""}'] + ([', '.join(kinds)] if kinds else [])
    return ' · '.join(parts + ([f'{unmerged} with unmerged work'] if unmerged else []))


def find(home=None, folder=None, teams=None, now=None, sessions=None, procs=None, herdr_teams=None):
    """{projects, unreadable, problems, looked_for}: what the sheet lists, one row per tool and project.
    `folder`: a project the person chose, looked in even when no agent history points at it."""
    home = os.path.realpath(home or os.path.expanduser('~'))
    if sessions is None:
        sessions = history(os.path.join(home, '.claude', 'projects'), os.path.join(home, '.codex', 'sessions'),
                           now or datetime.now(timezone.utc))
    procs = running_agents() if procs is None else procs
    squad, squad_problem = read_claude_squad(home)
    vibe, tasks, vibe_scripts, vibe_problem = read_vibe_kanban(home)
    cond, cond_scripts, cond_problem = read_conductor(home)
    readers = squad + vibe + cond
    repos = repos_from_history(sessions) + [f.project for f in readers]
    if folder:
        repos.append(_project_of(folder) or folder)
    repos = list(dict.fromkeys(os.path.realpath(r) for r in repos))
    scanned, unreadable = scan(repos, sessions, procs, home)
    by_folder = {f.folder: f for f in scanned if f.folder}
    bases = {}
    base_of = lambda p: bases[p] if p in bases else bases.setdefault(p, _base(p, time.monotonic() + LIMIT_S))
    for f in readers:
        s = by_folder.pop(f.folder, None) if f.folder else None
        _fill(f, base_of)
        if s:
            f.kind, f.model, f.session = f.kind or s.kind, f.model or s.model, f.session or s.session
            f.running_elsewhere = f.running_elsewhere or s.running_elsewhere
    skip = _adopted(teams or newteam.teams_dir())
    groups = {}
    for f in readers + list(by_folder.values()):
        if not (f.folder and f.folder in skip):
            groups.setdefault((f.tool, f.project), []).append(f)
    _last.clear()
    projects = []
    for (tool, project), its in sorted(groups.items(), key=lambda kv: (ORDER.index(kv[0][0]), kv[0][1])):
        its.sort(key=lambda f: (-f.ahead, not f.dirty, f.name))  # the most work first
        id_ = f'{tool}:{project}'
        given = {'conductor': cond_scripts, 'vibe-kanban': vibe_scripts}.get(tool, {}).get(project, {})
        _last[id_] = {'tool': tool, 'project': project, 'items': its, 'scripts': given,
                      'tasks': tasks.get(project, []) if tool == 'vibe-kanban' else []}
        where = TOOLS[tool] if tool != 'other' else 'another tool'
        projects.append({'id': id_, 'tool': tool, 'label': os.path.basename(project), 'summary': _summary(its),
                         'running': f'Still running in {where}: stop it there first.'
                         if any(f.running_elsewhere for f in its) else None})
    try:
        herdr_found = herdr.find() if herdr_teams is None else {'teams': herdr_teams}
    except Exception:  # herdr's own reader: one broken source must not hide the others
        herdr_found = {'teams': []}
    for t in herdr_found.get('teams', []):
        projects.append({'id': f'herdr:{t["id"]}', 'tool': 'herdr', 'label': t['label'],
                         'summary': f'{t["where"]} · {t["agents"]} agent{"s" if t["agents"] != 1 else ""}',
                         'running': 'Still running in herdr: stop it there first.' if t.get('running') else None})
    problems = [p for p in (squad_problem, vibe_problem, cond_problem) if p]
    return {'projects': projects, 'unreadable': unreadable, 'problems': problems, 'looked_for': LOOKED_FOR}


def _group(id_):
    g = _last.get(id_)
    if not g:
        raise ValueError("That project isn't in the list any more. Look again.")
    return g


def _becomes(f, n):
    who = ', '.join(x for x in (launch.NAMES.get(f.kind or 'claude'), f.model) if x)
    work = (f'{f.ahead} commit{"s" if f.ahead != 1 else ""} not merged' if f.ahead
            else 'uncommitted changes' if f.dirty else 'nothing new yet')
    return f'Becomes builder-{n} · {who} · {work}'


def workspaces(id_):
    """{label, tool, rows, scripts}: the "What it becomes" step for one project."""
    g = _group(id_)
    rows, n = [], 0
    for f in g['items']:
        ticked = bool(f.ahead or f.dirty)
        n += ticked
        rows.append({'name': f.name, 'branch': f.branch, 'ticked': ticked, 'becomes': _becomes(f, n) if ticked else '',
                     'note': '' if ticked else 'Left out: nothing new on its branch. Tick it to bring it anyway.'})
    lines = [{'which': k, 'line': scripts.rewrite(v)[0], 'becomes': SCRIPT_WORDS[k]} for k, v in g['scripts'].items()
             if k in SCRIPT_WORDS]
    return {'label': os.path.basename(g['project']), 'tool': TOOLS[g['tool']], 'rows': rows, 'scripts': lines}


def draft(id_, ticked):
    """The New team draft: one agent per ticked workspace, working in that folder on that branch."""
    if id_.startswith('herdr:'):
        return herdr.draft(id_.removeprefix('herdr:')) | {'from': 'herdr'}
    g = _group(id_)
    by_name = {f.name: f for f in g['items']}
    chosen = [by_name[t] for t in dict.fromkeys(ticked or []) if t in by_name]  # in the order the sheet lists them
    if not chosen:
        raise ValueError('Tick at least one workspace to bring over.')
    tool, notes = TOOLS[g['tool']], {}

    def note(text, agent=None):
        notes.setdefault(text, []).extend([agent] if agent else [])
    tasks = {t['branch']: t for t in g['tasks'] if t.get('branch')}
    roles, by_branch = [], {}
    for n, f in enumerate(chosen, 1):
        name = f'builder-{n}'
        agent = f.kind if f.kind in launch.CLIS else 'claude'
        if f.kind is None:
            note("Which agent worked here isn't recorded, so they start with Claude Code. Change it if needed.", name)
        elif f.kind not in launch.CLIS:
            note("They ran an agent Assieme doesn't start yet, so they start with Claude Code.", name)
        task = tasks.get(f.branch)
        brief = f"{task['title']}\n\n{task['description']}".strip() if task else ''
        if not brief:
            brief = newteam.kind_brief('builder')
            note('No brief was found, so they get the standard builder brief. Check it before you start.', name)
        role = {'name': name, 'agent': agent, 'job': f.name, 'checkout': 'branch', 'parent': None, 'brief': brief,
                'kind': 'builder', 'branch': f.branch}
        if f.model and not launch.check_model_effort(agent, f.model, None):
            role['model'] = f.model
        if f.folder:
            role['worktree'] = f.folder
        else:
            note('Their folder is gone, so each gets a fresh copy of its branch.', name)
        if f.session and agent in RESUMES and f.kind == agent:
            role['resume'] = f.session
        elif f.folder:
            note('No conversation was found to pick up, so they start a new one with their brief.', name)
        if f.running_elsewhere:
            note(f'Still running in {tool}: stop them there first, or they are skipped at Start.', name)
        for text in f.notes:
            note(text, name)
        roles.append(role)
        by_branch[f.branch] = name
    note(f"They start with Assieme's safe permissions: each works in its own folder and can't push. Wider permissions "
         f'set in {tool} are not carried over.')
    cards = [{'title': t['title'], 'status': t['status'], 'role': by_branch.get(t.get('branch'))}
             for t in g['tasks'] if t['status'] != 'cancelled']
    cancelled = len(g['tasks']) - len(cards)
    if cancelled:
        note(f'{cancelled} cancelled task{"s were" if cancelled != 1 else " was"} left off the board.')
    unknown = sorted({u for v in g['scripts'].values() for u in scripts.rewrite(v)[1]})
    if unknown:
        note(f"Its scripts use {', '.join(unknown)}, which Assieme doesn't set.")
    return {'project': g['project'], 'task': '', 'roles': roles, 'cards': cards,
            'scripts': {k: scripts.rewrite(v)[0] for k, v in g['scripts'].items() if k in SCRIPT_WORDS},
            'notes': [{'text': t, 'agents': a} for t, a in notes.items()],
            'running': next((f'Still running in {tool}: stop it there first.' for f in chosen if f.running_elsewhere), None),
            'from': 'bringover', 'tool': tool, 'label': os.path.basename(g['project'])}


# --- at Start ------------------------------------------------------------------------------------------------------

def not_running(roles, tool, procs=None):
    """(roles to start, skipped): an agent still running in the other tool's folder isn't started beside it."""
    procs = running_agents() if procs is None else procs
    kept, skipped = [], []
    for r in roles:
        if r.get('worktree') and any(p.get('cwd') and _inside(p['cwd'], r['worktree']) for p in procs):
            skipped.append({'role': r['name'], 'reason': f'Still running in {tool}: stop it there first.'})
        else:
            kept.append(r)
    return kept, skipped


def started(team, project, roles, cards, scripts_, origin, remember):
    """After a brought-over team is saved and starting: its tasks on the board, its run script as the preview
    command, and one Activity row that Undo turns back."""
    from . import live, tasks
    for c in cards:
        card = tasks.add(team, c['title'], c.get('role'), by='assieme')
        if c.get('status') and c['status'] != 'todo':
            tasks.move(team, card['id'], c['status'])
    if scripts_.get('run'):
        remember(project, scripts_['run'])
    live.record('bringover', None, f"Its agents work in the folders they had in {origin['tool']}.", team=team,
                title=f"Brought over {origin['label']} from {origin['tool']}",
                adopted=[r['worktree'] for r in roles if r.get('worktree')])
