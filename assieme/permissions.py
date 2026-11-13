"""What each agent may do: three levels and the person's own rules, written as each agent's own settings.

Claude Code gets allow and deny rules in the `--settings` it starts with; Codex gets its sandbox and program rules in
`<worktree>/.codex/rules/assieme.rules`; OpenCode gets `permission` rules in the config it starts with; Gemini CLI
gets a policy file it is pointed at; Cursor gets `permissions` in `<worktree>/.cursor/cli.json`. No enforcement of
our own. Rules match the program run (its first words),
never text that mentions it. Pushing stays blocked at every level.
"""
import json
import os
import re

LEVELS = ('safe', 'read-only', 'trusted')
DEFAULT_LEVEL = {'branch': 'safe', 'read-only': 'read-only'}
RULES_FILE = os.path.join('.codex', 'rules', 'assieme.rules')

GIT_PUSH = [['git', 'push'], ['git', 'tag'], ['git', 'merge']]
# Where git usually lives on a Mac, so `/usr/bin/git push` is denied as well as `git push`.
GIT_PATHS = ['/usr/bin/git', '/opt/homebrew/bin/git', '/usr/local/bin/git',
             '/Library/Developer/CommandLineTools/usr/bin/git', '/Applications/Xcode.app/Contents/Developer/usr/bin/git']
PUSH = GIT_PUSH + [[g, *p[1:]] for g in GIT_PATHS for p in GIT_PUSH]
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
# These run whatever they are given: allowed only with what they run (`python3 scripts/check.py`, `make lint`).
RUNNERS = {'python', 'python3', 'node', 'npx', 'uvx', 'pnpm', 'bunx', 'bun', 'deno', 'perl', 'ruby', 'osascript', 'make',
           'npm', 'yarn', 'uv'}
RUNS_ANYTHING = {'dlx', 'exec', 'x', 'run'}  # `pnpm dlx`, `npm exec`, `uv run`: still nothing named

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
    'secrets': {'words': 'Read secrets (~/.ssh, cloud keys, keychain)', 'programs': SECRET_PROGRAMS,
                'note': 'its file tools and commands that name them',
                'notes': {'codex': 'only the keychain command, for now', 'gemini': 'its file tools only, for now',
                          'opencode': 'its file tools only, for now', 'cursor': 'its file tools only, for now'}},
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
            'items': {k: {'words': v['words'], 'note': v.get('note', ''), 'notes': v.get('notes', {})}
                      for k, v in ITEMS.items()}}  # notes: where an agent kind covers less than the note says


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
        name, rest = os.path.basename(words[0]), words[1:]
        for floor in GIT_PUSH:
            n = min(len(floor), len(words))
            if floor[:n] == [name, *rest][:n]:
                raise ValueError('Pushing stays blocked at every level. You merge in Review.')
        if name in LAUNCHERS:
            raise ValueError(f'Allowing {words[0]} would allow anything run through it. Name the program itself.')
        if name in RUNNERS and all(w.startswith('-') or w in RUNS_ANYTHING for w in rest):
            raise ValueError(f'Allowing {" ".join(words)} would allow anything it runs. Add what it runs, '
                             f'like {name} lint or {name} scripts/check.py.')
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


def variants(program, cwd=None):
    """The ways a program is run: a script path also as ./path, its full path in the worktree, bash path and sh path."""
    first, rest = program[0], program[1:]
    if '/' not in first or first.startswith(('/', '~')):
        return [program]
    p = first[2:] if first.startswith('./') else first
    paths = [p, './' + p] + ([os.path.join(cwd, p)] if cwd else [])
    return [[x, *rest] for x in paths] + [[sh, x, *rest] for sh in ('bash', 'sh') for x in paths]


def _bash(program):
    return f'Bash({" ".join(program)}:*)'


def _secret_commands():
    """Shell commands that name a secret path (`cat ~/.ssh/id_ed25519`, `cd ~/.aws`), however home is written.
    shortcut: a path built another way (a variable, a glob) still gets through; step 16 checks every command."""
    home = os.path.expanduser('~')
    return [f'Bash(* {h}/{p[2:].removesuffix("/**")}*)' for p in SECRET_PATHS for h in ('~', '$HOME', home)]


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
            deny += [f'Read({p})' for p in SECRET_PATHS] + _secret_commands()
    for r in role.rules:
        (allow if r['decision'] == 'allow' else deny).extend(_bash(v) for v in variants(r['program'], cwd))
    return allow, deny  # a deny always wins in Claude Code, so a block rule beats the level's allow


def codex_args(role):
    """Codex's sandbox for the level; [] for a role without one."""
    if not role.level:
        return []
    if role.level == 'read-only' or role.checkout == 'read-only':
        return ['--sandbox', 'read-only']
    a = ['--sandbox', 'workspace-write']
    return a + ['-c', 'sandbox_workspace_write.network_access=true'] if role.level == 'trusted' else a


def _codex_lines(role, cwd):
    """(forbidden, allowed) rule lines for one role; ([], []) for a role without a level."""
    if not role.level:
        return [], []
    allowed, blocked = _items(role)
    forbid, allow = [], []

    def rule(program, decision, why):
        return f'prefix_rule(pattern={json.dumps(program)}, decision="{decision}", justification={json.dumps(why)})'
    for key in blocked:
        forbid += [rule(p, 'forbidden', ITEMS[key]['words']) for p in ITEMS[key].get('programs') or []]
    # Tests and reading run inside the sandbox; commits need .git, which the sandbox keeps read-only.
    for key in allowed:
        if key in ('commit', 'install', 'download', 'network'):
            allow += [rule(p, 'allow', ITEMS[key]['words']) for p in ITEMS[key]['programs']]
    for r in role.rules:
        for v in variants(r['program'], cwd):
            line = rule(v, 'forbidden' if r['decision'] == 'block' else 'allow', r.get('words') or 'your rule')
            (forbid if r['decision'] == 'block' else allow).append(line)
    return forbid, allow


def codex_rules(roles, cwd=None):
    """The program rules for the Codex agents that work in one folder, or None when none has a level.

    Codex loads every .rules file in the folder, so each agent there gets all of them: one file holds every agent's
    blocks and only the allows they all have, so no agent is widened by another's rules."""
    if not any(r.level for r in roles):
        return None
    lines = [_codex_lines(r, cwd) for r in roles]
    forbid = list(dict.fromkeys(x for f, _ in lines for x in f))
    allow = [x for x in dict.fromkeys(lines[0][1]) if all(x in a for _, a in lines[1:])]
    names = ', '.join(r.name for r in roles)
    return '\n'.join([f'# Written by Assieme for {names} when it starts. Change it in Assieme, not here.',
                      *forbid, *allow]) + '\n'


def write_codex_rules(cwd, roles):
    """Write (or, when no agent there has a level, remove) the rules file for the Codex agents in this folder."""
    path = os.path.join(cwd, RULES_FILE)
    text = codex_rules(roles, cwd)
    if text is None:
        if os.path.exists(path):
            os.remove(path)
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    return path


def _program_lists(role, cwd=None, test=None):
    """(allow, deny, allowed items, blocked items): the level's programs, then the person's rules, for the kinds whose
    rules are plain command prefixes."""
    allowed, blocked = _items(role)
    allow = [p for k in allowed for p in ITEMS[k].get('programs') or []] + ([test] if test and 'tests' in allowed else [])
    deny = [p for k in blocked for p in ITEMS[k].get('programs') or []]
    for r in role.rules:
        (allow if r['decision'] == 'allow' else deny).extend(variants(r['program'], cwd))
    return allow, deny, allowed, blocked


def _secret_dirs():
    home = os.path.expanduser('~')
    return [f'{home}/{p[2:].removesuffix("/**")}' for p in SECRET_PATHS]


def opencode(role, cwd=None, test=None):
    """OpenCode's `permission` rules, or None for a role without a level. Anything not named asks the person."""
    if not role.level:
        return None
    allow, deny, allowed, blocked = _program_lists(role, cwd, test)
    bash = {'*': 'ask'}
    for decision, programs in (('allow', allow), ('deny', deny)):  # denies last: the last matching pattern wins
        for p in programs:
            for pattern in (' '.join(p), ' '.join(p) + ' *'):
                bash.pop(pattern, None)
                bash[pattern] = decision
    out = {'bash': bash, 'edit': 'deny' if 'read' in allowed else 'allow',
           'webfetch': 'allow' if 'network' in allowed else 'deny', 'external_directory': 'ask'}
    if 'secrets' in blocked:
        out['read'] = {'*': 'allow', **{p: 'deny' for p in SECRET_PATHS}}
    return out


GEMINI_POLICY = os.path.join('.gemini', 'policies', 'assieme.toml')


def gemini_policy(role, cwd=None, test=None):
    """Gemini CLI's policy rules (TOML), or None for a role without a level. A deny outranks any allow; anything not
    named asks the person. Read only runs in Gemini's plan mode, which never edits."""
    if not role.level:
        return None
    allow, deny, allowed, blocked = _program_lists(role, cwd, test)
    out = [f'# Written by Assieme for {role.name} when it starts. Change it in Assieme, not here.', '']

    def rule(decision, priority, **fields):
        out.extend(['[[rule]]', *(f'{k} = {json.dumps(v)}' for k, v in fields.items()),
                    f'decision = "{decision}"', f'priority = {priority}', ''])
    for p in allow:
        rule('allow', 500, toolName='run_shell_command', commandPrefix=' '.join(p))
    for p in deny:
        rule('deny', 900, toolName='run_shell_command', commandPrefix=' '.join(p))
    if 'network' in blocked:
        rule('deny', 900, toolName='web_fetch')
    if 'secrets' in blocked:
        for d in _secret_dirs():  # its file tools, by the path in their arguments
            rule('deny', 900, toolName='*', argsPattern=re.escape(d))
    return '\n'.join(out)


def write_gemini_policy(cwd, role, test=None):
    """The policy file in the agent's own folder, kept out of git; its path, or None (and no file) without a level."""
    path = os.path.join(cwd, GEMINI_POLICY)
    text = gemini_policy(role, cwd, test)
    if text is None:
        if os.path.exists(path):
            os.remove(path)
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    return path


def _cursor_shell(program):
    """Cursor matches a command by its first word, and its arguments after a colon."""
    return f'Shell({program[0]})' if len(program) == 1 else f'Shell({program[0]}:{" ".join(program[1:])}*)'


def cursor(role, cwd=None, test=None):
    """Cursor's `permissions` {allow, deny}, or None for a role without a level. A deny always wins in Cursor."""
    if not role.level:
        return None
    allow, deny, allowed, blocked = _program_lists(role, cwd, test)
    out = {'allow': [_cursor_shell(p) for p in allow], 'deny': [_cursor_shell(p) for p in deny]}
    if 'network' in blocked:
        out['deny'].append('WebFetch(*)')
    if 'secrets' in blocked:
        out['deny'] += [f"Read({os.path.expanduser('~')}/{p[2:]})" for p in SECRET_PATHS]
    if 'read' in allowed:  # Read only
        out['deny'].append('Write(**)')
    return {k: list(dict.fromkeys(v)) for k, v in out.items()}
