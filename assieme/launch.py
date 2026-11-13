"""Each agent's command line: its CLI, model, read-only settings, the status hook and the team tools.

The brief is not on the command line: it is sent after the agent's first turn ends.
"""
import hashlib
import json
import os
import re
import shlex
import sys

from . import container, permissions, quiet, setup, worktree

CLIS = {'claude': 'claude', 'codex': 'codex', 'gemini': 'gemini', 'opencode': 'opencode', 'cursor': 'cursor-agent'}
NAMES = {'claude': 'Claude Code', 'codex': 'Codex', 'gemini': 'Gemini CLI', 'opencode': 'OpenCode', 'cursor': 'Cursor'}
# Thinking effort each CLI accepts (claude --effort; codex model_reasoning_effort). Codex models list their own.
EFFORTS = {'claude': ('low', 'medium', 'high', 'xhigh', 'max'),
           'codex': ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'),
           'gemini': (), 'opencode': (), 'cursor': ()}  # no thinking-effort flag
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
    """Programs that must be on this Mac: a container brings its own agent."""
    return sorted({CLIS[r.agent] for r in roles if not r.container and not which(CLIS[r.agent])})


def hook_command(state, py=None):
    """py: the Python that runs it; this one on the Mac, the image's `python3` in a container."""
    return f'{shlex.quote(py or sys.executable)} -P -m assieme.hook {state}'  # -P: never import from the worktree


def _mcp(py=None):
    return {'command': py or sys.executable, 'args': ['-P', '-m', 'assieme', 'mcp']}  # -P: never import from the worktree


def _test_command(cwd):
    from . import review  # review imports team, which imports this module
    return review.test_command(cwd) if cwd else None


def _claude(role, cwd=None, py=None):
    # Its own team's message and report tools: asking the person about each one would defeat them.
    allow, deny = permissions.claude(role, cwd, _test_command(cwd) if role.level else None)
    perms = {'allow': ['mcp__assieme', *allow]} | ({'deny': deny} if deny else {})
    settings = {'hooks': {e: [{'hooks': [{'type': 'command', 'command': hook_command(s, py)}]}] for e, s in HOOKS.items()},
                'permissions': perms}
    a = ['claude', '--settings', json.dumps(settings), '--mcp-config', json.dumps({'mcpServers': {'assieme': _mcp(py)}})]
    if role.model:
        a += ['--model', role.model]
    if role.effort:
        a += ['--effort', role.effort]
    if role.level:  # "anything else asks you first": not the person's auto mode or a skip-prompts default
        a += ['--permission-mode', 'default']
    if role.checkout == 'read-only' or role.level == 'read-only':
        a += ['--disallowedTools', 'Edit', 'Write', 'NotebookEdit']
    return a


CODEX_EVENTS = {'SessionStart': 'session_start', 'UserPromptSubmit': 'user_prompt_submit', 'Stop': 'stop',
                'PermissionRequest': 'permission_request'}


def codex_hook_hash(event, command):
    """Codex's trust fingerprint for one command hook: sha256 of its normalized identity as sorted, compact JSON."""
    ident = {'event_name': event, 'hooks': [{'type': 'command', 'command': command, 'timeout': 600, 'async': False}]}
    return 'sha256:' + hashlib.sha256(json.dumps(ident, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _codex(role, py=None):
    a = ['codex', '-c', 'check_for_update_on_startup=false']  # its update screen would stop the start-up
    # Its sandbox keeps .git read-only, so each commit is a request to step outside it: the person answers, in Assieme.
    a += ['-c', 'approval_policy="on-request"', '-c', 'approvals_reviewer="user"']
    trust = []
    for e, s in HOOKS.items():
        # A JSON string is a valid TOML basic string.
        a += ['-c', f'hooks.{e}=[{{hooks=[{{type="command",command={json.dumps(hook_command(s, py))}}}]}}]']
        key = f'/<session-flags>/config.toml:{CODEX_EVENTS[e]}:0:0'
        trust.append(f'{json.dumps(key)}={{trusted_hash={json.dumps(codex_hook_hash(CODEX_EVENTS[e], hook_command(s, py)))}}}')
    # Trust these four hooks for this run only, so a first Codex team doesn't stop at "Hooks need review".
    # Any other hook (the person's, a project's, a plugin's) still goes through Codex's own review.
    a += ['-c', 'hooks.state={' + ','.join(trust) + '}']
    # Codex hands MCP servers a short list of variables: these say which agent this is.
    a += ['-c', f'mcp_servers.assieme.command={json.dumps(_mcp(py)["command"])}',
          '-c', f'mcp_servers.assieme.args={json.dumps(_mcp(py)["args"])}',
          '-c', 'mcp_servers.assieme.env_vars=["HC_SESSION","HC_TERMINAL_ID","XDG_STATE_HOME","ASSIEME_BRIDGE","PYTHONPATH"]',
          '-c', 'mcp_servers.assieme.default_tools_approval_mode="approve"']
    if role.model:
        a += ['-m', role.model]
    if role.effort:
        a += ['-c', f'model_reasoning_effort={json.dumps(role.effort)}']
    a += permissions.codex_args(role)  # its program rules are a file in its worktree (team writes it)
    if role.checkout == 'read-only' and '--sandbox' not in a:
        a += ['--sandbox', 'read-only']
    return a


def _merge(cwd, rel, ours):
    """Assieme's entries into a settings file in the agent's own folder: the project's own entries stay, ours are
    replaced, never doubled. Kept out of the agent's commits (excluded, or skip-worktree when the project tracks it).
    A file that isn't plain JSON is left alone: that agent's status then comes from its screen only. Returns the data."""
    path = os.path.join(cwd, rel)
    data = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get('hooks', {}), dict):
            return None
    for key, value in ours.items():
        if key == 'hooks':
            hooks = data.setdefault('hooks', {})
            for event, entries in value.items():
                kept = [e for e in hooks.get(event) or [] if 'assieme.hook' not in json.dumps(e)]
                hooks[event] = kept + entries
        elif isinstance(value, dict) and isinstance(data.get(key, {}), dict):
            data[key] = {**data.get(key, {}), **value}
        else:
            data.setdefault(key, value)
    tracked = worktree._git(cwd, 'ls-files', '--error-unmatch', rel).returncode == 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    if tracked:
        worktree._git(cwd, 'update-index', '--skip-worktree', rel)
    else:
        worktree.exclude(cwd, '/' + rel)
    return data


# Kinds that read their settings from the agent's folder only when told it is trusted: Assieme asks the person first.
ASKS_TRUST = ('gemini', 'cursor')


def _gemini(role, cwd, py=None, trusted=False):
    """Gemini CLI takes system settings only from files root owns: these go in the agent's own folder."""
    hook = lambda state: [{'hooks': [{'type': 'command', 'command': hook_command(state, py)}]}]
    _merge(cwd, '.gemini/settings.json', {
        'hooks': {'SessionStart': hook('idle'), 'BeforeAgent': hook('working'), 'AfterAgent': hook('idle'),
                  'Notification': [{'matcher': 'ToolPermission', 'hooks': hook('blocked')[0]['hooks']}]},
        'mcpServers': {'assieme': {**_mcp(py), 'trust': True}}})  # its own team tools, without asking each time
    a = ['gemini'] + (['-m', role.model] if role.model else [])
    if role.checkout == 'read-only':
        a += ['--approval-mode', 'plan']
    return a, ({'GEMINI_CLI_TRUST_WORKSPACE': 'true'} if trusted else {})


OPENCODE_PLUGIN = os.path.join(os.path.dirname(__file__), 'opencode_status.js')


def _opencode(role, cwd, py=None, trusted=False):
    """OpenCode reads a whole config from its environment; the status plugin calls our hook."""
    config = {'plugin': ['file://' + OPENCODE_PLUGIN],
              'mcp': {'assieme': {'type': 'local', 'command': [_mcp(py)['command'], *_mcp(py)['args']], 'enabled': True}}}
    if role.checkout == 'read-only':
        config['permission'] = {'edit': 'deny'}
    a = ['opencode'] + (['-m', role.model] if role.model else [])
    hook = shlex.split(hook_command('idle', py))[:-1]
    return a, {'OPENCODE_CONFIG_CONTENT': json.dumps(config), 'ASSIEME_HOOK': json.dumps(hook)}


def _cursor(role, cwd, py=None, trusted=False):
    """Cursor reads hooks and MCP servers only from files, so they go in the agent's own folder."""
    hook = lambda state: [{'command': hook_command(state, py)}]
    _merge(cwd, '.cursor/hooks.json', {'version': 1, 'hooks': {
        'sessionStart': hook('idle'), 'beforeSubmitPrompt': hook('working'), 'stop': hook('idle')}})
    mcp = _merge(cwd, '.cursor/mcp.json', {'mcpServers': {'assieme': _mcp(py)}})
    # Approving MCP servers approves them all: only when Assieme's is the only one there.
    alone = mcp is not None and set(mcp.get('mcpServers') or {}) == {'assieme'}
    a = ['cursor-agent'] + (['--approve-mcps'] if alone else []) + (['--trust'] if trusted else []) + (['--model', role.model] if role.model else [])
    if role.checkout == 'read-only':
        a += ['--mode', 'plan']
    return a, {}


BUILDERS = {'claude': lambda role, cwd, py=None, trusted=False: (_claude(role, cwd, py), {}),
            'codex': lambda role, cwd, py=None, trusted=False: (_codex(role, py), {}),
            'gemini': _gemini, 'opencode': _opencode, 'cursor': _cursor}


# An empty prefix matches every remote; pushes go to a git-remote helper (quiet.install writes it) that refuses. Fetch is untouched.
BLOCK_PUSH = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'url.assieme-push-blocked://.pushInsteadOf', 'GIT_CONFIG_VALUE_0': ''}


def argv(role, block_push=True, which=setup.agent_path, cwd=None, terminal=None, bridge=None, trusted=False):
    """The command line. The agent is started by full path, so the PATH the person's shell has isn't relied on.
    cwd: the agent's folder, for its permissions' paths and for the kinds that read their settings from files. A
    container role runs inside `docker run`; terminal names the container, bridge is the port its team tools reach
    the brain on. trusted: the person said yes to this project, for the kinds in ASKS_TRUST.
    A role with a permission level is always kept from pushing."""
    if cwd is None:
        if role.agent in ASKS_TRUST:  # they write settings files: never into whatever folder this process is in
            raise ValueError(f'{NAMES[role.agent]} needs the agent\'s own folder.')
        cwd = os.getcwd()
    push = BLOCK_PUSH if block_push or role.level else {}
    if role.container:
        a, extra = BUILDERS[role.agent](role, cwd, 'python3', trusted)
        return container.run_argv(role, a, {**push, **extra}, cwd, terminal or role.name, bridge, which)
    a, extra = BUILDERS[role.agent](role, cwd, None, trusted)
    a[0] = which(a[0]) or a[0]
    env = {**push, **extra}
    if os.path.isabs(a[0]):
        # An npm-installed agent is a `#!/usr/bin/env node` script whose node sits beside it, and the core may be older
        # than this brain, with a shorter PATH. Quiet send's folder stays first, as the core sets it.
        shim, folder = quiet.shim_dir(), os.path.dirname(a[0])
        rest = [p for p in os.environ.get('PATH', '').split(os.pathsep) if p and p not in (shim, folder)]
        env['PATH'] = os.pathsep.join([shim, folder, *rest])
    return ['/usr/bin/env', *(f'{k}={v}' for k, v in env.items()), *a] if env else a
