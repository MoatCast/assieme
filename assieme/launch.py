"""Each agent's command line: Claude Code or Codex, its model, read-only settings and the status hook.

The brief is not on the command line: it is sent after the agent's first turn ends.
"""
import hashlib
import json
import re
import shlex
import sys

from . import setup

CLIS = {'claude': 'claude', 'codex': 'codex'}
NAMES = {'claude': 'Claude Code', 'codex': 'Codex'}
# Thinking effort each CLI accepts (claude --effort; codex model_reasoning_effort). Codex models list their own.
EFFORTS = {'claude': ('low', 'medium', 'high', 'xhigh', 'max'),
           'codex': ('low', 'medium', 'high', 'xhigh', 'max', 'ultra')}
MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')  # the Codex value goes into a config setting: keep it plain


def check_model_effort(agent, model, effort):
    """The problem with an agent's model or effort, in words, or None."""
    if model and not MODEL.match(model):
        return 'Model names use letters, digits, dots and dashes.'
    if effort and effort not in EFFORTS.get(agent, ()):
        return f'{NAMES.get(agent, agent)} has no thinking effort "{effort}".'
    return None

# Agent event -> status. Not Claude Code's Notification: it also fires after a minute of plain idling.
HOOKS = {'SessionStart': 'idle', 'UserPromptSubmit': 'working', 'Stop': 'idle', 'PermissionRequest': 'blocked'}


def missing_clis(roles, which=setup.agent_path):
    return sorted({CLIS[r.agent] for r in roles if not which(CLIS[r.agent])})


def hook_command(state):
    return f'{shlex.quote(sys.executable)} -P -m assieme.hook {state}'  # -P: never import from the worktree


def _mcp():
    return {'command': sys.executable, 'args': ['-P', '-m', 'assieme', 'mcp']}  # -P: never import from the worktree


def _claude(role):
    # Its own team's message and report tools: asking the person about each one would defeat them.
    settings = {'hooks': {e: [{'hooks': [{'type': 'command', 'command': hook_command(s)}]}] for e, s in HOOKS.items()},
                'permissions': {'allow': ['mcp__assieme']}}
    a = ['claude', '--settings', json.dumps(settings), '--mcp-config', json.dumps({'mcpServers': {'assieme': _mcp()}})]
    if role.model:
        a += ['--model', role.model]
    if role.effort:
        a += ['--effort', role.effort]
    if role.checkout == 'read-only':
        a += ['--disallowedTools', 'Edit', 'Write', 'NotebookEdit']
    return a


CODEX_EVENTS = {'SessionStart': 'session_start', 'UserPromptSubmit': 'user_prompt_submit', 'Stop': 'stop',
                'PermissionRequest': 'permission_request'}


def codex_hook_hash(event, command):
    """Codex's trust fingerprint for one command hook: sha256 of its normalized identity as sorted, compact JSON."""
    ident = {'event_name': event, 'hooks': [{'type': 'command', 'command': command, 'timeout': 600, 'async': False}]}
    return 'sha256:' + hashlib.sha256(json.dumps(ident, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _codex(role):
    a = ['codex', '-c', 'check_for_update_on_startup=false']  # its update screen would stop the start-up
    trust = []
    for e, s in HOOKS.items():
        # A JSON string is a valid TOML basic string.
        a += ['-c', f'hooks.{e}=[{{hooks=[{{type="command",command={json.dumps(hook_command(s))}}}]}}]']
        key = f'/<session-flags>/config.toml:{CODEX_EVENTS[e]}:0:0'
        trust.append(f'{json.dumps(key)}={{trusted_hash={json.dumps(codex_hook_hash(CODEX_EVENTS[e], hook_command(s)))}}}')
    # Trust these four hooks for this run only, so a first Codex team doesn't stop at "Hooks need review".
    # Any other hook (the person's, a project's, a plugin's) still goes through Codex's own review.
    a += ['-c', 'hooks.state={' + ','.join(trust) + '}']
    # Codex hands MCP servers a short list of variables: these say which agent this is.
    a += ['-c', f'mcp_servers.assieme.command={json.dumps(_mcp()["command"])}',
          '-c', f'mcp_servers.assieme.args={json.dumps(_mcp()["args"])}',
          '-c', 'mcp_servers.assieme.env_vars=["HC_SESSION","HC_TERMINAL_ID","XDG_STATE_HOME"]',
          '-c', 'mcp_servers.assieme.default_tools_approval_mode="approve"']
    if role.model:
        a += ['-m', role.model]
    if role.effort:
        a += ['-c', f'model_reasoning_effort={json.dumps(role.effort)}']
    if role.checkout == 'read-only':
        a += ['--sandbox', 'read-only']
    return a


# An empty prefix matches every remote; pushes go to a git-remote helper (quiet.install writes it) that refuses. Fetch is untouched.
BLOCK_PUSH = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'url.assieme-push-blocked://.pushInsteadOf', 'GIT_CONFIG_VALUE_0': ''}


def argv(role, block_push=True, which=setup.agent_path):
    """The command line. The agent is started by full path, so the PATH the person's shell has isn't relied on."""
    a = _claude(role) if role.agent == 'claude' else _codex(role)
    a[0] = which(a[0]) or a[0]
    return ['/usr/bin/env', *(f'{k}={v}' for k, v in BLOCK_PUSH.items()), *a] if block_push else a
