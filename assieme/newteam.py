"""New team in the app: the starting teams, checks, saving a team folder, starting it in the background, and
Help me choose. The app sends the form; this module turns it into an ordinary team folder for `preset.load`."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib

from . import ask, container, live, launch, permissions, preset, runtime, scripts, settings, setup, team, worktree

PRESETS_DIR = os.path.join(os.path.dirname(__file__), 'presets')
BUILTIN = ('one-agent', 'builder-reviewer', 'full')
KINDS = {'planner': ('planner.md', 'read-only'), 'builder': ('builder.md', 'branch'),
         'reviewer': ('reviewer.md', 'read-only'), 'tester': ('tester.md', 'branch')}


NAME = re.compile(r'^[a-z0-9-]+$')
BAD_NAME = 'Names use lower-case letters, digits and dashes, and must differ.'
BRANCH = re.compile(r'^(?!-)(?!.*\.\.)[A-Za-z0-9._/-]+$')  # a brought-over branch: plain git names only
KEEP_DONE_S = 600  # a finished start stays on First run for 10 minutes
NEEDS_SAVE = "This folder has no saved version yet. Agents work from a saved version of your project, so it needs one."
TOO_LARGE = "This folder is very large. Choose the folder for just this project."
NO_TOOLS = "This needs Apple's developer tools first."
NO_GIT = "This machine needs git. Install it there, then choose the folder again."


class NewTeamError(ValueError):
    """A problem the form shows next to `field` ('project', 'roles', 'role:<index>', 'start' or 'suggest')."""

    def __init__(self, field, message):
        super().__init__(f'{field}: {message}')
        self.field, self.message = field, message


def teams_dir():
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'assieme', 'teams')


def kind_brief(kind):
    with open(os.path.join(PRESETS_DIR, 'briefs', KINDS[kind][0])) as f:
        return f.read()


def _role_dict(r):
    return {'name': r.name, 'agent': r.agent, 'job': r.job, 'checkout': r.checkout, 'parent': r.parent,
            'brief': r.brief, 'model': r.model, 'effort': r.effort, 'group': r.group, 'kind': r.kind,
            'owns': r.owns, 'level': r.level, 'rules': r.rules, 'container': r.container, 'share_signin': r.share_signin,
            **{k: getattr(r, k) for k in ('worktree', 'branch', 'resume') if getattr(r, k)}}  # only brought-over roles


def _as_preset(folder, id_, label=None):
    tm = preset.load(folder, practices=False)
    with open(os.path.join(folder, 'team.toml'), 'rb') as f:
        data = tomllib.load(f)
    by_name = {r.name: r for r in tm.roles}  # load orders parents first; the form keeps the file's order
    return {'id': id_, 'label': label or data.get('label', id_),
            'roles': [_role_dict(by_name[r['name']]) for r in data.get('role', [])]}


def presets(teams=None):
    teams = teams or teams_dir()
    builtin = [_as_preset(os.path.join(PRESETS_DIR, i), i) for i in BUILTIN]
    last = None
    saved = sorted((os.path.join(teams, d) for d in (os.listdir(teams) if os.path.isdir(teams) else [])),
                   key=os.path.getmtime, reverse=True)
    for folder in saved:
        try:
            last = _as_preset(folder, os.path.basename(folder), 'My last team')
            break
        except (preset.PresetError, OSError):
            continue  # a half-written or hand-edited folder: try the next one
    kinds = {k: {'brief': kind_brief(k), 'job': _KIND_JOBS[k], 'checkout': c} for k, (_, c) in KINDS.items()}
    return {'presets': builtin, 'practices': preset.practices(), 'last': last, 'models': model_options(),
            'practice_defaults': settings.load()['practices'], 'kinds': kinds, 'permissions': permissions.table()}


# Claude Code's own aliases, strongest first (Help me choose is told that order)
CLAUDE_MODELS = (('fable', 'Fable'), ('opus', 'Opus'), ('sonnet', 'Sonnet'), ('haiku', 'Haiku'))


def _codex_cache():
    return os.path.join(os.environ.get('CODEX_HOME') or os.path.expanduser('~/.codex'), 'models_cache.json')


def model_options(codex_cache=None):
    """The models and thinking efforts New team offers. Codex's come from the list Codex keeps on this Mac
    (an internal file: if it's missing or changes shape, Codex agents get their default only)."""
    claude = list(launch.EFFORTS['claude'])
    codex = []
    try:
        with open(codex_cache or _codex_cache()) as f:
            for m in json.load(f).get('models', []):
                slug = m.get('slug') or ''
                levels = [x.get('effort') for x in m.get('supported_reasoning_levels') or [] if isinstance(x, dict)]
                if launch.MODEL.match(slug):
                    codex.append({'id': slug, 'label': slug, 'about': str(m.get('description') or ''),
                                  'efforts': [e for e in launch.EFFORTS['codex'] if e in levels]})
    except (OSError, ValueError, AttributeError, TypeError):
        codex = []
    common = [e for e in launch.EFFORTS['codex'] if codex and all(e in m['efforts'] for m in codex)]
    return {'claude': {'models': [{'id': i, 'label': n, 'efforts': claude} for i, n in CLAUDE_MODELS],
                       'efforts': claude},
            'codex': {'models': codex, 'efforts': common},
            **{k: {'models': [], 'efforts': []} for k in ('gemini', 'opencode', 'cursor', 'antigravity')}}  # their own default only


def project_check(path):
    if not setup.git_ready():
        if sys.platform != 'darwin' or os.environ.get('ASSIEME_HC_SOCKET'):  # a remote machine: nothing to click here
            return {'ok': False, 'reason': NO_GIT}
        return {'ok': False, 'reason': NO_TOOLS, 'tools': True}
    path = os.path.expanduser(path)
    if not os.path.isdir(path):
        return {'ok': False, 'reason': "This folder doesn't exist."}
    too_large = {'ok': False, 'reason': TOO_LARGE, 'too_large': True}
    if setup.too_broad(path):  # before anything walks the folder
        return too_large
    try:
        worktree.check_repo(path)
    except worktree.WorktreeError:  # no repository, or no commits
        try:
            setup.folder_preview(path)  # counts the files a first version would hold
        except setup.TooLarge:
            return too_large
        except setup.SetupError:
            pass
        return {'ok': False, 'reason': NEEDS_SAVE, 'setup': True}
    try:
        n = setup.uncommitted(path)
    except setup.TooLarge:
        return too_large
    except setup.Unfinished as e:
        return {'ok': False, 'reason': str(e)}  # shown as the folder's problem; nothing is offered to save
    except setup.SetupError:
        n = 0
    return {'ok': True, 'uncommitted': n} if n else {'ok': True}


def _container_ready(roles):
    for r in roles:
        problem = r.container and container.problem(r.agent)
        if problem:
            raise NewTeamError('start', f'{r.name} runs in a container. {problem}')


def validate(roles):
    if not roles:
        raise NewTeamError('roles', 'Add at least one agent.')
    names = [r.get('name', '') for r in roles]
    for i, r in enumerate(roles):
        field = f'role:{i}'
        if not NAME.match(names[i]) or names[i] in names[:i]:
            raise NewTeamError(field, BAD_NAME)
        if r.get('agent') not in preset.AGENTS:
            raise NewTeamError(field, 'Choose which agent runs this.')
        if any(r.get(k) not in (None, True, False) for k in ('container', 'share_signin')):
            raise NewTeamError(field, 'Choose where this agent works.')
        if r.get('checkout', 'branch') not in preset.CHECKOUTS:
            raise NewTeamError(field, f"Unknown checkout {r.get('checkout')!r}.")
        if r.get('worktree') and not os.path.isabs(r['worktree']):
            raise NewTeamError(field, 'Its folder needs a full path.')
        if r.get('branch') and not BRANCH.match(r['branch']):
            raise NewTeamError(field, f"{r['branch']!r} isn't a branch name Assieme can use.")
        if r.get('parent') and r['parent'] not in names:
            raise NewTeamError(field, f"Reports to {r['parent']!r}, which isn't in this team.")
        if not (r.get('brief') or '').strip():
            raise NewTeamError(field, 'Every agent needs a brief.')
        problem = launch.check_model_effort(r['agent'], r.get('model'), r.get('effort'))
        if r.get('kind') is not None and r['kind'] not in KINDS:
            problem = problem or f"Unknown role {r['kind']!r}."
        if r.get('level') is not None and r['level'] not in permissions.LEVELS:
            problem = problem or f"Unknown permission level {r['level']!r}."
        problem = problem or permissions.check_owns(r.get('owns') or []) or permissions.check_rules(r.get('rules') or [])
        if problem:
            raise NewTeamError(field, problem)


def team_name(project, taken):
    base = re.sub(r'[^a-z0-9]+', '-', os.path.basename(os.path.normpath(project)).lower()).strip('-') or 'team'
    name, n = base, 2
    while name in taken:
        name, n = f'{base}-{n}', n + 1
    return name


def _toml_str(v):
    return json.dumps(v, ensure_ascii=False)  # ASCII escapes would split emoji into surrogates TOML rejects


def _role_toml(r):
    q = _toml_str
    out = ['[[role]]', f'name = {q(r["name"])}', f'agent = {q(r["agent"])}', f'job = {q(r.get("job", ""))}',
           f'checkout = {q(r.get("checkout", "branch"))}']
    out += [f'{key} = {q(r[key])}' for key in ('parent', 'model', 'effort', 'group', 'kind', 'level', 'worktree', 'branch',
                                                'resume') if r.get(key)]
    out += [f'{key} = true' for key in ('container', 'share_signin') if r.get(key) is True]
    if r.get('owns'):
        out.append(f'owns = {json.dumps(r["owns"], ensure_ascii=False)}')  # a JSON list of strings is a TOML array
    if r.get('rules'):
        tables = ['{' + f'decision = {q(x["decision"])}, program = {json.dumps(x["program"], ensure_ascii=False)}, '
                  f'words = {q(x.get("words", ""))}' + '}' for x in r['rules']]
        out.append(f'rules = [{", ".join(tables)}]')
    return out + ['']


def forget_role(folder, name):
    """Take a role back out of a saved team, so it doesn't return at the next start and its name is free again."""
    tm = preset.load(folder, practices=False)
    with open(os.path.join(folder, 'team.toml'), 'rb') as f:
        data = tomllib.load(f)
    keep = [r for r in data.get('role', []) if r.get('name') != name]
    by_name = {r.name: r for r in tm.roles}
    gone = by_name.get(name)
    roles = [_role_dict(by_name[r['name']]) for r in keep]
    for r in roles:  # whoever reported to it now reports one level up, or the saved team would no longer load
        if r['parent'] == name:
            r['parent'] = gone.parent if gone else None
    save(tm.project, roles, tm.practices, tm.name, os.path.dirname(folder), tm.scripts)


def _rewrite_role(folder, name, **changes):
    """Save the team again with one role's fields changed, in the file's order and with its scripts."""
    tm = preset.load(folder, practices=False)
    with open(os.path.join(folder, 'team.toml'), 'rb') as f:
        order = [r['name'] for r in tomllib.load(f).get('role', [])]
    by_name = {r.name: _role_dict(r) for r in tm.roles}
    if name in by_name:
        by_name[name].update(changes)
    save(tm.project, [by_name[n] for n in order], tm.practices, tm.name, os.path.dirname(folder), tm.scripts)


def clear_resume(folder, name):
    """After a brought-over agent's first start: later starts don't resume that conversation again."""
    _rewrite_role(folder, name, resume=None)


def clear_worktree(folder, name):
    """After merging a brought-over agent: its next start makes an Assieme copy; the old folder stays where it is."""
    _rewrite_role(folder, name, worktree=None, branch=None)


def roster(name, teams=None):
    """What the saved team folder says about each role. A team started from the command line has none."""
    try:
        tm = preset.load(os.path.join(teams or teams_dir(), name))  # with the roles bigger-team presets add
    except (preset.PresetError, OSError):
        return {}
    return {r.name: {'job': r.job, 'group': r.group, 'parent': r.parent} for r in tm.roles}


def save(project, roles, practices, name, teams, scripts=None):
    """Write the team folder `preset.load` reads; TOML strings are written as JSON strings, which TOML accepts.
    `scripts`: setup, run and archive lines brought over from another tool (step 30)."""
    scripts = {k: v for k, v in (scripts or {}).items() if v}
    unknown = set(scripts) - set(preset.SCRIPTS)
    if unknown:
        raise ValueError(f'Unknown script {", ".join(sorted(unknown))}.')
    folder = os.path.join(teams, name)
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
    q = _toml_str
    out = [f'name = {q(name)}', f'project = {q(os.path.abspath(project))}', '']
    if practices:
        out += ['[practices]'] + [f'{k} = {"true" if v else "false"}' for k, v in practices.items()] + ['']
    if scripts:
        out += ['[scripts]'] + [f'{k} = {q(v)}' for k, v in scripts.items()] + ['']
    for r in roles:
        out += _role_toml(r)
        with open(os.path.join(folder, f'{r["name"]}.md'), 'w') as f:
            f.write(r['brief'].rstrip('\n') + '\n')
    with open(os.path.join(folder, 'team.toml'), 'w') as f:
        f.write('\n'.join(out))
    return folder


class Starter:
    """Starts teams for the app: checks and saves at once, then runs 7a's `team.start` in a thread, keeping a
    progress record per team for the snapshot. Records live in memory; the teams themselves outlive the brain."""

    def __init__(self, rt, teams, thread=threading.Thread, now=time.time, which=setup.agent_path, scheduled=set):
        """`scheduled()` names the teams of scheduled runs, which may share a project with another team."""
        self.rt, self.teams, self.thread, self.now, self.which = rt, teams, thread, now, which
        self.scheduled = scheduled
        self.lock, self.recs, self.busy = threading.Lock(), [], {}  # busy: project → name, while checks run

    def _live_names(self):
        try:
            return {r['team'] for r in team.status(self.rt) if r['state'] == 'live'}
        except runtime.RuntimeError_:
            return set()  # the assieme core isn't running yet: no live teams

    def _project_of(self, name):
        try:
            with open(os.path.join(self.teams, name, 'team.toml'), 'rb') as f:
                return tomllib.load(f).get('project')
        except (OSError, tomllib.TOMLDecodeError):
            return None  # started from the command line, or the folder is gone

    def start(self, project, task, roles, practices, name=None, scripts=None):
        """`name` is a scheduled run's own team name; New team leaves it to the project's folder name."""
        validate(roles)
        project = os.path.expanduser(project)
        check = project_check(project)
        if not check['ok']:
            raise NewTeamError('project', check['reason'])
        project = os.path.abspath(project)
        live = self._live_names()
        with self.lock:  # held only for bookkeeping: git work below must not block records()
            if project in self.busy or any(r['project'] == project and not r['done'] for r in self.recs):
                raise NewTeamError('start', 'This team is still starting.')
            sched, mine = self.scheduled(), {r['name'] for r in roles}

            def blocks(other):
                if mine & set(roster(other, self.teams)):  # worktrees are per role name: these would share one
                    return True
                return other not in sched and name not in sched  # New team beside New team: one per project
            running = sorted(n for n in live if self._project_of(n) == project and blocks(n))
            if running:
                raise NewTeamError('project', f'This project already has a running team: {running[0]}. Stop it first.')
            taken = live | set(self.busy.values()) | {r['team'] for r in self.recs if not r['done']}
            if name in taken:
                raise NewTeamError('start', f'{name} is still running.')
            name = self.busy[project] = name or team_name(project, taken)
        try:
            folder = save(project, roles, practices, name, self.teams, scripts)
            try:
                tm = preset.load(folder)
            except preset.PresetError as e:
                raise NewTeamError('start', str(e)) from None
            missing = launch.missing_clis(tm.roles, which=self.which)
            if missing:
                raise NewTeamError('start', f'not installed: {", ".join(missing)}. Install it, then start again.')
            _container_ready(tm.roles)
            try:
                # Copies made by this start get the team's setup script; brought-over folders are already set up.
                fresh = {r.name: worktree.folder(tm, r) for r in tm.roles
                         if r.checkout != 'read-only' and not (r.worktree and os.path.isdir(r.worktree))
                         and not os.path.isdir(worktree.folder(tm, r))}
                for r in tm.roles:  # every worktree now, so a dirty one is reported to the form, not the log
                    worktree.ensure(tm, r)
            except worktree.WorktreeError as e:
                raise NewTeamError('start', str(e)) from None
        except Exception:
            with self.lock:
                self.busy.pop(project, None)
            raise
        rec = {'team': name, 'project': project, 'done': False, 'error': None, 'done_at': None,
               'roles': [{'role': r.name, 'state': 'starting', 'detail': ''} for r in tm.roles]}  # with preset-added roles
        with self.lock:
            self.busy.pop(project, None)
            self.recs.append(rec)
        self.thread(target=lambda: self._run(rec, tm, task, fresh), daemon=True).start()
        return {'team': name}

    def _setup(self, rec, tm, fresh):
        """The team's setup script in each copy this start made, when the person has said yes to it; otherwise the
        record asks for that yes, and the agents start without it."""
        line = tm.scripts.get('setup')
        if not line or not fresh:
            return
        if not scripts.allowed(tm.name, 'setup', line):
            rec['ask_scripts'] = [{'which': 'setup', 'line': line}]
            return
        for name, path in fresh.items():
            got = scripts.run(line, path, tm.project)
            live.record('setup', name, 'Ran the team setup in its copy' if got['ok'] else "The team setup didn't finish",
                        ok=got['ok'], detail=got['output'], team=tm.name, by='assieme')

    def _run(self, rec, tm, task, fresh=None):
        try:
            self._setup(rec, tm, fresh or {})
            # Clicking Start in the app is the person's agreement to trust the folder.
            results = {s.role: s for s in team.start(tm, task, self.rt, ask=lambda q: True)}
            for r in tm.roles:  # a conversation brought over is picked up once; later starts begin fresh
                if r.resume and results.get(r.name) and results[r.name].address:
                    clear_resume(os.path.join(self.teams, tm.name), r.name)
            with self.lock:
                for r in rec['roles']:
                    s = results.get(r['role'])
                    if s:
                        r['state'], r['detail'] = s.state, s.note
        except Exception as e:  # anything here would otherwise vanish with the thread
            rec['error'] = str(e)
        finally:
            with self.lock:
                rec['done'], rec['done_at'] = True, self.now()

    def add_agent(self, team_name, role):
        """One more agent for a running team: added to its saved folder, then started like any other."""
        folder = os.path.join(self.teams, team_name)
        if not os.path.isfile(os.path.join(folder, 'team.toml')):
            raise NewTeamError('start', "This team was started from the command line, so agents can't be added here.")
        try:
            rows = self.rt.list({team.TEAM: team_name}, all=False)
        except runtime.RuntimeError_:
            rows = []  # no core running means nothing of this team is
        if not any(r.get('state') == 'live' and (r.get('labels') or {}).get(team.TEAM) == team_name for r in rows):
            raise NewTeamError('start', 'This team has stopped. Start it again to add an agent.')
        role = dict(role)
        kind = role.pop('kind', None)
        if kind in KINDS:
            role.setdefault('checkout', KINDS[kind][1])
            if not (role.get('brief') or '').strip():
                role['brief'] = kind_brief(kind)
        tm = preset.load(folder, practices=False)
        have = [_role_dict(r) for r in tm.roles]
        validate(have + [role])
        new = preset.Role(role['name'], role['agent'], '', '', container=role.get('container') is True)
        missing = launch.missing_clis([new], which=self.which)
        if missing:
            raise NewTeamError('start', f'not installed: {", ".join(missing)}. Install it, then try again.')
        _container_ready([new])
        name = role['name']
        with open(os.path.join(folder, f'{name}.md'), 'w') as f:
            f.write(role['brief'].rstrip('\n') + '\n')
        with open(os.path.join(folder, 'team.toml'), 'a') as f:
            f.write('\n'.join(_role_toml(role)) + '\n')
        tm = preset.load(folder)
        try:
            worktree.ensure(tm, next(r for r in tm.roles if r.name == name))
        except worktree.WorktreeError as e:
            forget_role(folder, name)
            raise NewTeamError('start', str(e)) from None
        rec = {'team': team_name, 'project': tm.project, 'done': False, 'error': None, 'done_at': None,
               'roles': [{'role': name, 'state': 'starting', 'detail': ''}]}
        with self.lock:
            self.recs.append(rec)
        self.thread(target=lambda: self._run_added(rec, tm, name, folder), daemon=True).start()
        return {'team': team_name}

    def _run_added(self, rec, tm, name, folder):
        try:
            [s] = team.start(tm, None, self.rt, ask=lambda q: True, only=name)
            with self.lock:
                rec['roles'][0]['state'], rec['roles'][0]['detail'] = s.state, s.note
            if s.state == 'exited':
                forget_role(folder, name)
            else:
                live.record('add_agent', name, 'You added it to this team.', team=tm.name, address=s.address)
        except Exception as e:
            rec['error'] = str(e)
            forget_role(folder, name)
        finally:
            with self.lock:
                rec['done'], rec['done_at'] = True, self.now()

    def records(self):
        with self.lock:
            now = self.now()
            self.recs = [r for r in self.recs if not r['done'] or now - r['done_at'] <= KEEP_DONE_S]
            return [{k: v for k, v in r.items() if k != 'done_at'} | {'roles': [dict(x) for x in r['roles']]}
                    for r in self.recs]


SUGGEST_TIMEOUT_S = 170
MAX_ROLES = 8
_WORDS = {'size': {'small': 'a small fix', 'feature': 'a feature', 'large': 'a large change'},
          'review': {'every': 'every change', 'end': 'just the end result'},
          'budget': {'lean': 'keep it lean (fewer agents, cheaper)', 'fast': 'speed matters more than cost'}}
_KIND_JOBS = {'planner': 'reads the project, splits the task and hands out pieces; never edits',
              'builder': 'makes one change at a time with tests, on its own branch',
              'reviewer': 'reads every diff and gives a verdict; never edits',
              'tester': 'writes tests and runs the full suite, on its own branch'}


def _models_text(agents, models):
    out = []
    for a in agents:
        ms = models.get(a, {'models': []})['models']
        if a == 'claude':
            out.append(f'- claude models, strongest and most expensive first: {", ".join(m["id"] for m in ms)}. '
                       f'Efforts: {", ".join(models[a]["efforts"])}')
        elif not ms:
            out.append(f'- {a}: its own default only; leave model and effort out')
        else:
            out += [f'- {a} model {m["id"]} ({m.get("about") or "no description"}). Efforts: {", ".join(m["efforts"])}'
                    for m in ms]
    return '\n'.join(out)


def _prompt(size, review, budget, agents, models):
    kinds = '\n'.join(f'- {k}: {v}' for k, v in _KIND_JOBS.items())
    return (f'Suggest an agent team for the code in this folder. Read enough of the project to judge it.\n\n'
            f'The change is {_WORDS["size"].get(size, size)}. The person wants to review '
            f'{_WORDS["review"].get(review, review)}. Budget: {_WORDS["budget"].get(budget, budget)}.\n\n'
            f'Role kinds you can use:\n{kinds}\n\n'
            f'Use 1 to {MAX_ROLES} agents. Each agent is one of these, the ones installed: '
            f'{", ".join(agents)}. Names use lower-case letters, digits and dashes, and differ.\n\n'
            'Also choose each agent\'s model and thinking effort. Think harder where mistakes cost most (a reviewer '
            'usually thinks harder than a builder); on a lean budget prefer cheaper models and lower effort. Leave '
            'either out to keep that agent\'s own default. Only these exist on this machine:\n'
            f'{_models_text(agents, models)}\n\n'
            'Reply with only one JSON object: {"roles": [{"name": "...", "kind": "...", "agent": "...", '
            '"job": "one short line", "model": "...", "effort": "..."}], "why": "one sentence"}')


def _model_effort(r, cli):
    """The suggested model and effort if this machine can run them; anything else is Default, which always runs."""
    chosen = next((m for m in cli['models'] if m['id'] == r.get('model')), None)
    effort = r.get('effort')
    return (chosen and chosen['id'],
            effort if isinstance(effort, str) and effort in (chosen or cli)['efforts'] else None)


def _team_from(text, installed, models):
    data = ask.first_json(text)
    if not isinstance(data, dict) or not isinstance(data.get('roles'), list):
        raise ValueError("the answer wasn't a team.")
    raw = data['roles']
    if not raw:
        raise ValueError('no agents in the answer.')
    if len(raw) > MAX_ROLES:
        raise ValueError(f'more than {MAX_ROLES} agents.')
    planner = next((r.get('name') for r in raw if isinstance(r, dict) and r.get('kind') == 'planner'), None)
    roles = []
    for r in raw:
        kind = r.get('kind') if isinstance(r, dict) else None
        if kind not in KINDS:
            raise ValueError(f'unknown role kind {kind!r}.')
        if r.get('agent') not in installed:
            raise ValueError(f"it chose {r.get('agent')}, which is not installed.")
        model, effort = _model_effort(r, models.get(r['agent'], {'models': [], 'efforts': []}))
        roles.append({'name': str(r.get('name', '')), 'agent': r['agent'], 'job': str(r.get('job', '')),
                      'checkout': KINDS[kind][1], 'parent': planner if kind != 'planner' else None,
                      'brief': kind_brief(kind), 'model': model, 'effort': effort})
    return {'roles': roles, 'why': str(data.get('why', ''))}


def suggest(project, size, review, budget, run=subprocess.run, which=setup.agent_path, models=None):
    """Help me choose: the person's own Claude Code (else Codex), read-only in the project, suggests a team
    with each agent's model and thinking effort, among what New team offers on this machine."""
    have = ask.installed(which)
    models = models or model_options()
    pre = "Couldn't get a suggestion: "
    try:
        text = ask.ask_agent(_prompt(size, review, budget, have, models), project, SUGGEST_TIMEOUT_S, run=run, which=which,
                             timeout_label='3 minutes')
    except ask.AskError as e:
        raise NewTeamError('suggest', str(e) if e.missing else pre + str(e)) from None
    try:
        return _team_from(text, have, models)
    except ValueError as e:
        raise NewTeamError('suggest', pre + str(e)) from None


IMPROVE_TIMEOUT_S = 170
BRIEF_MAX = 6000


def _brief_prompt(role):
    kind = role.get('kind')
    lines = [f'Role: {kind} ({_KIND_JOBS[kind]}).' if kind in _KIND_JOBS else 'Role: custom.',
             f'Its job in one line: {role.get("job") or "(not written yet)"}']
    if role.get('owns'):
        lines.append(f'Files only it edits: {", ".join(role["owns"])}')
    if role.get('parent'):
        lines.append(f'It reports to the agent named {role["parent"]}.')
    return ('Improve the brief below for one AI coding agent in a team working on the code in this folder. Read enough '
            'of the project to make it concrete: name the real files, commands and checks. Keep it short: one line on '
            'what it does, then a few bullet points. Keep every instruction the person wrote unless it is wrong for '
            'this project. Leave out reporting format, stop rules and handoff format: they are added automatically.\n\n'
            + '\n'.join(lines) + '\n\nThe current brief, between triple quotes:\n"""\n'
            + (role.get('brief') or '').strip() + '\n"""\n\nReply with only one JSON object: {"brief": "..."}')


def improve_brief(project, role, run=subprocess.run, which=setup.agent_path):
    """Improve this brief: the person's own Claude Code (else Codex), read-only in the project, rewrites it.
    Nothing is saved: the editor shows the result and the person keeps or undoes it."""
    pre = "Couldn't improve the brief: "
    try:
        text = ask.ask_agent(_brief_prompt(role), project, IMPROVE_TIMEOUT_S, run=run, which=which,
                             timeout_label='3 minutes')
    except ask.AskError as e:
        raise NewTeamError('suggest', str(e) if e.missing else pre + str(e)) from None
    got = (ask.first_json(text) or {}).get('brief')
    if not isinstance(got, str) or not got.strip() or len(got) > BRIEF_MAX:
        raise NewTeamError('suggest', pre + "the answer wasn't a brief.")
    return {'brief': got.strip() + '\n'}


RULE_TIMEOUT_S = 120
_RULE_PROMPT = '''A person is setting what one AI coding agent may run in the project in this folder. They wrote a rule in
plain words, quoted below between triple quotes. Turn it into one rule on the program the agent runs.

"""
{words}
"""

A rule is the program and, if needed, its first few arguments, matched as a prefix of the command run: ["npm",
"publish"] matches "npm publish --tag next". If the words name something in this project (a script, a launcher, a
tool), read the project to find the exact path or command, such as ["build/cloud/run.sh"]. Words only: no spaces
inside a word, no wildcards, no shell symbols.

Reply with only one JSON object: {{"decision": "block" or "allow", "program": ["...", "..."]}}
If the words don't describe running a program, reply {{"unclear": "one short sentence saying why"}}'''


def rule_draft(project, words, run=subprocess.run, which=setup.agent_path):
    """Add a rule: plain words to a rule on the program run, for the person to see before Add. Nothing is saved."""
    words = (words or '').strip()
    if not words:
        raise NewTeamError('suggest', 'Write the rule in a few words first.')
    pre = "Couldn't turn that into a rule: "
    try:
        text = ask.ask_agent(_RULE_PROMPT.format(words=words[:300]), project, RULE_TIMEOUT_S, run=run, which=which,
                             timeout_label='2 minutes')
    except ask.AskError as e:
        raise NewTeamError('suggest', str(e) if e.missing else pre + str(e)) from None
    data = ask.first_json(text) or {}
    if isinstance(data.get('unclear'), str):
        raise NewTeamError('suggest', pre + data['unclear'])
    try:
        program = permissions.check_rule(data.get('decision'), data.get('program'))
    except ValueError as e:
        raise NewTeamError('suggest', pre + str(e)) from None
    return {'decision': data['decision'], 'program': program, 'words': words}
