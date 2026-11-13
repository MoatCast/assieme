"""Antigravity (`agy`) and Assieme's plugin for it.

Antigravity loads MCP servers only from the person's own settings or from a plugin, so its agents get the team tools
through a small plugin Assieme builds here and installs only when the person says yes (Paper "v2 — Antigravity:
connect Assieme"). The plugin also shows in the person's own Antigravity sessions, where the tools read team status
and ask before changing anything. Hooks and permissions don't need it: they go per agent (see `launch`).
"""
import json
import os
import shlex
import subprocess
import sys

from . import quiet, setup

NAME = 'assieme'
DESCRIPTION = ("Assieme's team tools for the agents it starts. In your own sessions they read team status and ask "
               'before changing anything.')


def plugin_dir():
    return os.path.join(quiet.state_dir(), 'agy-plugin')


def build_plugin(path=None):
    """The plugin folder, pointing at this Python (Antigravity copies it when it installs it)."""
    path = path or plugin_dir()
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, 'plugin.json'), 'w') as f:
        json.dump({'name': NAME, 'description': DESCRIPTION, 'version': '1.0.0'}, f, indent=2)
    with open(os.path.join(path, 'mcp_config.json'), 'w') as f:
        json.dump({'mcpServers': {NAME: {'command': sys.executable, 'args': ['-P', '-m', 'assieme', 'mcp']}}}, f, indent=2)
    return path


def connected(agy, run=subprocess.run):
    """Installed, switched on, and starting this Python (after an Assieme update the copy there is stale)."""
    try:
        plugins = json.loads(run([agy, 'plugin', 'list', '--json'], capture_output=True, text=True,
                                 timeout=setup.TIMEOUT_S).stdout or '[]')
        mine = next((p for p in plugins if isinstance(p, dict) and p.get('name') == NAME), None)
        if not mine or not mine.get('enabled'):
            return False
        with open(os.path.join(mine['path'], 'mcp_config.json')) as f:
            return json.load(f)['mcpServers'][NAME]['command'] == sys.executable
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return False


def connect(rt):
    """Install the plugin in a setup terminal the person can watch: the shown command is what runs."""
    agy = setup.agent_path('agy')
    if not agy:
        raise setup.SetupError("Antigravity isn't installed yet.")
    return setup._start(rt, 'antigravity', 'connect', f'{shlex.quote(agy)} plugin install {shlex.quote(build_plugin())}')
