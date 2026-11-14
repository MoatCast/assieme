"""Settings → Your assistants (step 18a): Assieme installed into each app the way that app expects, and removed.

- Claude Code: a plugin from a local marketplace Assieme writes (assieme/plugin.py), with its ask hook.
- The Claude app: an extension (.mcpb), opened so the app shows its own install window; listed there as a connector.
- Codex in the ChatGPT app: a plugin from the same marketplace, with per-tool approval.

The old Connect button added plain `mcp` entries instead; installing removes them so an app never runs Assieme twice.
The Claude app rewrites its config file from memory while open (seen on 8 November 2026), so its old entry is only
removed with the app closed."""
import json
import os
import subprocess
import sys
import tomllib

from . import plugin, quiet, setup

NAME = 'assieme'
PLUGIN = 'assieme@assieme'
TIMEOUT_S = 60
APPS = (('claude-code', 'Claude Code', 'plugin'), ('claude-app', 'The Claude app', 'connector'),
        ('codex', 'Codex in the ChatGPT app', 'plugin'))
CLI = {'claude-code': 'claude', 'codex': 'codex'}
FINISH_IN_APP = 'Finish in the Claude app’s install window.'
QUIT_APP = 'Quit the Claude app, then open Settings here again to remove its old Assieme entry.'
REMOVE_IN_APP = 'Remove it in the Claude app: Settings → Extensions → Assieme.'


def market_dir():
    return os.path.join(quiet.state_dir(), 'assistants-plugin')


def _support(home):
    return os.path.join(home or os.path.expanduser('~'), 'Library', 'Application Support', 'Claude')


def desktop_config(home=None):
    return os.path.join(_support(home), 'claude_desktop_config.json')


def _load(path):
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError('not a settings object')
    return data


def _desktop_open(run):
    try:
        return run(['pgrep', '-x', 'Claude'], capture_output=True, text=True, timeout=TIMEOUT_S).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _cli(args, run):
    r = run(args, capture_output=True, text=True, timeout=TIMEOUT_S)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[:200] or 'it failed')
    return r.stdout


def _quiet(args, run):  # a step that may already be done (a marketplace added before, nothing to remove)
    try:
        return run(args, capture_output=True, text=True, timeout=TIMEOUT_S).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# What is installed

def _claude_code_has(exe, run):
    try:
        return any(p.get('id') == PLUGIN for p in json.loads(_cli([exe, 'plugin', 'list', '--json'], run)))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, AttributeError):
        return False


def _codex_has(home):
    # `codex plugin list` refuses to run while any configured marketplace is broken, so its own config is read
    try:
        with open(os.path.join(home or os.path.expanduser('~'), '.codex', 'config.toml'), 'rb') as f:
            return bool(tomllib.load(f).get('plugins', {}).get(PLUGIN, {}).get('enabled'))
    except (OSError, tomllib.TOMLDecodeError):
        return False


def _app_has(home):
    try:
        exts = _load(os.path.join(_support(home), 'extensions-installations.json')).get('extensions') or {}
    except (OSError, ValueError):
        return False
    return any(i.startswith('local.mcpb.') and (e.get('manifest') or {}).get('name') == NAME for i, e in exts.items())


def _old_app_entry(home):
    try:
        return NAME in (_load(desktop_config(home)).get('mcpServers') or {})
    except (OSError, ValueError, AttributeError):
        return False


def _drop_old_app_entry(home, run):
    """The old Connect button's entry in the Claude app's config, removed only while the app is closed."""
    if not _old_app_entry(home):
        return None
    if _desktop_open(run):
        return QUIT_APP
    path = desktop_config(home)
    data = _load(path)
    del data['mcpServers'][NAME]
    quiet._write_json(path, data)
    return None


def status(run=subprocess.run, which=setup.agent_path, home=None, python=None, market=None, problems=None):
    out = []
    for app, label, kind in APPS:
        if app in CLI:
            exe = which(CLI[app])
            available = bool(exe)
            installed = bool(exe) and (_claude_code_has(exe, run) if app == 'claude-code' else _codex_has(home))
        else:
            available = os.path.isdir(_support(home))
            installed = available and _app_has(home)
        row = {'app': app, 'label': label, 'kind': kind, 'available': available, 'installed': installed}
        problem = (problems or {}).get(app)
        if app == 'claude-app' and installed and not problem:
            problem = _drop_old_app_entry(home, run)
        if not available and not problem:
            problem = None
        if problem:
            row['problem'] = problem
        out.append(row)
    return {'apps': out}


# Installing and removing

def _check(app):
    if app not in {a for a, _, _ in APPS}:
        raise ValueError(f'No app named {app}.')


def install(app, run=subprocess.run, which=setup.agent_path, home=None, python=None, market=None):
    _check(app)
    market = market or market_dir()
    label = dict((a, l) for a, l, _ in APPS)[app]
    problem = None
    try:
        built = plugin.build(market, python or sys.executable)
        if app in CLI:
            exe = which(CLI[app])
            if not exe:
                problem = f'{"Codex" if app == "codex" else "Claude Code"} isn’t installed on this Mac.'
            else:
                scope = ['--scope', 'user'] if app == 'claude-code' else []
                if _quiet([exe, 'mcp', 'get', NAME], run):  # the old Connect button's entry
                    _cli([exe, 'mcp', 'remove', *scope, NAME], run)
                _quiet([exe, 'plugin', 'marketplace', 'add', market], run)  # already added: fine
                _cli([exe, 'plugin', 'install' if app == 'claude-code' else 'add', PLUGIN], run)
        else:
            if not os.path.isdir(_support(home)):
                problem = 'The Claude app isn’t installed on this Mac.'
            else:
                problem = _drop_old_app_entry(home, run)
                _cli(['open', built['extension']], run)  # the app's own install window; nothing written behind it
                if not _app_has(home):
                    problem = FINISH_IN_APP
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
        problem = f'{label} couldn’t install it: {e}'
    return status(run, which, home, problems={app: problem} if problem else None)


def remove(app, run=subprocess.run, which=setup.agent_path, home=None, python=None, market=None):
    _check(app)
    label = dict((a, l) for a, l, _ in APPS)[app]
    problem = None
    try:
        if app in CLI:
            exe = which(CLI[app])
            if exe:
                _cli([exe, 'plugin', 'uninstall' if app == 'claude-code' else 'remove', PLUGIN], run)
                _quiet([exe, 'plugin', 'marketplace', 'remove', NAME], run)
        else:
            problem = REMOVE_IN_APP  # the app owns its extensions; it removes them from its own Settings
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        problem = f'{label} couldn’t remove it: {e}'
    return status(run, which, home, problems={app: problem} if problem else None)


def handlers():
    return {'assistants.status': lambda: status(), 'assistants.install': lambda app: install(app),
            'assistants.remove': lambda app: remove(app)}
