"""Team presets: a folder with team.toml and one Markdown brief per role (briefs use hc's template format)."""
import os
import tomllib

from . import launch
from dataclasses import dataclass, field

AGENTS = ('claude', 'codex')
PRACTICES = os.path.join(os.path.dirname(__file__), 'practices.toml')
CHECKOUTS = ('branch', 'read-only')


class PresetError(ValueError):
    pass


@dataclass
class Role:
    name: str
    agent: str
    brief_path: str
    brief: str
    model: str | None = None
    checkout: str = 'branch'
    parent: str | None = None
    job: str = ''
    effort: str | None = None
    group: str | None = None  # where it sits on the map of a big team


@dataclass
class Team:
    name: str
    project: str
    practices: dict = field(default_factory=dict)
    roles: list = field(default_factory=list)


def _body(text):
    """The brief without hc's `---` header."""
    if text.startswith('---\n'):
        end = text.find('\n---', 4)
        if end != -1:
            return text[end + 4:].lstrip('\n')
    return text


def _parents_first(roles):
    by_name, out, seen = {r.name: r for r in roles}, [], set()

    def visit(r, path):
        if r.name in seen:
            return
        if r.name in path:
            raise PresetError(f'roles form a parent loop: {" -> ".join(path + [r.name])}')
        if r.parent:
            visit(by_name[r.parent], path + [r.name])
        seen.add(r.name)
        out.append(r)

    for r in roles:
        visit(r, [])
    return out


def practices(path=PRACTICES):
    with open(path, 'rb') as f:
        data = tomllib.load(f)
    return {'groups': data.get('group', []), 'basics': data.get('basic', [])}


def _groups():  # `load`'s `practices` argument hides the function inside it
    return practices()['groups']


def _practice_rules(chosen):
    lines = [f'- {rule}' for g in _groups() if chosen.get(g['id'], True) is not False for rule in g['rules']]
    return '\n\n## Team practices\n\n' + '\n'.join(lines) + '\n' if lines else ''


def load(folder, practices=True):
    path = os.path.join(folder, 'team.toml')
    try:
        with open(path, 'rb') as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise PresetError(f'no team.toml in {folder}') from None
    except tomllib.TOMLDecodeError as e:
        raise PresetError(f'{path}: {e}') from None
    name = data.get('name')
    if not name:
        raise PresetError(f'{path}: the team needs a name')
    project = os.path.abspath(os.path.join(folder, os.path.expanduser(data.get('project', '.'))))
    roles, names = [], set()
    for raw in data.get('role', []):
        r = raw.get('name')
        if not r:
            raise PresetError(f'{path}: every role needs a name')
        if r in names:
            raise PresetError(f'{path}: duplicate role name {r!r}')
        names.add(r)
        agent = raw.get('agent')
        if agent not in AGENTS:
            raise PresetError(f'{path}: role {r!r} has agent {agent!r}; use one of {", ".join(AGENTS)}')
        checkout = raw.get('checkout', 'branch')
        if checkout not in CHECKOUTS:
            raise PresetError(f'{path}: role {r!r} has checkout {checkout!r}; use one of {", ".join(CHECKOUTS)}')
        brief_path = os.path.join(folder, raw.get('brief', f'{r}.md'))
        try:
            with open(brief_path) as f:
                brief = _body(f.read())
        except FileNotFoundError:
            raise PresetError(f'role {r!r}: brief file not found: {brief_path}') from None
        problem = launch.check_model_effort(agent, raw.get('model'), raw.get('effort'))
        if problem:
            raise PresetError(f'{path}: role {r!r}: {problem}')
        roles.append(Role(r, agent, brief_path, brief, raw.get('model'), checkout, raw.get('parent'), raw.get('job', ''),
                          raw.get('effort'), raw.get('group')))
    if not roles:
        raise PresetError(f'{path}: the team has no roles ([[role]] tables)')
    for r in roles:
        if r.parent and r.parent not in names:
            raise PresetError(f'role {r.name!r}: parent {r.parent!r} is not a role in this team')
    chosen = dict(data.get('practices', {}))
    unknown = set(chosen) - {g['id'] for g in _groups()}
    if unknown:
        raise PresetError(f'{path}: unknown practice {", ".join(sorted(unknown))}')
    if practices:
        extra = _practice_rules(chosen)
        for r in roles:
            r.brief = r.brief.rstrip('\n') + extra
    return Team(name, project, chosen, _parents_first(roles))
