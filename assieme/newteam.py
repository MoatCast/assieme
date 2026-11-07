"""New team in the app: the starting teams, checks, saving a team folder, starting it in the background, and
Help me choose. The app sends the form; this module turns it into an ordinary team folder for `preset.load`."""
import os
import tomllib

from . import preset

PRESETS_DIR = os.path.join(os.path.dirname(__file__), 'presets')
BUILTIN = ('one-agent', 'builder-reviewer', 'full')
KINDS = {'planner': ('planner.md', 'read-only'), 'builder': ('builder.md', 'branch'),
         'reviewer': ('reviewer.md', 'read-only'), 'tester': ('tester.md', 'branch')}


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
