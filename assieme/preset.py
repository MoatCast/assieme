"""Team presets: a folder with team.toml and one Markdown brief per role (briefs use hc's template format)."""
import os
import re
import tomllib

from . import launch, permissions
from dataclasses import dataclass, field

AGENTS = tuple(launch.CLIS)
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
    kind: str | None = None  # planner, builder, reviewer, tester; None for a custom role
    owns: list = field(default_factory=list)  # paths in the project only this agent edits
    level: str | None = None  # safe, read-only or trusted; None: started as before levels existed
    rules: list = field(default_factory=list)  # the person's own: {decision, program, words}
    container: bool = False  # runs in a container that sees only its own copy
    share_signin: bool = False  # that container may use this kind's sign-in file from the Mac
    worktree: str | None = None  # brought over (step 30): works in this existing folder instead of its own copy
    branch: str | None = None  # brought over: the branch it works on instead of assieme/<team>/<role>
    resume: str | None = None  # brought over: the agent's conversation to pick up on its first start


SCRIPTS = ('setup', 'run', 'archive')


@dataclass
class Team:
    name: str
    project: str
    practices: dict = field(default_factory=dict)
    roles: list = field(default_factory=list)
    scripts: dict = field(default_factory=dict)  # setup, run and archive lines brought over from another tool


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
    groups = [{k: v for k, v in g.items() if k != 'rule'} | {'rules': g.get('rule', [])} for g in data.get('group', [])]
    return {'groups': groups, 'basics': data.get('basic', []), 'bigger': data.get('bigger', [])}


def _all():  # `load`'s `practices` argument hides the function inside it
    return practices()


def keys():
    """Every key a choice may hold: group, rule and bigger-team ids."""
    p = _all()
    return {g['id'] for g in p['groups']} | {r['id'] for g in p['groups'] for r in g['rules']} | {b['id'] for b in p['bigger']}


def rules_on(chosen):
    """The rules agents get: a rule is on when its group and itself are both on."""
    return [r for g in _all()['groups'] if chosen.get(g['id']) is not False for r in g['rules'] if chosen.get(r['id']) is not False]


def rule_on(chosen, rule_id):
    return any(r['id'] == rule_id for r in rules_on(chosen))


def _practice_rules(chosen):
    lines = [f"- {r['text']}" for r in rules_on(chosen)]
    return '\n\n## Team practices\n\n' + '\n'.join(lines) + '\n' if lines else ''


STEWARD = 'steward'
RESEARCHER = re.compile(r'^(research|investigat)', re.I)  # the role's name, never its job's wording; the app's agentCount matches it
STEWARD_LINE = ('When you would stop, or need a decision only the person could make, send it to steward with '
                'message_agent first. Call report only when steward tells you it needs the person.')


def _brief_file(name):
    with open(os.path.join(os.path.dirname(__file__), 'presets', 'briefs', name)) as f:
        return f.read()


def _bigger(roles, chosen):
    """Roles the presets for bigger teams add, and lines for the briefs of the rest. Parents come first."""
    names = {r.name for r in roles}
    added = []
    if chosen.get('research-pairs') is True:
        for r in list(roles):
            critic = f'{r.name}-critic'
            if RESEARCHER.match(r.name) and critic not in names:
                r.brief = r.brief.rstrip('\n') + (f'\n\nBefore you report a finding, send it with its sources to {critic} '
                                                  'and wait for its check. Report only what it confirmed, and say what it could not.\n')
                added.append(Role(critic, r.agent, '', _brief_file('critic.md'), r.model, 'read-only', r.name,
                                  f'Checks every claim of {r.name} at the source', r.effort, r.group))
    if chosen.get('steward') is True and STEWARD not in names:
        top = next((r for r in roles if not r.parent), roles[0])
        for r in roles + added:
            r.brief = r.brief.rstrip('\n') + '\n\n' + STEWARD_LINE + '\n'
        added.append(Role(STEWARD, top.agent, '', _brief_file('steward.md'), top.model, 'read-only', top.name,
                          'Settles routine stops, so only real decisions reach you', top.effort))
    return roles + added


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
        owns, level, rules = raw.get('owns', []), raw.get('level'), raw.get('rules', [])
        problem = permissions.check_owns(owns) or permissions.check_rules(rules)
        if level is not None and level not in permissions.LEVELS:
            problem = f'unknown permission level {level!r}'
        if problem:
            raise PresetError(f'{path}: role {r!r}: {problem}')
        roles.append(Role(r, agent, brief_path, brief, raw.get('model'), checkout, raw.get('parent'), raw.get('job', ''),
                          raw.get('effort'), raw.get('group'), raw.get('kind'), list(owns), level,
                          [{'decision': x['decision'], 'program': list(x['program']), 'words': str(x.get('words', ''))}
                           for x in rules], container=raw.get('container') is True,
                          share_signin=raw.get('share_signin') is True, worktree=raw.get('worktree'),
                          branch=raw.get('branch'), resume=raw.get('resume')))
    if not roles:
        raise PresetError(f'{path}: the team has no roles ([[role]] tables)')
    for r in roles:
        if r.parent and r.parent not in names:
            raise PresetError(f'role {r.name!r}: parent {r.parent!r} is not a role in this team')
    chosen = dict(data.get('practices', {}))
    unknown = set(chosen) - keys()
    if unknown:
        raise PresetError(f'{path}: unknown practice {", ".join(sorted(unknown))}')
    if practices:
        roles = _bigger(roles, chosen)
        extra = _practice_rules(chosen)
        for r in roles:
            r.brief = r.brief.rstrip('\n') + extra
    scripts = {k: v for k, v in data.get('scripts', {}).items() if k in SCRIPTS and isinstance(v, str) and v.strip()}
    return Team(name, project, chosen, _parents_first(roles), scripts)
