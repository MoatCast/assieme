"""Each agent's command line: Claude Code or Codex, its model, read-only settings and the status hook.

The brief is not on the command line: it is sent after the agent's first turn ends.
"""
import json
import re
import shlex
import shutil
import sys

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


def missing_clis(roles, which=shutil.which):
    return sorted({CLIS[r.agent] for r in roles if not which(CLIS[r.agent])})


def hook_command(state):
    return f'{shlex.quote(sys.executable)} -P -m assieme.hook {state}'  # -P: never import from the worktree


def _claude(role):
    settings = {'hooks': {e: [{'hooks': [{'type': 'command', 'command': hook_command(s)}]}] for e, s in HOOKS.items()}}
    a = ['claude', '--settings', json.dumps(settings)]
    if role.model:
        a += ['--model', role.model]
    if role.effort:
        a += ['--effort', role.effort]
    if role.checkout == 'read-only':
        a += ['--disallowedTools', 'Edit', 'Write', 'NotebookEdit']
    return a


def _codex(role):
    a = ['codex', '-c', 'check_for_update_on_startup=false']  # its update screen would stop the start-up
    for e, s in HOOKS.items():
        # A JSON string is a valid TOML basic string.
        a += ['-c', f'hooks.{e}=[{{hooks=[{{type="command",command={json.dumps(hook_command(s))}}}]}}]']
    if role.model:
        a += ['-m', role.model]
    if role.effort:
        a += ['-c', f'model_reasoning_effort={json.dumps(role.effort)}']
    if role.checkout == 'read-only':
        a += ['--sandbox', 'read-only']
    return a


def argv(role):
    return _claude(role) if role.agent == 'claude' else _codex(role)
