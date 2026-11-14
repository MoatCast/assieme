"""Step 18a: Assieme packaged the way each app expects, written at install time because it must name this Mac's own
Assieme (the Claude app doesn't see a terminal's PATH):

- one local marketplace, for Claude Code (.claude-plugin/marketplace.json) and Codex (.agents/plugins/marketplace.json)
- one plugin folder: the MCP server, a "Use Assieme" skill, Claude Code's ask hook (in its own file, so Codex, which
  reads hooks/hooks.json, doesn't run it) and Codex's per-tool "prompt" approval
- a Claude app extension (.mcpb: a zip holding manifest.json), listed there as a connector

Every server entry sets CLIENT_ASKS: the client's own approval is the person's yes (see mcpserver._with_yes)."""
import json
import os
import shutil
import zipfile

from . import actions, askhook

NAME = 'assieme'
DESCRIPTION = 'See and run your Assieme teams: design new ones and act on what needs you. Every change asks you first.'
GUIDE = os.path.join(os.path.dirname(__file__), 'guide', 'team-design.md')


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f, indent=1)


def _server(python):
    return {'command': python, 'args': ['-P', '-m', 'assieme', 'mcp'], 'env': {actions.CLIENT_ASKS: '1'}}


def build(dest, python):
    """Write the marketplace, plugin and extension into `dest` (replacing what was there) for `python`'s Assieme.
    Returns {market: the marketplace folder, extension: the .mcpb path}."""
    shutil.rmtree(dest, ignore_errors=True)
    p = os.path.join(dest, 'plugin')
    _write(os.path.join(dest, '.claude-plugin', 'marketplace.json'),
           {'name': NAME, 'owner': {'name': 'Assieme'}, 'plugins': [{'name': NAME, 'source': './plugin', 'description': DESCRIPTION}]})
    _write(os.path.join(dest, '.agents', 'plugins', 'marketplace.json'),
           {'name': NAME, 'interface': {'displayName': 'Assieme'},
            'plugins': [{'name': NAME, 'source': {'source': 'local', 'path': './plugin'},
                         'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'}, 'category': 'Developer Tools'}]})
    _write(os.path.join(p, '.claude-plugin', 'plugin.json'),
           {'name': NAME, 'version': '1.0.0', 'description': DESCRIPTION, 'hooks': './claude-hooks.json'})
    _write(os.path.join(p, '.mcp.json'), {'mcpServers': {NAME: _server(python)}})
    _write(os.path.join(p, 'claude-hooks.json'),
           {'hooks': {'PreToolUse': [{'matcher': 'mcp__.*assieme.*',
                                      'hooks': [{'type': 'command', 'command': f'{python} -P -m assieme.askhook'}]}]}})
    _write(os.path.join(p, '.codex-plugin', 'plugin.json'),
           {'name': NAME, 'version': '1.0.0', 'description': DESCRIPTION, 'mcpServers': './codex-mcp.json',
            'author': {'name': 'Assieme'}})
    prompt = {t: {'approval_mode': 'prompt'} for t in (*askhook.CHANGE_TOOLS, 'app_action')}
    _write(os.path.join(p, 'codex-mcp.json'),
           {'mcpServers': {NAME: _server(python) | {'default_tools_approval_mode': 'approve', 'tools': prompt}}})
    with open(GUIDE) as f:
        guide = f.read().strip()
    _write(os.path.join(p, 'skills', 'use-assieme', 'SKILL.md'),
           '---\nname: use-assieme\ndescription: How to see, design and run Assieme teams of Claude Code and Codex '
           'agents on this Mac. Use when the person asks about their agents, their teams, or designing a new team.\n'
           f'---\n\n{guide}\n')
    extension = os.path.join(dest, 'assieme.mcpb')
    manifest = {'manifest_version': '0.2', 'name': NAME, 'display_name': 'Assieme', 'version': '1.0.0',
                'description': DESCRIPTION, 'author': {'name': 'Assieme'},
                'server': {'type': 'binary', 'entry_point': python, 'mcp_config': _server(python)}, 'tools_generated': True}
    with zipfile.ZipFile(extension, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest, indent=1))
    return {'market': dest, 'extension': extension}
