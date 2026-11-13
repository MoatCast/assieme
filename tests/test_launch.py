"""Agent command lines for Claude Code and Codex, with the status hook."""
import json
import os
import subprocess
import tempfile
import tomllib
import unittest
import unittest.mock

from assieme import launch
from assieme.preset import Role

EVENTS = ['PermissionRequest', 'SessionStart', 'Stop', 'UserPromptSubmit']


def role(agent, model=None, checkout='branch', effort=None):
    return Role('builder-1', agent, 'b.md', 'SECRET BRIEF TEXT', model, checkout, effort=effort)


def plain_argv(r):
    return launch.argv(r, block_push=False, which=lambda n: None)


class LaunchTest(unittest.TestCase):
    def test_claude_hooks_map_events(self):
        a = plain_argv(role('claude'))
        self.assertEqual(a[0], 'claude')
        s = json.loads(a[a.index('--settings') + 1])
        self.assertEqual(sorted(s['hooks']), EVENTS)
        self.assertTrue(s['hooks']['Stop'][0]['hooks'][0]['command'].endswith('-m assieme.hook idle'))
        self.assertTrue(s['hooks']['UserPromptSubmit'][0]['hooks'][0]['command'].endswith('-m assieme.hook working'))
        self.assertTrue(s['hooks']['PermissionRequest'][0]['hooks'][0]['command'].endswith('-m assieme.hook blocked'))
        self.assertNotIn('Notification', s['hooks'])

    def test_codex_hooks_are_valid_toml(self):
        a = plain_argv(role('codex'))
        self.assertEqual(a[0], 'codex')
        values = [a[i + 1] for i, x in enumerate(a) if x == '-c' and a[i + 1].startswith('hooks.')
                  and not a[i + 1].startswith('hooks.state')]
        events = sorted(next(iter(tomllib.loads(v)['hooks'])) for v in values)
        self.assertEqual(events, EVENTS)
        stop = next(tomllib.loads(v) for v in values if 'Stop' in tomllib.loads(v)['hooks'])
        self.assertTrue(stop['hooks']['Stop'][0]['hooks'][0]['command'].endswith('-m assieme.hook idle'))

    def test_an_agent_outside_path_gets_its_own_folder_on_path(self):
        # An npm Codex under nvm is a `#!/usr/bin/env node` script: started by full path from a bare PATH, it exited
        # with "env: node: No such file or directory". Its node sits in the same folder.
        from assieme import quiet
        with unittest.mock.patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}):
            a = launch.argv(role('codex'), block_push=False, which=lambda n: '/u/.nvm/bin/codex')
            self.assertEqual(a[:3], ['/usr/bin/env', f'PATH={quiet.shim_dir()}:/u/.nvm/bin:/usr/bin:/bin', '/u/.nvm/bin/codex'])
            # Always set, even when the brain's PATH has the folder: the core's own PATH may be older and shorter.
            b = launch.argv(role('codex'), block_push=False, which=lambda n: '/usr/bin/codex')
            self.assertEqual(b[:3], ['/usr/bin/env', f'PATH={quiet.shim_dir()}:/usr/bin:/bin', '/usr/bin/codex'])

    def test_codex_asks_the_person_before_leaving_its_sandbox(self):
        # Codex keeps .git read-only in its sandbox, so a commit needs approval: the person gives it, each time.
        a = plain_argv(role('codex'))
        values = [a[i + 1] for i, x in enumerate(a) if x == '-c']
        self.assertIn('approval_policy="on-request"', values)
        self.assertIn('approvals_reviewer="user"', values)

    def test_codex_trusts_only_assiemes_own_hooks_for_this_run(self):
        # A first Codex team stopped at "Hooks need review". Codex reads trust for -c hooks from the same -c layer.
        a = plain_argv(role('codex'))
        state = next(tomllib.loads(a[i + 1]) for i, x in enumerate(a) if x == '-c' and a[i + 1].startswith('hooks.state'))
        keys = sorted(state['hooks']['state'])
        self.assertEqual(keys, ['/<session-flags>/config.toml:' + k + ':0:0'
                                for k in ('permission_request', 'session_start', 'stop', 'user_prompt_submit')])
        self.assertTrue(all(v['trusted_hash'].startswith('sha256:') for v in state['hooks']['state'].values()))

    def test_codex_hook_hash_matches_codex(self):
        # Vector checked against the trust Codex 0.167 itself wrote for this hook (path changed, same algorithm).
        self.assertEqual(launch.codex_hook_hash('session_start', '/opt/assieme/bin/python -P -m assieme.hook idle'),
                         'sha256:' + __import__('hashlib').sha256(
                             b'{"event_name":"session_start","hooks":[{"async":false,'
                             b'"command":"/opt/assieme/bin/python -P -m assieme.hook idle","timeout":600,"type":"command"}]}'
                         ).hexdigest())

    def test_codex_skips_update_prompt(self):
        # A real run stopped at Codex's "Update available" screen; agents must start without it.
        a = plain_argv(role('codex'))
        values = [a[i + 1] for i, x in enumerate(a) if x == '-c']
        self.assertIn('check_for_update_on_startup=false', values)

    def test_hook_ignores_the_worktree_on_import_path(self):
        # python -m puts the current folder (the agent's worktree) first on the import path; -P stops that.
        self.assertIn(' -P -m assieme.hook idle', launch.hook_command('idle'))

    def test_read_only(self):
        a = plain_argv(role('claude', checkout='read-only'))
        i = a.index('--disallowedTools')
        self.assertEqual(a[i + 1:i + 4], ['Edit', 'Write', 'NotebookEdit'])
        c = plain_argv(role('codex', checkout='read-only'))
        self.assertEqual(c[c.index('--sandbox') + 1], 'read-only')
        self.assertNotIn('--disallowedTools', plain_argv(role('claude')))
        self.assertNotIn('--sandbox', plain_argv(role('codex')))

    def test_model_flag(self):
        a = plain_argv(role('claude', model='opus'))
        self.assertEqual(a[a.index('--model') + 1], 'opus')
        c = plain_argv(role('codex', model='gpt-x'))
        self.assertEqual(c[c.index('-m') + 1], 'gpt-x')
        self.assertNotIn('--model', plain_argv(role('claude')))
        self.assertNotIn('-m', plain_argv(role('codex')))

    def test_brief_not_on_command_line(self):
        for agent in ('claude', 'codex'):
            self.assertFalse(any('SECRET BRIEF' in x for x in plain_argv(role(agent))))

    def test_missing_clis(self):
        roles = [role('claude'), role('codex'), role('codex')]
        self.assertEqual(launch.missing_clis(roles, which=lambda c: None if c == 'codex' else '/bin/' + c), ['codex'])


class ModelEffortTest(unittest.TestCase):
    def test_claude_model_and_effort(self):
        a = plain_argv(role('claude', model='opus', effort='high'))
        self.assertEqual(a[a.index('--model') + 1], 'opus')
        self.assertEqual(a[a.index('--effort') + 1], 'high')

    def test_codex_model_and_effort(self):
        a = plain_argv(role('codex', model='gpt-6-luna', effort='xhigh'))
        self.assertEqual(a[a.index('-m') + 1], 'gpt-6-luna')
        values = [a[i + 1] for i, x in enumerate(a) if x == '-c']
        self.assertEqual(tomllib.loads(next(v for v in values if v.startswith('model_reasoning_effort'))),
                         {'model_reasoning_effort': 'xhigh'})

    def test_defaults_add_nothing(self):
        self.assertNotIn('--effort', plain_argv(role('claude')))
        self.assertFalse(any(x.startswith('model_reasoning_effort') for x in plain_argv(role('codex'))))
        self.assertNotIn('-m', plain_argv(role('codex')))

    def test_check_model_effort(self):
        ok = launch.check_model_effort
        self.assertIsNone(ok('claude', 'opus', 'max'))
        self.assertIsNone(ok('codex', 'gpt-6.1-sol', 'ultra'))
        self.assertIsNone(ok('claude', None, None))
        self.assertEqual(ok('claude', None, 'ultra'), 'Claude Code has no thinking effort "ultra".')
        self.assertEqual(ok('codex', 'gpt"; rm', None), 'Model names use letters, digits, dots and dashes.')
        self.assertEqual(ok('claude', 'a b', None), 'Model names use letters, digits, dots and dashes.')


class BlockPushTest(unittest.TestCase):
    def test_agents_cannot_push_by_default(self):
        for agent in ('claude', 'codex'):
            a = launch.argv(role(agent), which=lambda n: f'/x/{n}')
            self.assertEqual(a[:1], ['/usr/bin/env'])
            self.assertIn('GIT_CONFIG_KEY_0=url.assieme-push-blocked://.pushInsteadOf', a)
            self.assertIn('GIT_CONFIG_VALUE_0=', a[:a.index(f'/x/{agent}')])

    def test_turning_safe_by_default_off_removes_the_block(self):
        a = launch.argv(role('claude'), block_push=False, which=lambda n: f'/x/{n}')
        self.assertFalse(any(w.startswith('GIT_CONFIG') for w in a))
        self.assertIn('/x/claude', a)

    def test_a_missing_agent_keeps_its_plain_name(self):
        self.assertEqual(launch.argv(role('codex'), block_push=False, which=lambda n: None)[0], 'codex')

    def test_known_limit_a_remote_with_its_own_push_address_still_pushes(self):
        # Documented in README.md and SECURITY.md: git doesn't apply pushInsteadOf to an explicit remote.<name>.pushurl.
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')
            git = lambda *a, cwd=root, e=env: subprocess.run(['git', *a], cwd=cwd, env=e, capture_output=True, text=True)
            git('init', '-q', '--bare', 'origin.git')
            git('clone', '-q', 'origin.git', 'work')
            work = os.path.join(root, 'work')
            git('config', 'remote.origin.pushurl', os.path.join(root, 'origin.git'), cwd=work)
            git('-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-q', '--allow-empty', '-m', 'one', cwd=work)
            self.assertEqual(git('push', '-q', 'origin', 'HEAD', cwd=work, e=env | launch.BLOCK_PUSH).returncode, 0)

    def test_the_setting_really_stops_a_push_and_keeps_fetch(self):
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')
            git = lambda *a, cwd=root, e=env: subprocess.run(['git', *a], cwd=cwd, env=e, capture_output=True, text=True)
            git('init', '-q', '--bare', 'origin.git')
            git('clone', '-q', 'origin.git', 'work')
            work = os.path.join(root, 'work')
            git('-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-q', '--allow-empty', '-m', 'one', cwd=work)
            blocked = env | launch.BLOCK_PUSH
            push = git('push', '-q', 'origin', 'HEAD', cwd=work, e=blocked)
            self.assertNotEqual(push.returncode, 0)
            self.assertEqual(git('rev-parse', '--verify', '-q', 'HEAD', cwd=os.path.join(root, 'origin.git')).returncode, 1)
            self.assertEqual(git('fetch', '-q', 'origin', cwd=work, e=blocked).returncode, 0)
            self.assertEqual(git('push', '-q', 'origin', 'HEAD', cwd=work).returncode, 0)  # without the setting it works


class McpTest(unittest.TestCase):
    def test_claude_gets_the_server_and_needs_no_permission_for_it(self):
        a = plain_argv(role('claude'))
        self.assertEqual(json.loads(a[a.index('--mcp-config') + 1])['mcpServers']['assieme']['args'], ['-P', '-m', 'assieme', 'mcp'])
        self.assertEqual(json.loads(a[a.index('--settings') + 1])['permissions']['allow'], ['mcp__assieme'])

    def test_codex_gets_the_server_with_the_variables_that_name_the_agent(self):
        a = plain_argv(role('codex'))
        conf = tomllib.loads('\n'.join(a[i + 1] for i, x in enumerate(a) if x == '-c' and a[i + 1].startswith('mcp_servers.')))
        s = conf['mcp_servers']['assieme']
        self.assertEqual(s['args'], ['-P', '-m', 'assieme', 'mcp'])
        self.assertIn('HC_TERMINAL_ID', s['env_vars'])


def env_of(a):
    """The KEY=VALUE words in front of the program, and the program's own words."""
    words = a[1:] if a[0] == '/usr/bin/env' else a
    env = {}
    while words and '=' in words[0] and not words[0].startswith('-'):
        k, _, v = words.pop(0).partition('=')
        env[k] = v
    return env, words


class NewKindsTest(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        p = unittest.mock.patch.dict(os.environ, {'XDG_STATE_HOME': self.state})
        p.start()
        self.addCleanup(p.stop)
        self.cwd = tempfile.mkdtemp()
        subprocess.run(['git', 'init', '-q', self.cwd], check=True)

    def argv(self, r):
        return launch.argv(r, block_push=False, which=lambda n: None, cwd=self.cwd)

    def test_gemini_reads_hooks_and_team_tools_from_its_folder_kept_out_of_git(self):
        env, words = env_of(self.argv(role('gemini', model='gemini-2.5-pro', checkout='read-only')))
        self.assertEqual(words, ['gemini', '-m', 'gemini-2.5-pro', '--approval-mode', 'plan'])
        self.assertNotIn('GEMINI_CLI_TRUST_WORKSPACE', env)  # only once the person said yes
        s = json.load(open(os.path.join(self.cwd, '.gemini', 'settings.json')))
        cmd = lambda e: s['hooks'][e][0]['hooks'][0]['command']
        self.assertTrue(cmd('BeforeAgent').endswith('assieme.hook working'))
        self.assertTrue(cmd('AfterAgent').endswith('assieme.hook idle'))
        self.assertTrue(cmd('SessionStart').endswith('assieme.hook idle'))
        self.assertEqual(s['hooks']['Notification'][0]['matcher'], 'ToolPermission')
        self.assertTrue(cmd('Notification').endswith('assieme.hook blocked'))
        self.assertEqual(s['mcpServers']['assieme']['args'], ['-P', '-m', 'assieme', 'mcp'])
        st = subprocess.run(['git', '-C', self.cwd, 'status', '--porcelain'], capture_output=True, text=True).stdout
        self.assertEqual(st, '')

    def test_trusted_gemini_and_cursor_are_told_so(self):
        env, _ = env_of(launch.argv(role('gemini'), block_push=False, which=lambda n: None, cwd=self.cwd, trusted=True))
        self.assertEqual(env['GEMINI_CLI_TRUST_WORKSPACE'], 'true')
        _, words = env_of(launch.argv(role('cursor'), block_push=False, which=lambda n: None, cwd=self.cwd, trusted=True))
        self.assertIn('--trust', words)

    @unittest.mock.patch.object(launch, "_opencode_major", return_value=1)  # whatever OpenCode this machine has
    def test_opencode_gets_its_config_and_the_status_plugin_from_its_environment(self, _major):
        env, words = env_of(self.argv(role('opencode', model='anthropic/claude-sonnet-4', checkout='read-only')))
        self.assertEqual(words, ['opencode', '-m', 'anthropic/claude-sonnet-4'])
        config = json.loads(env['OPENCODE_CONFIG_CONTENT'])
        self.assertTrue(os.path.isfile(config['plugin'][0].removeprefix('file://')))
        self.assertEqual(config['mcp']['assieme']['command'][-2:], ['assieme', 'mcp'])
        self.assertEqual(config['permission'], {'edit': 'deny'})
        self.assertEqual(json.loads(env['ASSIEME_HOOK'])[-2:], ['-m', 'assieme.hook'])

    def test_cursor_gets_its_files_in_its_folder_kept_out_of_git(self):
        _, words = env_of(self.argv(role('cursor', checkout='read-only')))
        self.assertEqual(words, ['cursor-agent', '--approve-mcps', '--mode', 'plan'])
        hooks = json.load(open(os.path.join(self.cwd, '.cursor', 'hooks.json')))['hooks']
        self.assertTrue(hooks['beforeSubmitPrompt'][0]['command'].endswith('assieme.hook working'))
        self.assertTrue(hooks['stop'][0]['command'].endswith('assieme.hook idle'))
        self.assertIn('assieme', json.load(open(os.path.join(self.cwd, '.cursor', 'mcp.json')))['mcpServers'])
        st = subprocess.run(['git', '-C', self.cwd, 'status', '--porcelain'], capture_output=True, text=True).stdout
        self.assertEqual(st, '')

    def test_cursor_keeps_a_projects_own_hooks_and_adds_its_own(self):
        os.makedirs(os.path.join(self.cwd, '.cursor'))
        with open(os.path.join(self.cwd, '.cursor', 'hooks.json'), 'w') as f:
            f.write('{"version": 1, "hooks": {"stop": [{"command": "theirs"}]}}')
        self.argv(role('cursor'))
        stop = json.load(open(os.path.join(self.cwd, '.cursor', 'hooks.json')))['hooks']['stop']
        self.assertEqual(stop[0], {'command': 'theirs'})
        self.assertTrue(stop[1]['command'].endswith('assieme.hook idle'))

    def test_new_kinds_take_no_thinking_effort(self):
        for kind in ('gemini', 'opencode', 'cursor'):
            self.assertIn('no thinking effort', launch.check_model_effort(kind, None, 'high'))


@unittest.skipUnless(__import__('shutil').which('node'), 'needs node')
class OpencodePluginTest(unittest.TestCase):
    def test_states_reach_the_hook(self):
        d = tempfile.mkdtemp()
        out, hook = os.path.join(d, 'said'), os.path.join(d, 'hook.sh')
        with open(hook, 'w') as f:
            f.write(f'#!/bin/sh\necho "$1" >> {out}\n')
        os.chmod(hook, 0o755)
        script = ("const m = await import(process.argv[1]); const p = await m.AssiemeStatus({});"
                  "for (const e of [{type: 'session.status', properties: {status: {type: 'busy'}}},"
                  "{type: 'permission.asked'}, {type: 'session.idle'}, {type: 'message.updated'}]) await p.event({event: e});"
                  "await new Promise(r => setTimeout(r, 300));")
        subprocess.run(['node', '--input-type=module', '-e', script, launch.OPENCODE_PLUGIN], check=True,
                       env={**os.environ, 'ASSIEME_HOOK': json.dumps([hook])})
        self.assertEqual(sorted(open(out).read().split()), sorted(['idle', 'working', 'blocked', 'idle']))
