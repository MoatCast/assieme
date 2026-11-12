"""What each agent may do: three levels and the person's own rules, written as each agent's own settings.

Claude Code gets allow and deny rules in the `--settings` it starts with; Codex gets its sandbox and program rules in
`<worktree>/.codex/rules/assieme.rules`. No enforcement of our own. Rules match the program run (its first words),
never text that mentions it. Pushing stays blocked at every level.
"""
import json
import os
import re

LEVELS = ('safe', 'read-only', 'trusted')
DEFAULT_LEVEL = {'branch': 'safe', 'read-only': 'read-only'}
RULES_FILE = os.path.join('.codex', 'rules', 'assieme.rules')

PUSH = [['git', 'push'], ['git', 'tag'], ['git', 'merge']]
INSTALL = [['npm', 'install'], ['npm', 'i'], ['npm', 'add'], ['pnpm', 'add'], ['pnpm', 'install'], ['yarn', 'add'],
           ['pip', 'install'], ['pip3', 'install'], ['uv', 'add'], ['uv', 'pip', 'install'], ['poetry', 'add'],
           ['cargo', 'add'], ['cargo', 'install'], ['brew', 'install'], ['gem', 'install'], ['go', 'get']]
DOWNLOAD = [['wget'], ['git', 'clone'], ['git', 'lfs'], ['huggingface-cli', 'download'], ['hf', 'download']]
NETWORK = [['curl'], ['nc'], ['ssh'], ['scp'], ['telnet'], ['ftp']]
COMMIT = [['git', 'add'], ['git', 'commit']]
GIT_READ = [['git', 'status'], ['git', 'diff'], ['git', 'log'], ['git', 'show']]
SECRET_PATHS = ['~/.ssh/**', '~/.aws/**', '~/.config/gcloud/**', '~/.azure/**', '~/.gnupg/**', '~/.netrc',
                '~/Library/Keychains/**']
SECRET_PROGRAMS = [['security']]
# Allowing one of these would allow everything run through it.
LAUNCHERS = {'bash', 'sh', 'zsh', 'fish', 'env', 'sudo', 'xargs', 'eval', 'exec', 'nohup', 'time', 'command'}

# Each item: the words the editors show, and its programs (None: handled by the agent's own switches).
ITEMS = {
    'edit_owned': {'words': 'Edit files it owns'},
    'tests': {'words': 'Run tests, 2 CPU cores kept free'},
    'commit': {'words': 'Commit to its own branch', 'programs': COMMIT},
    'message': {'words': 'Message its team'},
    'read': {'words': 'Read the project and its history', 'programs': GIT_READ},
    'edit': {'words': 'Edit files or commit', 'programs': COMMIT},
    'push': {'words': 'Push, tags, merge', 'note': 'you merge in Review', 'programs': PUSH},
    'install': {'words': 'Install packages or new dependencies', 'programs': INSTALL},
    'download': {'words': 'Download models or datasets', 'programs': DOWNLOAD},
    'secrets': {'words': 'Read secrets (~/.ssh, cloud keys, keychain)', 'programs': SECRET_PROGRAMS},
    'network': {'words': 'Direct network calls', 'programs': NETWORK},
}
LEVEL_INFO = {
    'safe': {'title': 'Safe', 'about': 'Edit its files, test, commit locally',
             'summary': 'Safe: edit its files, run tests, commit locally.',
             'allowed': ['edit_owned', 'tests', 'commit', 'message'],
             'blocked': ['push', 'install', 'download', 'secrets', 'network']},
    'read-only': {'title': 'Read only', 'about': 'For reviewers and researchers',
                  'summary': 'Read only: read and run tests, never edit.',
                  'allowed': ['read', 'tests', 'message'],
                  'blocked': ['push', 'edit', 'install', 'download', 'secrets', 'network']},
    'trusted': {'title': 'Trusted', 'about': 'Also install packages and go online',
                'summary': 'Trusted: also install packages and go online. Never push.',
                'allowed': ['edit_owned', 'tests', 'commit', 'message', 'install', 'download', 'network'],
                'blocked': ['push', 'secrets']},
}
WORD = re.compile(r'^[A-Za-z0-9._/+@=:,%~-]+$')  # no spaces, globs, quotes or shell symbols
OWNED = re.compile(r'^[A-Za-z0-9._/*@+-][A-Za-z0-9._/*@+ -]*$')


def table():
    """For the editors: the levels in order and the items' words."""
    return {'levels': [{'id': k, **v} for k, v in LEVEL_INFO.items()],
            'items': {k: {'words': v['words'], 'note': v.get('note', '')} for k, v in ITEMS.items()}}


def check_level(level):
    if level is not None and level not in LEVELS:
        raise ValueError(f'Unknown permission level "{level}".')


def check_owns(owns):
    """Paths inside the project; a problem in words, or None."""
    if not isinstance(owns, list):
        return 'Files it owns must be a list of paths.'
    for p in owns:
        if not isinstance(p, str) or not OWNED.match(p) or p.startswith('/') or '..' in p.split('/'):
            return f'"{p}" is not a path inside the project.'
    return None


def check_rule(decision, program):
    """A rule's program words, cleaned; ValueError in words when it can't be a rule."""
    if decision not in ('block', 'allow'):
        raise ValueError('A rule either blocks or allows a program.')
    if not isinstance(program, list) or not 1 <= len(program) <= 6:
        raise ValueError('A rule names a program and at most a few of its first words.')
    words = [str(w) for w in program]
    if not all(WORD.match(w) for w in words):
        raise ValueError('A rule names a program and its first words, without spaces, wildcards or shell symbols.')
    if decision == 'allow':
        for floor in PUSH:
            n = min(len(floor), len(words))
            if floor[:n] == words[:n]:
                raise ValueError('Pushing stays blocked at every level. You merge in Review.')
        if os.path.basename(words[0]) in LAUNCHERS:
            raise ValueError(f'Allowing {words[0]} would allow anything run through it. Name the program itself.')
    return words


def check_rules(rules):
    if not isinstance(rules, list):
        return 'Rules must be a list.'
    for r in rules:
        if not isinstance(r, dict):
            return 'A rule needs a decision and a program.'
        try:
            check_rule(r.get('decision'), r.get('program'))
        except ValueError as e:
            return str(e)
    return None


def variants(program):
    """The ways a program is run: a script path also as ./path, bash path and sh path."""
    first, rest = program[0], program[1:]
    if '/' not in first or first.startswith(('/', '~')):
        return [program]
    p = first[2:] if first.startswith('./') else first
    return [[x, *rest] for x in (p, './' + p)] + [[sh, x, *rest] for sh in ('bash', 'sh') for x in (p, './' + p)]


def _bash(program):
    return f'Bash({" ".join(program)}:*)'


def _items(role):
    info = LEVEL_INFO[role.level]
    return info['allowed'], info['blocked']


def claude(role, cwd=None, test=None):
    """(allow, deny) for Claude Code's settings; ([], []) for a role without a level."""
    if not role.level:
        return [], []
    allowed, blocked = _items(role)
    allow, deny = [], []
    base = ('/' + cwd.rstrip('/')) if cwd else '.'  # `//abs` is an absolute path in Claude Code's rules
    for key in allowed:
        if key == 'edit_owned':
            allow += [f'Edit({base}/{p.removeprefix("./")})' for p in role.owns] or [f'Edit({base}/**)']
        elif key == 'tests' and test:
            allow.append(_bash(test))
        allow += [_bash(p) for p in ITEMS[key].get('programs') or []]
    for key in blocked:
        deny += [_bash(p) for p in ITEMS[key].get('programs') or []]
        if key == 'secrets':
            deny += [f'Read({p})' for p in SECRET_PATHS]
    for r in role.rules:
        (allow if r['decision'] == 'allow' else deny).extend(_bash(v) for v in variants(r['program']))
    return allow, deny  # a deny always wins in Claude Code, so a block rule beats the level's allow


def codex_args(role):
    """Codex's sandbox for the level; [] for a role without one."""
    if not role.level:
        return []
    if role.level == 'read-only' or role.checkout == 'read-only':
        return ['--sandbox', 'read-only']
    a = ['--sandbox', 'workspace-write']
    return a + ['-c', 'sandbox_workspace_write.network_access=true'] if role.level == 'trusted' else a


def codex_rules(role):
    """The text of Codex's program rules for this role, or None for a role without a level."""
    if not role.level:
        return None
    allowed, blocked = _items(role)
    out = [f'# Written by Assieme for {role.name} when it starts. Change it in Assieme, not here.']

    def rule(program, decision, why):
        out.append(f'prefix_rule(pattern={json.dumps(program)}, decision="{decision}", justification={json.dumps(why)})')
    for key in blocked:
        for p in ITEMS[key].get('programs') or []:
            rule(p, 'forbidden', ITEMS[key]['words'])
    # Tests and reading run inside the sandbox; commits need .git, which the sandbox keeps read-only.
    for key in allowed:
        if key in ('commit', 'install', 'download', 'network'):
            for p in ITEMS[key]['programs']:
                rule(p, 'allow', ITEMS[key]['words'])
    for r in role.rules:
        for v in variants(r['program']):
            rule(v, 'forbidden' if r['decision'] == 'block' else 'allow', r.get('words') or 'your rule')
    return '\n'.join(out) + '\n'


def write_codex_rules(cwd, role):
    """Write (or, for a role without a level, remove) the rules file in the agent's worktree."""
    path = os.path.join(cwd, RULES_FILE)
    text = codex_rules(role)
    if text is None:
        if os.path.exists(path):
            os.remove(path)
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    return path
