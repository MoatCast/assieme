"""New team in the app: the starting teams, checks, saving a team folder, starting it in the background, and
Help me choose. The app sends the form; this module turns it into an ordinary team folder for `preset.load`."""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import tomllib

from . import ask, live, launch, preset, runtime, settings, team, worktree

PRESETS_DIR = os.path.join(os.path.dirname(__file__), 'presets')
BUILTIN = ('one-agent', 'builder-reviewer', 'full')
KINDS = {'planner': ('planner.md', 'read-only'), 'builder': ('builder.md', 'branch'),
         'reviewer': ('reviewer.md', 'read-only'), 'tester': ('tester.md', 'branch')}


NAME = re.compile(r'^[a-z0-9-]+$')
BAD_NAME = 'Names use lower-case letters, digits and dashes, and must differ.'
KEEP_DONE_S = 600  # a finished start stays on First run for 10 minutes
NOT_GIT = "This folder isn't a git repository. Assieme gives each agent its own branch, so it needs one."
NO_COMMITS = "This repository has no commits yet. Agents branch from the latest commit, so make a first commit, then choose it again."


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
            'brief': r.brief, 'model': r.model, 'effort': r.effort, 'group': r.group}


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
    return {'presets': builtin, 'practices': preset.practices(), 'last': last, 'models': model_options(),
            'practice_defaults': settings.load()['practices']}


CLAUDE_MODELS = (('fable', 'Fable'), ('opus', 'Opus'), ('sonnet', 'Sonnet'))  # Claude Code's own aliases


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
                    codex.append({'id': slug, 'label': slug,
                                  'efforts': [e for e in launch.EFFORTS['codex'] if e in levels]})
    except (OSError, ValueError, AttributeError, TypeError):
        codex = []
    common = [e for e in launch.EFFORTS['codex'] if codex and all(e in m['efforts'] for m in codex)]
    return {'claude': {'models': [{'id': i, 'label': n, 'efforts': claude} for i, n in CLAUDE_MODELS],
                       'efforts': claude},
            'codex': {'models': codex, 'efforts': common}}


def project_check(path):
    if not os.path.isdir(path):
        return {'ok': False, 'reason': "This folder doesn't exist."}
    try:
        worktree.check_repo(path)
    except worktree.NoCommitsError:
        return {'ok': False, 'reason': NO_COMMITS}
    except worktree.WorktreeError:
        return {'ok': False, 'reason': NOT_GIT}
    return {'ok': True}


def validate(roles):
    if not roles:
        raise NewTeamError('roles', 'Add at least one agent.')
    names = [r.get('name', '') for r in roles]
    for i, r in enumerate(roles):
        field = f'role:{i}'
        if not NAME.match(names[i]) or names[i] in names[:i]:
            raise NewTeamError(field, BAD_NAME)
        if r.get('agent') not in preset.AGENTS:
            raise NewTeamError(field, 'Choose Claude Code or Codex for this agent.')
        if r.get('checkout', 'branch') not in preset.CHECKOUTS:
            raise NewTeamError(field, f"Unknown checkout {r.get('checkout')!r}.")
        if r.get('parent') and r['parent'] not in names:
            raise NewTeamError(field, f"Reports to {r['parent']!r}, which isn't in this team.")
        if not (r.get('brief') or '').strip():
            raise NewTeamError(field, 'Every agent needs a brief.')
        problem = launch.check_model_effort(r['agent'], r.get('model'), r.get('effort'))
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
    out += [f'{key} = {q(r[key])}' for key in ('parent', 'model', 'effort', 'group') if r.get(key)]
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
    save(tm.project, roles, tm.practices, tm.name, os.path.dirname(folder))


def roster(name, teams=None):
    """What the saved team folder says about each role. A team started from the command line has none."""
    try:
        tm = preset.load(os.path.join(teams or teams_dir(), name), practices=False)
    except (preset.PresetError, OSError):
        return {}
    return {r.name: {'job': r.job, 'group': r.group, 'parent': r.parent} for r in tm.roles}


def save(project, roles, practices, name, teams):
    """Write the team folder `preset.load` reads; TOML strings are written as JSON strings, which TOML accepts."""
    folder = os.path.join(teams, name)
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
    q = _toml_str
    out = [f'name = {q(name)}', f'project = {q(os.path.abspath(project))}', '']
    if practices:
        out += ['[practices]'] + [f'{k} = {"true" if v else "false"}' for k, v in practices.items()] + ['']
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

    def __init__(self, rt, teams, thread=threading.Thread, now=time.time, which=shutil.which):
        self.rt, self.teams, self.thread, self.now, self.which = rt, teams, thread, now, which
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

    def start(self, project, task, roles, practices):
        validate(roles)
        check = project_check(project)
        if not check['ok']:
            raise NewTeamError('project', check['reason'])
        project = os.path.abspath(project)
        live = self._live_names()
        with self.lock:  # held only for bookkeeping: git work below must not block records()
            if project in self.busy or any(r['project'] == project and not r['done'] for r in self.recs):
                raise NewTeamError('start', 'This team is still starting.')
            running = sorted(n for n in live if self._project_of(n) == project)
            if running:  # a second team here would share the first team's worktrees
                raise NewTeamError('project', f'This project already has a running team: {running[0]}. Stop it first.')
            taken = live | set(self.busy.values()) | {r['team'] for r in self.recs if not r['done']}
            name = self.busy[project] = team_name(project, taken)
        try:
            folder = save(project, roles, practices, name, self.teams)
            try:
                tm = preset.load(folder)
            except preset.PresetError as e:
                raise NewTeamError('start', str(e)) from None
            missing = launch.missing_clis(tm.roles, which=self.which)
            if missing:
                raise NewTeamError('start', f'not installed: {", ".join(missing)}. Install it, then start again.')
            try:
                for r in tm.roles:  # every worktree now, so a dirty one is reported to the form, not the log
                    worktree.ensure(tm, r)
            except worktree.WorktreeError as e:
                raise NewTeamError('start', str(e)) from None
        except Exception:
            with self.lock:
                self.busy.pop(project, None)
            raise
        rec = {'team': name, 'project': project, 'done': False, 'error': None, 'done_at': None,
               'roles': [{'role': r['name'], 'state': 'starting', 'detail': ''} for r in roles]}
        with self.lock:
            self.busy.pop(project, None)
            self.recs.append(rec)
        self.thread(target=lambda: self._run(rec, tm, task), daemon=True).start()
        return {'team': name}

    def _run(self, rec, tm, task):
        try:
            # Clicking Start in the app is the person's agreement to trust the folder.
            results = {s.role: s for s in team.start(tm, task, self.rt, ask=lambda q: True)}
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
        missing = launch.missing_clis([preset.Role(role['name'], role['agent'], '', '')], which=self.which)
        if missing:
            raise NewTeamError('start', f'not installed: {", ".join(missing)}. Install it, then try again.')
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


def _prompt(size, review, budget, agents):
    kinds = '\n'.join(f'- {k}: {v}' for k, v in _KIND_JOBS.items())
    return (f'Suggest an agent team for the code in this folder. Read enough of the project to judge it.\n\n'
            f'The change is {_WORDS["size"].get(size, size)}. The person wants to review '
            f'{_WORDS["review"].get(review, review)}. Budget: {_WORDS["budget"].get(budget, budget)}.\n\n'
            f'Role kinds you can use:\n{kinds}\n\n'
            f'Use 1 to {MAX_ROLES} agents. Each agent is "claude" or "codex"; only these are installed: '
            f'{", ".join(agents)}. Names use lower-case letters, digits and dashes, and differ.\n\n'
            'Reply with only one JSON object: {"roles": [{"name": "...", "kind": "...", "agent": "...", '
            '"job": "one short line"}], "why": "one sentence"}')


def _team_from(text, installed):
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
        roles.append({'name': str(r.get('name', '')), 'agent': r['agent'], 'job': str(r.get('job', '')),
                      'checkout': KINDS[kind][1], 'parent': planner if kind != 'planner' else None,
                      'brief': kind_brief(kind)})
    return {'roles': roles, 'why': str(data.get('why', ''))}


def suggest(project, size, review, budget, run=subprocess.run, which=shutil.which):
    """Help me choose: the person's own Claude Code (else Codex), read-only in the project, suggests a team."""
    have = ask.installed(which)
    pre = "Couldn't get a suggestion: "
    try:
        text = ask.ask_agent(_prompt(size, review, budget, have), project, SUGGEST_TIMEOUT_S, run=run, which=which,
                             timeout_label='3 minutes')
    except ask.AskError as e:
        raise NewTeamError('suggest', str(e) if e.missing else pre + str(e)) from None
    try:
        return _team_from(text, have)
    except ValueError as e:
        raise NewTeamError('suggest', pre + str(e)) from None
