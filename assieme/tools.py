"""Step 32: the skills, plugins and MCP servers each agent kind has installed, so each agent gets only what it needs.

Read-only. An item carries its id, type, name, description and whether it can be turned off for that kind; a server's
command, arguments and environment often hold keys, so they are never read into an item, a notice or a log.
"""

import glob
import json
import os
import re
import tomllib

SELF = ('assieme', 'assieme_assieme')  # Assieme's own team server: always on, the app shows its own chip
FIELDS = ('id', 'type', 'name', 'description', 'locked', 'why')

LOCKED = {
    ('claude', 'skill'): 'Claude Code can turn skills off only all together, with "All skills".',
    ('gemini', 'skill-shared'): 'Gemini CLI reads skill settings from the project folder, so every Gemini agent '
                                'working there gets the same ones.',
    ('cursor', 'server-home'): 'Cursor reads this server from your home folder, so every Cursor agent gets it.',
    ('cursor', 'server-shared'): 'Cursor reads its servers from the project folder, so every Cursor agent working '
                                 'there gets the same ones.',
    ('antigravity', 'all'): "Antigravity can't be given its own set of skills and servers yet: it gets everything "
                            'installed.',
}


def _item(type_, name, description='', why=''):
    return {'id': f'{type_}:{name}', 'type': type_, 'name': name, 'description': description,
            'locked': bool(why), 'why': why}


class _Reader:
    def __init__(self):
        self.notices = []

    def json(self, path):
        if not os.path.isfile(path):
            return {}
        try:
            with open(path) as f:
                text = f.read()
            if path.endswith('.jsonc'):
                text = re.sub(r'^\s*//.*$', '', text, flags=re.M)  # shortcut: whole-line comments only
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.notices.append(f"Couldn't read {path}.")
            return {}

    def toml(self, path):
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, 'rb') as f:
                return tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            self.notices.append(f"Couldn't read {path}.")
            return {}


def _names(servers):
    """Server names that are on, never their settings."""
    if not isinstance(servers, dict):
        return []
    return [n for n, v in servers.items() if n not in SELF and not (isinstance(v, dict) and
            (v.get('enabled') is False or v.get('disabled') is True))]


def _description(skill_md):
    try:
        with open(skill_md) as f:
            head = f.read(4096)
    except OSError:
        return ''
    m = re.search(r'^description:\s*(.+)$', head, re.M)
    return m.group(1).strip().strip('"\'') if m else ''


def _skills(*folders):
    """{name: SKILL.md path} from skill folders, first folder wins."""
    out = {}
    for folder in folders:
        for md in sorted(glob.glob(os.path.join(folder, '*', 'SKILL.md'))):
            out.setdefault(os.path.basename(os.path.dirname(md)), md)
    return out


def _dedupe(items):
    seen, out = set(), []
    for i in items:
        if i['id'] not in seen:
            seen.add(i['id'])
            out.append(i)
    return out


def _claude(r, project, home, own_copy):
    cfg = r.json(os.path.join(home, '.claude.json'))
    local = ((cfg.get('projects') or {}).get(project) or {}) if project else {}
    servers = _names(cfg.get('mcpServers')) + _names(local.get('mcpServers'))
    if project:
        servers += _names(r.json(os.path.join(project, '.mcp.json')).get('mcpServers'))
    items = [_item('server', n) for n in servers]
    enabled = r.json(os.path.join(home, '.claude', 'settings.json')).get('enabledPlugins') or {}
    installed = (r.json(os.path.join(home, '.claude', 'plugins', 'installed_plugins.json')).get('plugins') or {})
    for pid, on in enabled.items():
        if on is not True:
            continue
        where = (installed.get(pid) or [{}])[-1].get('installPath') or ''
        manifest = r.json(os.path.join(where, '.claude-plugin', 'plugin.json')) if where else {}
        items.append(_item('plugin', pid, str(manifest.get('description') or '')))
    skills = _skills(os.path.join(home, '.claude', 'skills'), *([os.path.join(project, '.claude', 'skills')] if project else []))
    if skills:
        items.append(_item('skill', '*', 'Every skill this agent can use. Claude Code turns them on or off together.'))
        items += [_item('skill', n, _description(p), LOCKED[('claude', 'skill')]) for n, p in skills.items()]
    return items


def _codex_skills(r, project, home):
    cfg = r.toml(os.path.join(home, '.codex', 'config.toml'))
    out = {n: p for n, p in _skills(os.path.join(home, '.codex', 'skills')).items()}
    for pid, v in (cfg.get('plugins') or {}).items():
        if not isinstance(v, dict) or v.get('enabled') is False or '@' not in pid:
            continue
        name, market = pid.split('@', 1)
        versions = sorted(glob.glob(os.path.join(home, '.codex', 'plugins', 'cache', market, name, '*')))
        if versions:
            out.update({f'{name}:{n}': p for n, p in _skills(os.path.join(versions[-1], 'skills')).items()})
    return cfg, out


def _codex(r, project, home, own_copy):
    cfg, skills = _codex_skills(r, project, home)
    return [_item('server', n) for n in _names(cfg.get('mcp_servers'))] + \
        [_item('skill', n, _description(p)) for n, p in skills.items()]


def _gemini(r, project, home, own_copy):
    servers = _names(r.json(os.path.join(home, '.gemini', 'settings.json')).get('mcpServers'))
    if project:
        servers += _names(r.json(os.path.join(project, '.gemini', 'settings.json')).get('mcpServers'))
    items = [_item('server', n) for n in servers]
    for manifest in sorted(glob.glob(os.path.join(home, '.gemini', 'extensions', '*', 'gemini-extension.json'))):
        m = r.json(manifest)
        items.append(_item('extension', str(m.get('name') or os.path.basename(os.path.dirname(manifest))),
                           str(m.get('description') or '')))
    why = '' if own_copy else LOCKED[('gemini', 'skill-shared')]
    skills = _skills(os.path.join(home, '.gemini', 'skills'), os.path.join(home, '.agents', 'skills'))
    return items + [_item('skill', n, _description(p), why) for n, p in skills.items()]


def _opencode(r, project, home, own_copy):
    servers = []
    for path in [os.path.join(home, '.config', 'opencode', f) for f in ('opencode.json', 'opencode.jsonc')] + \
            ([os.path.join(project, 'opencode.json')] if project else []):
        servers += _names(r.json(path).get('mcp'))
    return [_item('server', n) for n in servers]


def _cursor(r, project, home, own_copy):
    items = [_item('server', n, why=LOCKED[('cursor', 'server-home')])
             for n in _names(r.json(os.path.join(home, '.cursor', 'mcp.json')).get('mcpServers'))]
    if project:
        why = '' if own_copy else LOCKED[('cursor', 'server-shared')]
        items += [_item('server', n, why=why)
                  for n in _names(r.json(os.path.join(project, '.cursor', 'mcp.json')).get('mcpServers'))]
    return items


READERS = {'claude': _claude, 'codex': _codex, 'gemini': _gemini, 'opencode': _opencode, 'cursor': _cursor}


def find(kind, project=None, home=None, own_copy=True):
    """{'items': [...], 'notices': [...]} for one agent kind. own_copy: the agent works in its own copy of the
    project, so settings read from the project folder are its alone."""
    home = home or os.path.expanduser('~')
    if kind == 'antigravity':
        return {'items': [], 'notices': [LOCKED[('antigravity', 'all')]]}
    reader = READERS.get(kind)
    if not reader:
        return {'items': [], 'notices': []}
    r = _Reader()
    items = _dedupe(reader(r, project and os.path.abspath(project), home, own_copy))
    return {'items': [{k: i[k] for k in FIELDS} for i in items], 'notices': r.notices}


def skill_paths(kind, project=None, home=None):
    """{skill id: SKILL.md path}, for the kinds that turn single skills off by path (Codex)."""
    if kind != 'codex':
        return {}
    _, skills = _codex_skills(_Reader(), project, home or os.path.expanduser('~'))
    return {f'skill:{n}': p for n, p in skills.items()}


DEPLOY = ('deploy', 'publish', 'release', 'vercel', 'netlify', 'runpod', 'aws')
BROWSER = ('browser', 'chrome', 'playwright', 'puppeteer', 'computer-use', 'computer use')
_AVOID = {'reviewer': DEPLOY + BROWSER, 'planner': DEPLOY + BROWSER, 'builder': DEPLOY, 'tester': DEPLOY}


def suggest(role_kind, items):
    """Ids to turn off for a role (planner, builder, reviewer, tester; None for a custom role gets everything).
    Only words in an item's name or description move it off: anything the rules can't place stays on."""
    words = _AVOID.get(role_kind, ())
    if not words:
        return []
    pattern = re.compile(r'(?<![a-z0-9])(' + '|'.join(re.escape(w) for w in words) + r')(?![a-z0-9])')  # whole words
    return [i['id'] for i in items if not i['locked'] and pattern.search(f"{i['name']} {i['description']}".lower())]


def off(role, items):
    """What is off when this role starts: its saved choice, plus anything new when it doesn't take new ones."""
    ids = {i['id'] for i in items if not i['locked']}
    out = set(role.tools_off) & ids
    if not role.tools_new:
        out |= ids - set(role.tools_known)
    return out
