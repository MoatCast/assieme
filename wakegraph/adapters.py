"""Adapters: turn one tool's local logs into Sessions. Each takes (root, since, show_text) and yields Sessions.

Wake markers (herdr `hc`, Claude Code Agent Teams, task notifications) are shared: any adapter whose
transport carries user-turn text runs it through wake_sender().
"""
import collections
import glob
import json
import os
import re

from .model import HUMAN, Session, parse_ts

WAKE_MARKERS = [
    (re.compile(r'<hc from="\S+ ([\w.-]+)">'), 'hc'),                          # herdr: <hc from="<term> <agent>">
    (re.compile(r'<teammate-message teammate_id="([\w.-]+)"'), 'team'),         # Claude Code Agent Teams
    (re.compile(r'^\s*<task-notification>'), 'notify'),                         # a background subagent finished
]
# Claude Code: auto-mode classifier or permission rule said no.
CLAUDE_REFUSED = re.compile(r'^Permission (for this|to use)|^Permission to .* (was )?denied')
# Codex: sandbox or approval refusal in a tool's output.
CODEX_REFUSED = re.compile(r'sandbox_apply: Operation not permitted|rejected by (the )?(user|policy)|approval (was )?denied',
                           re.I)


def wake_sender(text):
    """User-turn text -> (sender, channel). Unmarked text is a person typing."""
    for rx, channel in WAKE_MARKERS:
        m = rx.search(text)
        if m:
            return (m.group(1) if rx.groups else 'subagent'), channel
    return HUMAN, HUMAN


def command_label(tool, command, show_text):
    """A refused call -> a short label. Only the program name unless --show-text: arguments can hold secrets."""
    if not command:
        return tool
    if isinstance(command, list):
        command = ' '.join(map(str, command))
    words = command.split()
    return f"{tool}: {' '.join(words[:3])[:60] if show_text else (words[0] if words else '')}"


def _rows(path):
    with open(path, errors='ignore') as f:
        for line in f:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if isinstance(o, dict):
                yield o


def _recent(paths, since):
    cutoff = since.timestamp()
    return [p for p in paths if os.path.getmtime(p) >= cutoff]


def _text_of(content):
    """User content -> its typed text, or None when it only carries tool results."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text']
        return '\n'.join(texts) if texts else None
    return None


def _strip_common_prefix(names):
    """'-Users-me-src-team-a', '-Users-me-src-team-b' -> 'a', 'b'. Keeps a lone name whole."""
    if len(names) < 2:
        return {n: n.lstrip('-') or n for n in names}
    cut = os.path.commonprefix(names).rfind('-') + 1
    return {n: n[cut:] or n for n in names}


def claude_code(root, since, show_text=False):
    """~/.claude/projects/<project>/<session>.jsonl, plus <session>/subagents/agent-*.jsonl."""
    paths = _recent(glob.glob(os.path.join(root, '*', '*.jsonl'))
                    + glob.glob(os.path.join(root, '*', '*', 'subagents', '*.jsonl')), since)
    names = _strip_common_prefix(sorted({os.path.relpath(p, root).split(os.sep)[0] for p in paths}))
    for path in paths:
        parts = os.path.relpath(path, root).split(os.sep)
        agent = names[parts[0]]
        parent = None
        if len(parts) == 4:  # a subagent: name it after its parent and type
            meta = {}
            try:
                with open(path[:-len('.jsonl')] + '.meta.json') as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                pass
            parent, agent = agent, f"{agent}/{meta.get('agentType') or 'subagent'}"
        s = _claude_session(path, agent, parent, since, show_text)
        if s.calls:
            yield s


def _claude_session(path, agent, parent, since, show_text):
    s = Session(agent, 'claude-code')
    seen, tool_label = set(), {}
    for o in _rows(path):
        ts = parse_ts(o.get('timestamp'))
        if not ts or ts < since:
            continue
        msg = o.get('message') or {}
        content = msg.get('content')
        if o.get('type') == 'user' and not o.get('isMeta'):
            text = _text_of(content)
            if text is not None:
                sender, channel = wake_sender(text)
                if parent and not s.turns:
                    sender, channel = parent, 'subagent'
                s.wake(sender, channel, text, show_text, ts)
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get('type') == 'tool_result' and b.get('is_error'):
                    out = b.get('content')
                    if CLAUDE_REFUSED.search(out if isinstance(out, str) else json.dumps(out)):
                        s.refusals[tool_label.get(b.get('tool_use_id'), '?')] += 1
        elif o.get('type') == 'assistant' and msg.get('usage'):
            if msg.get('id') not in seen:  # one call is logged once per content block
                seen.add(msg.get('id'))
                u = msg['usage']
                s.call(u.get('input_tokens', 0) + u.get('cache_read_input_tokens', 0)
                       + u.get('cache_creation_input_tokens', 0), u.get('output_tokens', 0))
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get('type') == 'tool_use':
                    tool_label[b.get('id')] = command_label(b.get('name'), (b.get('input') or {}).get('command'), show_text)
    return s


def codex(root, since, show_text=False):
    """~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl. Spawned subagents name their parent thread."""
    sessions = {}
    for path in _recent(glob.glob(os.path.join(root, '**', '*.jsonl'), recursive=True), since):
        thread_id, parent_id, s = _codex_session(path, since, show_text)
        sessions[thread_id or path] = (parent_id, s)
    for parent_id, s in sessions.values():
        if parent_id and s.turns and s.turns[0].channel == 'spawn':
            s.turns[0].sender = sessions[parent_id][1].agent if parent_id in sessions else 'codex:parent'
        if s.calls:
            yield s


def _codex_session(path, since, show_text):
    s = Session('codex:?', 'codex')
    thread_id = parent_id = None
    last_total, tool_label = None, {}
    for o in _rows(path):
        p = o.get('payload') or {}
        if o.get('type') == 'session_meta':
            thread_id = p.get('id')
            src = p.get('source') if isinstance(p.get('source'), dict) else {}
            sub = src.get('subagent') if isinstance(src.get('subagent'), dict) else {}
            spawn = sub.get('thread_spawn') if isinstance(sub.get('thread_spawn'), dict) else {}
            parent_id = spawn.get('parent_thread_id')
            kind = 'subagent' if spawn else (sub.get('other') or '')
            s.agent = f"codex:{os.path.basename(p.get('cwd') or '') or '?'}" + (f'/{kind}' if kind else '')
            continue
        ts = parse_ts(o.get('timestamp'))
        if not ts or ts < since:
            continue
        kind = p.get('type')
        if kind == 'message' and p.get('role') == 'user':
            text = _text_of([{'type': 'text', 'text': b.get('text', '')} for b in p.get('content') or []
                             if isinstance(b, dict)]) or ''
            if text.lstrip().startswith(('<environment_context>', '# AGENTS.md', '<user_instructions>')):
                continue  # injected context, not a wake
            sender, channel = wake_sender(text)
            if (parent_id or 'subagent' in s.agent) and not s.turns:
                sender, channel = 'codex:parent', 'spawn'
            s.wake(sender, channel, text, show_text, ts)
        elif kind == 'token_count' and isinstance(p.get('info'), dict):
            total = (p['info'].get('total_token_usage') or {}).get('total_tokens')
            last = p['info'].get('last_token_usage') or {}
            if total != last_total:  # the same count is re-emitted between calls
                last_total = total
                s.call(last.get('input_tokens', 0), last.get('output_tokens', 0))  # input already includes cached
        elif kind in ('function_call', 'custom_tool_call'):
            args = p.get('arguments') or p.get('input') or ''
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except ValueError:
                pass
            cmd = (args.get('cmd') or args.get('command')) if isinstance(args, dict) else args
            tool_label[p.get('call_id')] = command_label(p.get('name') or '?', cmd, show_text)
        elif kind in ('function_call_output', 'custom_tool_call_output'):
            out = p.get('output')
            if CODEX_REFUSED.search(out if isinstance(out, str) else json.dumps(out)):
                s.refusals[tool_label.get(p.get('call_id'), '?')] += 1
    return thread_id, parent_id, s


def generic(path, since, show_text=False):
    """One JSONL file of normalised events, for any other tool:

    {"ts": "...", "agent": "a", "type": "wake", "from": "b", "text": "..."}
    {"ts": "...", "agent": "a", "type": "call", "input_tokens": 1200, "output_tokens": 80}
    {"ts": "...", "agent": "a", "type": "refusal", "command": "git push"}
    """
    sessions = collections.OrderedDict()
    for o in _rows(path):
        ts = parse_ts(o.get('ts'))
        if not ts or ts < since or not o.get('agent'):
            continue
        s = sessions.setdefault(o['agent'], Session(str(o['agent']), 'generic'))
        if o.get('type') == 'wake':
            s.wake(str(o.get('from') or HUMAN), 'generic', str(o.get('text') or ''), show_text, ts)
        elif o.get('type') == 'call':
            s.call(int(o.get('input_tokens') or 0), int(o.get('output_tokens') or 0))
        elif o.get('type') == 'refusal':
            s.refusals[command_label('cmd', str(o.get('command') or ''), show_text)] += 1
    yield from (s for s in sessions.values() if s.calls)
