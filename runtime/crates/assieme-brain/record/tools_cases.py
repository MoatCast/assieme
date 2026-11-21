"""Records what Python's tools.find answers for a made-up home folder and project: `tests/data/tools.json`.

Run from the repository root with the repo's own Python:  .venv/bin/python runtime/crates/assieme-brain/record/tools_cases.py
Paths in the files and answers are written as @ROOT@, the scratch folder the test rebuilds the tree in.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
sys.path.insert(0, ROOT)
from assieme import tools  # noqa: E402

SKILL = '---\nname: x\ndescription: "{}"\n---\nbody\n'
FILES = {
    'home/.claude.json': {'mcpServers': {'alpha': {}, 'assieme': {}, 'off': {'disabled': True}},
                          'projects': {'@ROOT@/proj': {'mcpServers': {'local': {}}}}},
    'proj/.mcp.json': '{ not json',
    'home/.claude/settings.json': {'enabledPlugins': {'p@m': True, 'q@m': False}},
    'home/.claude/plugins/installed_plugins.json': {'plugins': {'p@m': [{'installPath': '@ROOT@/home/plug'}]}},
    'home/plug/.claude-plugin/plugin.json': {'description': 'A plugin'},
    'home/.claude/skills/s1/SKILL.md': SKILL.format('Deploy things'),
    'proj/.claude/skills/s1/SKILL.md': SKILL.format('shadowed'),
    'proj/.claude/skills/s2/SKILL.md': SKILL.format('Plain skill'),
    'home/.codex/config.toml': '[mcp_servers.cx]\ncommand = "x"\n[mcp_servers.cy]\nenabled = false\n'
                               '[plugins."pl@mk"]\nenabled = true\n',
    'home/.codex/skills/c1/SKILL.md': SKILL.format('Codex skill'),
    'home/.codex/plugins/cache/mk/pl/1.0/skills/z/SKILL.md': SKILL.format('From a plugin'),
    'home/.gemini/settings.json': {'mcpServers': {'g1': {}}},
    'proj/.gemini/settings.json': {'mcpServers': {'g2': {'enabled': False}, 'g3': {}}},
    'home/.gemini/extensions/e1/gemini-extension.json': {'name': 'ext-one', 'description': 'Ext', 'mcpServers': {'es': {}}},
    'home/.gemini/skills/gs/SKILL.md': SKILL.format('Gemini skill'),
    'home/.config/opencode/opencode.jsonc': '// a comment\n{"mcp": {"o1": {}, "-bad": {}}}',
    'proj/opencode.json': {'mcp': {'o2': {}}},
    'home/.cursor/mcp.json': {'mcpServers': {'cu1': {}}},
    'proj/.cursor/mcp.json': {'mcpServers': {'cu2': {}}},
}


def swap(x, root, back=False):
    s = json.dumps(x)
    return json.loads(s.replace('@ROOT@', root) if not back else s.replace(root, '@ROOT@'))


with tempfile.TemporaryDirectory() as root:
    root = os.path.realpath(root)
    for rel, body in FILES.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(body if isinstance(body, str) else json.dumps(swap(body, root)))
    cases = []
    for kind in ('claude', 'codex', 'gemini', 'opencode', 'cursor', 'antigravity', 'other'):
        for own in (True, False):
            got = tools.find(kind, os.path.join(root, 'proj'), home=os.path.join(root, 'home'), own_copy=own)
            cases.append({'kind': kind, 'own_copy': own, 'expect': swap(got, root, back=True)})
    out = os.path.join(ROOT, 'runtime', 'crates', 'assieme-brain', 'tests', 'data', 'tools.json')
    with open(out, 'w') as f:
        json.dump({'files': FILES, 'cases': cases}, f, indent=1)
