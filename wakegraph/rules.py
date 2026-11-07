"""F4: say why a command was refused, which rule did it, and suggest a narrower rule. Suggestions only."""
import fnmatch
import json
import os
import re

# Claude Code refusal wordings, each mapped to where the refusal came from.
DENY_RULE = re.compile(r'^Permission to use (\w+) with command .* has been denied', re.S)
CLASSIFIER = re.compile(r'denied by the Claude Code auto mode classifier(?:\. Reason: \[([^\]]+)\])?')
SAFETY = re.compile(r'denied by a built-in Claude Code safety check')
SLEEP = re.compile(r'^<tool_use_error>Blocked: sleep')
PATH_RULE = re.compile(r"blocked by a deny rule|denied by your permission settings")
REFUSED = re.compile('|'.join(r.pattern.lstrip('^') for r in (DENY_RULE, CLASSIFIER, SAFETY, SLEEP, PATH_RULE))
                     + r'|^Permission (for this|to use)')


def load(cwd, user_settings):
    """Deny rules that apply in cwd -> [(rule, file)], project files first."""
    files = [os.path.join(cwd, '.claude', n) for n in ('settings.local.json', 'settings.json')] if cwd else []
    out = []
    for path in files + [user_settings]:
        try:
            with open(path) as f:
                deny = (json.load(f).get('permissions') or {}).get('deny') or []
        except (OSError, ValueError, AttributeError):
            continue
        out += [(r, _short(path)) for r in deny if isinstance(r, str)]
    return out


def _short(path):
    home = os.path.expanduser('~')
    return '~' + path[len(home):] if path.startswith(home + os.sep) else path


def _parts(command):
    """'a && b | c' -> ['a', 'b', 'c']: Claude Code checks each part of a compound command."""
    return [p.strip() for p in re.split(r'&&|\|\||[;|\n]', command or '') if p.strip()]


def _bash_match(rule_body, part):
    if rule_body.endswith(':*'):
        head = rule_body[:-2]
        return part == head or part.startswith(head + ' ')
    return fnmatch.fnmatchcase(part, rule_body)


def explain(text, tool, command, rules, path=None):
    """Refusal text -> (why, note). why names the source; note is the advice, or ''."""
    m = CLASSIFIER.search(text)
    if m:
        words = (command or '').split()[:2]
        cmd = ' '.join(w for w in words if re.fullmatch(r'[\w.-]+', w))
        return (f"auto-mode classifier ({m.group(1) or 'no reason given'})",
                f"if this agent is meant to do it, allow it explicitly (`Bash({cmd}:*)`) or change its brief" if cmd
                else 'change the brief so the agent stops trying')
    if SAFETY.search(text):
        return 'built-in safety check', 'delete named paths instead of a broad removal'
    if SLEEP.search(text):
        return 'built-in sleep guard', 'wait with `hc wait` or a Monitor loop instead of sleep'
    if PATH_RULE.search(text):
        p = path or (re.search(r"'([^']+)'", text) or [None, None])[1]
        for rule, src in rules:
            body = re.fullmatch(r'(?:Read|Edit|Write)\((.+)\)', rule)
            if body and p and _path_match(body.group(1), p):
                return f'deny rule `{rule}` ({src})', ''
        return 'deny rule on a file path', ''
    if DENY_RULE.search(text) or text.startswith('Permission to use'):
        for rule, src in rules:
            body = re.fullmatch(r'Bash\((.+)\)', rule)
            if not body or tool != 'Bash':
                continue
            for part in _parts(command):
                if _bash_match(body.group(1), part):
                    return f'deny rule `{rule}` ({src})', _text_only_note(body.group(1), part)
        return 'deny rule (not found in readable settings)', ''
    return 'permission refused', ''


def _text_only_note(body, part):
    """A rule like Bash(*launcher*) also matches `grep launcher notes.md`: it names text, not the program run."""
    if not body.startswith('*'):
        return ''
    words = body.replace(':*', ' ').replace('*', ' ').split()
    program = os.path.basename(part.split()[0]) if part.split() else ''
    if not words or program == os.path.basename(words[0]):
        return ''
    return (f'the rule matched a word in the command, not the program run ({program}); '
            f"deny running it instead: `Bash({' '.join(words)}:*)`")


def _path_match(pattern, path):
    pattern = os.path.expanduser(pattern[1:] if pattern.startswith('//') else pattern)
    pattern = pattern.replace('**', '*')
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path, '*/' + pattern.lstrip('./'))


def load_file(path):
    """Deny rules from one settings file given with --rules."""
    return load(None, path)


def running_settings():
    """{cwd: [(rule, file)]} for running Claude Code agents started with `--settings <file>`. Never fatal.

    Teams often pass each role's rules this way, and the logs don't record the file, so this is the only zero-setup
    way to find them.
    """
    import subprocess
    out = {}
    try:
        ps = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return out
    for line in ps.splitlines():
        pid, _, cmd = line.strip().partition(' ')
        m = re.search(r'\bclaude\b.*--settings[= ](\S+)', cmd)
        if not m or m.group(1).startswith(('{', "'{")):
            continue  # inline JSON settings carry no file to read
        cwd = _proc_cwd(pid, subprocess)
        path = m.group(1) if os.path.isabs(m.group(1)) or not cwd else os.path.join(cwd, m.group(1))
        if cwd and os.path.isfile(path):
            out.setdefault(cwd, []).extend(load_file(path))
    return out


def _proc_cwd(pid, subprocess):
    try:
        return os.readlink(f'/proc/{pid}/cwd')  # Linux
    except OSError:
        pass
    try:  # macOS
        res = subprocess.run(['lsof', '-a', '-p', pid, '-d', 'cwd', '-Fn'], capture_output=True, text=True, timeout=5)
        return next((ln[1:] for ln in res.stdout.splitlines() if ln.startswith('n')), None)
    except (OSError, subprocess.SubprocessError):
        return None
