"""Import from herdr: a running herdr team, a saved herdr workspace or a launch script, as a New team draft.

Read-only: herdr's files and terminals are only read, and nothing is started. What doesn't map is said in the
draft's `notes`, one plain sentence each, instead of being dropped.
"""
import collections
import json
import os
import re
import shlex
import subprocess

from . import launch, newteam, status, worktree

KINDS = ('claude', 'codex')
# Agent kinds herdr knows (`herdr agent start --kind`): one of these that isn't Claude Code or Codex is left out with a
# note; any other program (a shell, a dev server) isn't an agent and is skipped.
HERDR_KINDS = {'pi', 'claude', 'codex', 'gemini', 'cursor', 'devin', 'agy', 'cline', 'omp', 'mastracode', 'opencode',
               'copilot', 'kimi', 'kiro', 'droid', 'amp', 'grok', 'hermes', 'kilo', 'qodercli', 'qwen', 'letta', 'maki',
               'muse', 'cursor-agent', 'aider'}
MAX_BRIEF = 256 * 1024

# Options that take a value; the rest are switches. Claude Code's tool and folder lists take several values.
VALUED = {'claude': {'--model', '--effort', '--permission-mode', '--settings', '--mcp-config', '--append-system-prompt',
                     '--append-system-prompt-file', '--system-prompt', '--system-prompt-file', '--fallback-model',
                     '--session-id', '--resume', '-r', '--output-format', '--input-format', '--agents', '--name', '-n',
                     '--add-dir', '--allowedTools', '--allowed-tools', '--disallowedTools', '--disallowed-tools',
                     '--plugin-dir', '--setting-sources'},
          'codex': {'-m', '--model', '-c', '--config', '-s', '--sandbox', '-a', '--ask-for-approval', '-p', '--profile',
                    '-C', '--cd', '-i', '--image', '--add-dir', '-o', '--output-last-message', '--enable', '--disable'}}
SEVERAL = {'--allowedTools', '--allowed-tools', '--disallowedTools', '--disallowed-tools', '--add-dir'}
# Read here, so they aren't listed as left out.
READ = {'--model', '-m', '--effort', '--permission-mode', '--dangerously-skip-permissions',
        '--allow-dangerously-skip-permissions', '--append-system-prompt', '--append-system-prompt-file',
        '--system-prompt', '--system-prompt-file', '--allowedTools', '--allowed-tools', '--disallowedTools',
        '--disallowed-tools', '-s', '--sandbox', '-a', '--ask-for-approval', '--full-auto', '--yolo',
        '--dangerously-bypass-approvals-and-sandbox', '-c', '--config', '--settings', '--mcp-config'}
WIDE_MODES = {'auto', 'acceptEdits', 'bypassPermissions', 'dontAsk'}
ARRAY = '\x01'  # "${opts[@]}": options only known when the script runs
HC_VALUED = {'--name', '--cwd', '--on', '--label', '--parent', '--timeout'}


def config_dir():
    return os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'herdr')


def _short(path):
    home = os.path.expanduser('~')
    return '~' + path[len(home):] if path and (path + '/').startswith(home + '/') else path


def _slug(s):
    return re.sub(r'[^a-z0-9]+', '-', (s or '').lower()).strip('-')


def _kind_of(name):
    """The standard role a name suggests, for its brief and job."""
    for word, kind in (('plan', 'planner'), ('coord', 'planner'), ('review', 'reviewer'), ('test', 'tester')):
        if word in name:
            return kind
    return 'builder'


def _jobs():
    full = newteam._as_preset(os.path.join(newteam.PRESETS_DIR, 'full'), 'full')['roles']
    return {r['name'].split('-')[0]: r['job'] for r in full}


def _checkout(cwd):
    """(the project's main checkout, the branch it is on) for a folder; the folder itself when it isn't in git."""
    if not cwd or not os.path.isdir(cwd):
        return cwd, None
    common = worktree._git(cwd, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if common.returncode != 0:
        return cwd, None
    path = common.stdout.strip()
    main = os.path.dirname(path) if os.path.basename(path) == '.git' else path
    branch = worktree._git(cwd, 'branch', '--show-current').stdout.strip() or None
    return os.path.realpath(main), branch


# --- one agent's command line ---------------------------------------------------------------------------------

def _options(kind, args):
    """{option: [values]} and the plain words (the first message, or Codex's subcommand and prompt)."""
    opts, words, i = collections.defaultdict(list), [], 0
    while i < len(args):
        a = args[i]
        if a is None or not a.startswith('-') or a == '-':
            words.append(a)
        elif a == '--':
            words += args[i + 1:]
            break
        elif a.startswith('--') and '=' in a:
            flag, _, value = a.partition('=')
            opts[flag].append(value)
        elif a in VALUED[kind] and kind == 'claude' and a in SEVERAL:
            j = i + 1
            while j < len(args) and not (args[j] or '').startswith('-'):
                opts[a].append(args[j])
                j += 1
            i = j
            continue
        elif a in VALUED[kind]:
            opts[a].append(args[i + 1] if i + 1 < len(args) else None)
            i += 2
            continue
        else:
            opts[a].append(True)
        i += 1
    return opts, words


def _unquote(v):
    return v[1:-1] if v and len(v) > 1 and v[0] == v[-1] and v[0] in '"\'' else v


def _read_brief(path, say):
    if path is None:
        say("its brief file is chosen when the script runs, so it couldn't be read.")
        return ''
    try:
        if os.path.getsize(path) > MAX_BRIEF:
            raise OSError
        with open(path, encoding='utf-8') as f:
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        say(f"its brief file {_short(path)} couldn't be read.")
        return ''


def agent_role(name, kind, args, env_names=()):
    """A New team role from one Claude Code or Codex command line, and what didn't carry over."""
    notes = []
    say = lambda text: notes.append(f'{name}: {text}')
    if ARRAY in args:
        say("some of its options are chosen when the script runs, so they couldn't be read.")
        args = [a for a in args if a != ARRAY]
    opts, words = _options(kind, args)
    first = lambda *flags: next((v for f in flags for v in opts.get(f, [])), False)
    role = {'name': name, 'agent': kind, 'job': '', 'checkout': 'branch', 'parent': None, 'brief': '',
            'model': None, 'effort': None}

    config = {}
    for c in opts.get('-c', []) + opts.get('--config', []):
        if isinstance(c, str) and '=' in c:
            key, _, value = c.partition('=')
            config[key.strip()] = _unquote(value.strip())
        elif c is None:
            say("one of its settings is chosen when the script runs, so it couldn't be read.")
    model = first('--model', '-m') or config.pop('model', False)
    effort = first('--effort') or config.pop('model_reasoning_effort', False)
    if model is None:
        say('its model is chosen when the script runs, so it gets the default model.')
    elif model and launch.MODEL.match(model):
        role['model'] = model
    elif model:
        say(f"its model {model!r} isn't one Assieme can pass on, so it gets the default model.")
    if effort is None:
        say('its thinking effort is chosen when the script runs, so it gets the default.')
    elif effort and not launch.check_model_effort(kind, None, effort):
        role['effort'] = effort
    elif effort:
        say(f'{launch.NAMES[kind]} has no thinking effort {effort!r}, so it gets the default.')

    wide = []
    if kind == 'claude':
        mode = first('--permission-mode')
        blocked = {t for v in opts.get('--disallowedTools', []) + opts.get('--disallowed-tools', []) if v
                   for t in re.split(r'[\s,]+', v)}
        if mode == 'plan' or {'Edit', 'Write'} & blocked:
            role['checkout'] = 'read-only'
        if mode in WIDE_MODES:
            wide.append(f'permission mode "{mode}"')
        if first('--dangerously-skip-permissions', '--allow-dangerously-skip-permissions'):
            wide.append('permission checks skipped')
        if opts.get('--allowedTools') or opts.get('--allowed-tools'):
            wide.append('extra allowed tools')
    else:
        sandbox = first('-s', '--sandbox') or config.pop('sandbox_mode', False)
        if sandbox == 'read-only':
            role['checkout'] = 'read-only'
        elif sandbox == 'danger-full-access':
            wide.append('full access outside its sandbox')
        approval = first('-a', '--ask-for-approval') or config.pop('approval_policy', False)
        if approval == 'never':
            wide.append('no approval asked')
        if first('--full-auto'):
            wide.append('"--full-auto"')
        if first('--yolo', '--dangerously-bypass-approvals-and-sandbox'):
            wide.append('approvals and sandbox turned off')
        if any(k.startswith('sandbox_') for k in config):
            wide.append('its own sandbox settings')
        if words and words[0] in ('exec', 'e'):
            words = words[1:]
            say('it ran once ("codex exec") and stopped; here it stays running and waits for work.')
        config = {k: v for k, v in config.items() if not k.startswith('sandbox_')}
    if wide:
        say(f'it ran with {", ".join(wide)}. Assieme starts it with safe permissions instead '
            "(it edits its own copy and can't push).")

    parts = []
    for f in ('--system-prompt-file', '--append-system-prompt-file'):
        parts += [_read_brief(p, say) for p in opts.get(f, [])]
    for f in ('--system-prompt', '--append-system-prompt'):
        for text in opts.get(f, []):
            if text is None:
                say('part of its brief is written when the script runs, so it is missing here.')
            parts.append(text or '')
    if any(w is None for w in words):
        say('its first message is written when the script runs, so it is missing here.')
    parts.append(' '.join(w for w in words if w))
    role['brief'] = '\n\n'.join(p for p in parts if p.strip())
    std = _kind_of(name)
    role['job'] = _jobs()[std]
    if not role['brief']:
        role['brief'] = newteam.kind_brief(std)
        say(f'no brief was found, so the standard {std} brief is filled in. Check it before you start.')

    if opts.get('--settings') or opts.get('--mcp-config'):
        say("its own settings file and MCP servers aren't carried over; Assieme adds its own.")
    if config:
        say(f"its settings {', '.join(sorted(config))} aren't carried over.")
    other = sorted(f for f in opts if f not in READ)
    if other:
        say(f"these options aren't carried over: {', '.join(other)}.")
    names = sorted({n for n in env_names if n != 'PATH'})
    if names:
        say(f"the environment variables it set ({', '.join(names)}) aren't carried over.")
    return role, notes


# --- shell words and variables --------------------------------------------------------------------------------

def _subs(text):
    """text with each outer $(...) swapped for a marker, and the texts inside them."""
    out, inner, i = [], [], 0
    while (j := text.find('$(', i)) >= 0:
        depth, k = 1, j + 2
        while k < len(text) and depth:
            depth += {'(': 1, ')': -1}.get(text[k], 0)
            k += 1
        out.append(text[i:j] + f'\x00{len(inner)}\x00')
        inner.append(text[j + 2:k - 1])
        i = k
    return ''.join(out + [text[i:]]), inner


VAR = re.compile(r'\$\{(\w+|BASH_SOURCE\[0\])(?:(:?[-+])([^}]*))?\}|\$(\w+)|\x00(\d+)\x00')


def _expand(s, env, inner=()):
    """The word with its variables filled in, or None when any of them is only known when the script runs."""
    unknown = False

    def one(m):
        nonlocal unknown
        if m.group(5) is not None:
            v = _command(inner[int(m.group(5))], env)
        else:
            v, op, word = env.get(m.group(1) or m.group(4)), m.group(2), m.group(3)
            if op and op.endswith('-') and not v:
                v = _expand(word, env, inner)
            elif op and op.endswith('+'):
                v = _expand(word, env, inner) if v else ''
        if v is None:
            unknown = True
        return v or ''

    out = VAR.sub(one, s)
    return None if unknown else os.path.expanduser(out) if out.startswith('~') else out


def _command(text, env):
    """What a few common $(...) give: command -v, which, dirname, basename, cd X && pwd. Otherwise None."""
    masked, inner = _subs(text)
    try:
        w = [_expand(x, env, inner) for x in shlex.split(masked)]
    except ValueError:
        return None
    if w[:2] == ['command', '-v'] and len(w) == 3 or w[:1] == ['which'] and len(w) == 2:
        return w[-1]
    if len(w) == 2 and w[0] in ('dirname', 'basename') and w[1]:
        return getattr(os.path, w[0])(w[1].rstrip('/') or '/')
    if len(w) >= 4 and w[0] == 'cd' and w[1] and w[2] == '&&' and w[3] == 'pwd':
        return os.path.normpath(w[1])
    return None


def _words(text, env):
    """The shell words of one command, variables filled in (None where unknown), up to the first | ; && ) or >."""
    masked, inner = _subs(text)
    lex = shlex.shlex(masked, posix=True, punctuation_chars=';&|<>()')
    lex.whitespace_split, lex.commenters = True, ''
    out = []
    try:
        for t in lex:
            if t and all(c in ';&|<>()' for c in t):
                break
            if re.fullmatch(r'\$\{\w+\[[@*]\]\}', t):
                out.append(ARRAY)
                continue
            # shortcut: a word found in single quotes is taken as literal; a mixed-quoted word may expand wrongly.
            restored = re.sub('\x00(\\d+)\x00', lambda m: f'$({inner[int(m.group(1))]})', t)
            word = restored if f"'{t}'" in masked else _expand(t, env, inner)
            if word is None and re.match(r'^[A-Za-z_]\w*=', t):  # PATH="$PATH": the name still counts
                word = t.split('=', 1)[0] + '='
            out.append(word)
    except ValueError:
        pass
    return out


def _split(argv):
    """(program, its arguments, the variables set in front of it) for a command line."""
    env, i = {}, 0
    while i < len(argv) and argv[i] is not None:
        if os.path.basename(argv[i]) == 'env':
            i += 1
        elif re.match(r'^[A-Za-z_]\w*=', argv[i]):
            k, _, v = argv[i].partition('=')
            env[k] = v
            i += 1
        else:
            break
    if i >= len(argv) or argv[i] is None:
        return None, [], env
    prog, args = os.path.basename(argv[i]), argv[i + 1:]
    if prog in ('sh', 'bash', 'zsh') and '-c' in args and args.index('-c') + 1 < len(args):
        for line in (args[args.index('-c') + 1] or '').splitlines():
            words = _words(line, env)  # the variables set in front of it fill in its words
            for j, w in enumerate(words):
                if w and os.path.basename(w) in HERDR_KINDS:
                    return os.path.basename(w), words[j + 1:], env
    return prog, args, env


# --- the three sources ----------------------------------------------------------------------------------------

def _team(members, project=None):
    """The draft from [(herdr name, program, args, env names, cwd, parent name)], one per terminal or pane."""
    roles, notes, taken, cwds = [], [], set(), {}
    for n, (name, prog, args, env_names, cwd, parent) in enumerate(members, 1):
        if prog is None:
            notes.append(f"{name or f'agent-{n}'}: the program it runs is chosen when the script runs, so it is left out.")
            continue
        if prog not in KINDS:
            if prog in HERDR_KINDS:  # anything else (a shell, a dev server) isn't an agent
                notes.append(f"{name or prog}: it runs {prog}, which Assieme doesn't start yet, so it is left out.")
            continue
        slug = _slug(name)
        if not slug:
            slug = f'agent-{n}'
            notes.append(f"{slug}: its name is chosen when the script runs, so it is called {slug} here.")
        base, k = slug, 2
        while slug in taken:
            slug, k = f'{base}-{k}', k + 1
        taken.add(slug)
        role, said = agent_role(slug, prog, list(args), env_names)
        role['_herdr'], role['_parent'] = name, parent
        roles.append(role)
        notes += said
        cwds[slug] = cwd
    by_herdr = {r['_herdr']: r['name'] for r in roles if r['_herdr']}
    for r in roles:
        parent = r.pop('_parent')
        r.pop('_herdr')
        if parent is None:
            notes.append(f'{r["name"]}: who it reported to is decided when the script runs, so here it reports to no one.')
        elif parent and parent in by_herdr and by_herdr[parent] != r['name']:
            r['parent'] = by_herdr[parent]
        elif parent:
            notes.append(f"{r['name']}: it reported to {parent}, which isn't in this team, so it reports to no one.")
    places = {name: _checkout(cwd) for name, cwd in cwds.items() if cwd}
    mains = collections.Counter(main for main, _ in places.values() if main)
    if not project:
        project = mains.most_common(1)[0][0] if mains else ''
    if len(mains) > 1:
        others = ', '.join(_short(m) for m in mains if m != project)
        notes.append(f'The agents worked in {len(mains)} folders. This team uses {_short(project)}; '
                     f'the others ({others}) are left as they are.')
    for r in roles:
        main, branch = places.get(r['name'], (None, None))
        if r['checkout'] == 'branch' and branch and os.path.realpath(cwds[r['name']]) != main:
            notes.append(f'{r["name"]}: it worked on branch {branch}. Here it gets a fresh branch of the project; '
                         f'{branch} stays as it is.')
    return {'project': project, 'task': '', 'roles': roles, 'notes': notes}


def _hc_terminals():
    """Live herdr-core terminals with their full details, or [] when hc is missing or isn't running."""
    try:
        listed = status._hc_json('ls').get('terminals') or []
        return [status._hc_json('get', t['address']) for t in listed if t.get('state') == 'live']
    except (OSError, ValueError, AttributeError, KeyError, TypeError, subprocess.SubprocessError):
        return []


def _running(terminals=None):
    """{team key: [terminal]} for herdr-core terminals running an agent, by herdr space, else by project folder."""
    teams = collections.defaultdict(list)
    for t in _hc_terminals() if terminals is None else terminals:
        prog = _split(t.get('argv') or [])[0]
        if prog not in HERDR_KINDS and (t.get('agent') or '') not in HERDR_KINDS:
            continue
        key = (t.get('labels') or {}).get('herdr.space') or _checkout(t.get('cwd'))[0] or 'herdr'
        teams[key].append(t)
    return teams


def _from_terminals(ts):
    names = {t.get('address'): t.get('name') or t.get('address') for t in ts}
    members = []
    for t in ts:
        prog, args, env = _split(t.get('argv') or [])
        if prog not in HERDR_KINDS:
            prog = t.get('agent') or prog
        # A parent outside the team is usually the shell it was started from: no one to report to here.
        members.append((names[t.get('address')], prog, args, env, t.get('cwd'), names.get(t.get('parent'), '')))
    return _team(members)


def _sessions(cfg=None):
    """[(session name, its saved file)] for herdr's default session and its named ones."""
    cfg = cfg or config_dir()
    found = [('default', os.path.join(cfg, 'session.json'))]
    folder = os.path.join(cfg, 'sessions')
    if os.path.isdir(folder):
        found += [(n, os.path.join(folder, n, 'session.json')) for n in sorted(os.listdir(folder))]
    out = []
    for name, path in found:
        try:
            with open(path) as f:
                data = json.load(f)
            out.append((name, data.get('workspaces') or []))
        except (OSError, ValueError, AttributeError):
            continue
    return out


def _agent_panes(ws):
    return [p for t in ws.get('tabs') or [] for p in (t.get('panes') or {}).values()
            if p.get('managed_agent_kind') or (p.get('agent_session') or {}).get('agent')]


def _from_workspace(ws):
    kinds = [p.get('managed_agent_kind') or p['agent_session']['agent'] for p in _agent_panes(ws)]
    members = [(p.get('agent_name') or k, k, [], {}, p.get('cwd'), '') for p, k in zip(_agent_panes(ws), kinds)]
    draft = _team(members)
    if draft['roles']:
        draft['notes'].insert(0, "herdr doesn't save models, briefs or permissions, so each agent gets the default "
                                 'model, the standard brief for its role and its own branch. Check them before you start.')
    return draft


def _from_script(path):
    """The agents a launch script starts with `hc run` or `herdr agent start`, helper functions followed one level."""
    with open(path, encoding='utf-8') as f:
        raw = re.sub(r'\\\n', ' ', f.read(MAX_BRIEF * 4)).splitlines()
    lines, buf = [], []
    for line in raw:  # a quoted text may run over several lines (sh -c '...')
        buf.append(line)
        try:
            shlex.split('\n'.join(buf), comments=True)
        except ValueError:
            if len(buf) < 50:
                continue
        lines.append('\n'.join(buf))
        buf = []
    lines += buf
    path = os.path.realpath(path)
    env = {'HOME': os.path.expanduser('~'), '0': path, 'BASH_SOURCE[0]': path, 'BASH_SOURCE': path}
    at = r'(?:^\s*|[;&|()`]\s*|\b(?:then|do|else)\s+)'  # where a command starts, not a word in its arguments
    starts = re.compile(at + r'(hc\s+run|herdr\s+agent\s+start)\s(.*)', re.S)
    funcs, i, walk = {}, 0, []
    while i < len(lines):  # helper functions that start an agent are followed where they are called
        m = re.match(r'^(\s*)(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{\s*(#.*)?$', lines[i])
        end = i
        if m:
            end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() == '}'
                        and len(lines[j]) - len(lines[j].lstrip()) <= len(m.group(1))), len(lines) - 1)
            if any(starts.search(x) for x in lines[i + 1:end]):
                funcs[m.group(2)] = lines[i + 1:end]
                i = end + 1
                continue
        walk.append(lines[i])
        i += 1
    members = []

    def run(text, env, depth=0):
        for line in text:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            _assign(stripped, env)
            m = starts.search(line)
            if m:
                members.append(_script_member(m.group(1).split()[0], _words(m.group(2), env)))
                continue
            for name, body in funcs.items():
                call = re.search(at + re.escape(name) + r'\s+(.*)$', stripped, re.S)
                if call and depth < 3:  # a helper that calls itself stops here
                    args = _words(call.group(1), env)
                    run(body, env | {str(k): v for k, v in enumerate(args, 1)}, depth + 1)

    run(walk, env)
    project = _checkout(os.path.dirname(path))[0]
    return _team(members, project if not any(m[4] for m in members) else None)


def _assign(line, env):
    masked, inner = _subs(line)
    try:
        words = shlex.split(masked, comments=True)
    except ValueError:
        return
    declare = bool(words) and words[0] in ('export', 'local', 'readonly', 'declare', 'typeset')
    for w in words[1:] if declare else words:
        m = re.match(r'^([A-Za-z_]\w*)=(.*)$', w, re.S)
        if m:
            env[m.group(1)] = _expand(m.group(2), env, inner)
        elif not declare or not re.match(r'^[A-Za-z_]\w*$|^-', w):
            break


def _script_member(tool, words):
    """(herdr name, program, args, env, cwd, parent) for one `hc run ...` or `herdr agent start ...`."""
    name, cwd, parent, i = '', '', '', 0
    if tool == 'herdr':
        name, i = words[0] if words else None, 1
        kind = None
        while i < len(words) and words[i] != '--':
            if words[i] == '--kind' and i + 1 < len(words):
                kind = words[i + 1]
            i += 2 if words[i] in ('--kind', '--pane', '--timeout') else 1
        return name, kind, words[i + 1:], {}, '', ''
    while i < len(words) and words[i] is not None and words[i][:1] in ('-', ARRAY):
        if words[i] == ARRAY:  # its options may hold --parent
            parent = None if parent == '' else parent
            i += 1
            continue
        if words[i] == '--':
            i += 1
            break
        if words[i] in HC_VALUED and i + 1 < len(words):
            value = words[i + 1]
            if words[i] == '--name':
                name = value
            elif words[i] == '--cwd':
                cwd = value
            elif words[i] == '--parent':
                parent = value
            i += 2
        else:
            i += 1
    prog, args, env = _split(words[i:])
    return name, prog, args, env, cwd, parent


# --- what the app calls ---------------------------------------------------------------------------------------

def find(terminals=None, cfg=None):
    """Every herdr team on this Mac that can be imported: running ones first, then saved workspaces."""
    teams = []
    for key, ts in _running(terminals).items():
        label = key if not os.path.isabs(key) else os.path.basename(key)
        teams.append({'id': f'running:{key}', 'label': label, 'where': 'Running in herdr', 'agents': len(ts)})
    for session, workspaces in _sessions(cfg):
        for ws in workspaces:
            panes = _agent_panes(ws)
            if panes:
                folder = ws.get('identity_cwd') or ''
                teams.append({'id': f'saved:{session}:{ws.get("id")}',
                              'label': ws.get('custom_name') or os.path.basename(folder.rstrip('/')) or 'workspace',
                              'where': 'Saved in herdr' + ('' if session == 'default' else f', session {session}'),
                              'agents': len(panes)})
    return {'teams': teams}


def draft(source, terminals=None, cfg=None):
    """The New team draft for one source from `find`, or `script:<path>` for a launch script."""
    kind, _, rest = source.partition(':')
    if kind == 'running':
        ts = _running(terminals).get(rest)
        if not ts:
            raise ValueError("That herdr team isn't running any more.")
        return _from_terminals(ts)
    if kind == 'saved':
        session, _, ws_id = rest.rpartition(':')
        for name, workspaces in _sessions(cfg):
            for ws in workspaces if name == session else []:
                if str(ws.get('id')) == ws_id:
                    return _from_workspace(ws)
        raise ValueError("That herdr workspace isn't saved any more.")
    if kind == 'script':
        try:
            return _from_script(rest)
        except (OSError, UnicodeDecodeError):
            raise ValueError("That launch script couldn't be read.") from None
    raise ValueError(f'Unknown herdr source {source!r}.')
