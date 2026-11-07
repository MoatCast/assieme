"""New team in the app: the starting teams, checks, saving a team folder, starting it in the background, and
Help me choose. The app sends the form; this module turns it into an ordinary team folder for `preset.load`."""
import json
import os
import re
import shutil
import tomllib

from . import preset, worktree

PRESETS_DIR = os.path.join(os.path.dirname(__file__), 'presets')
BUILTIN = ('one-agent', 'builder-reviewer', 'full')
KINDS = {'planner': ('planner.md', 'read-only'), 'builder': ('builder.md', 'branch'),
         'reviewer': ('reviewer.md', 'read-only'), 'tester': ('tester.md', 'branch')}


NAME = re.compile(r'^[a-z0-9-]+$')
BAD_NAME = 'Names use lower-case letters, digits and dashes, and must differ.'
NOT_GIT = "This folder isn't a git repository. Assieme gives each agent its own branch, so it needs one."


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
            'brief': r.brief}


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
    return {'presets': builtin, 'practices': preset.practices(), 'last': last}


def project_check(path):
    if not os.path.isdir(path):
        return {'ok': False, 'reason': "This folder doesn't exist."}
    try:
        worktree.check_repo(path)
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


def team_name(project, taken):
    base = re.sub(r'[^a-z0-9]+', '-', os.path.basename(os.path.normpath(project)).lower()).strip('-') or 'team'
    name, n = base, 2
    while name in taken:
        name, n = f'{base}-{n}', n + 1
    return name


def save(project, roles, practices, name, teams):
    """Write the team folder `preset.load` reads; TOML strings are written as JSON strings, which TOML accepts."""
    folder = os.path.join(teams, name)
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
    q = json.dumps
    out = [f'name = {q(name)}', f'project = {q(os.path.abspath(project))}', '']
    if practices:
        out += ['[practices]'] + [f'{k} = {"true" if v else "false"}' for k, v in practices.items()] + ['']
    for r in roles:
        out += ['[[role]]', f'name = {q(r["name"])}', f'agent = {q(r["agent"])}', f'job = {q(r.get("job", ""))}',
                f'checkout = {q(r.get("checkout", "branch"))}']
        if r.get('parent'):
            out.append(f'parent = {q(r["parent"])}')
        out.append('')
        with open(os.path.join(folder, f'{r["name"]}.md'), 'w') as f:
            f.write(r['brief'].rstrip('\n') + '\n')
    with open(os.path.join(folder, 'team.toml'), 'w') as f:
        f.write('\n'.join(out))
    return folder
