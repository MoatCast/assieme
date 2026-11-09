"""Asking the person's own agent a one-off question: Claude Code first, else Codex, read-only, in a folder.

Used by Help me choose and by suggested answers. The prompt goes on stdin (claude's --disallowedTools takes every
argument after it); the environment is cleaned of agent-session markers.
"""
import json
import os
import subprocess

from . import launch, preset, runtime, setup


class AskError(Exception):
    def __init__(self, message, missing=False):
        super().__init__(message)
        self.missing = missing  # no agent CLI installed at all


def installed(which=setup.agent_path):
    return [a for a in preset.AGENTS if which(launch.CLIS[a])]


NO_TOOLS = ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch', 'Task']  # with Edit/Write/NotebookEdit/Bash: nothing left


def ask_agent(prompt, cwd, timeout_s, run=subprocess.run, which=setup.agent_path, timeout_label=None, tools=True):
    """The agent's answer text (Claude's --output-format json wrapper removed); AskError in words otherwise.

    tools=False: a pure text answer. Use it whenever the prompt quotes text an agent printed, which may carry
    instructions from what that agent read (Codex's read-only sandbox already has no network)."""
    have = installed(which)
    if not have:
        raise AskError('Install Claude Code or Codex, then reopen New team.', missing=True)
    if have[0] == 'claude':
        argv = ['claude', '-p', '--output-format', 'json', '--disallowedTools', 'Edit', 'Write', 'NotebookEdit', 'Bash']
        if not tools:
            argv = argv[:4] + ['--strict-mcp-config'] + argv[4:] + NO_TOOLS
    else:
        argv = ['codex', 'exec', '--sandbox', 'read-only', '--skip-git-repo-check', '-']
    argv[0] = which(have[0])  # by full path: the PATH this process has may not include where it was installed
    try:
        r = run(argv, input=prompt, cwd=cwd, env=runtime.clean_env(os.environ), capture_output=True, text=True,
                timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise AskError(f'no answer within {timeout_label or f"{timeout_s} seconds"}.') from None
    if r.returncode != 0:
        lines = [x for x in (r.stderr or r.stdout or '').splitlines() if x.strip()] or [f'exit {r.returncode}']
        raise AskError(lines[-1].strip())
    text = r.stdout or ''
    try:
        outer = json.loads(text)
        if isinstance(outer, dict) and isinstance(outer.get('result'), str):
            return outer['result']
    except ValueError:
        pass
    return text


def first_json(text):
    """The JSON object from the first `{` to the last `}` of a reply (prose or a code fence around it is fine)."""
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end < start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
