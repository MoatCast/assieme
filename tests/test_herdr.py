"""Import from herdr, on made-up herdr teams only: running terminals, saved workspaces and launch scripts."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from assieme import herdr, newteam

# Made-up model lists: the tests never read this Mac's own Codex list.
KNOWN = {'claude': {'fable', 'opus', 'sonnet', 'haiku'}, 'codex': {'gpt-5', 'gpt-5-mini'}}
_known = mock.patch.object(herdr, '_known_models', return_value=KNOWN)


def setUpModule():
    _known.start()


def tearDownModule():
    _known.stop()
from tests.test_newteam import git_repo


def by_name(draft):
    return {r['name']: r for r in draft['roles']}


def notes_for(draft, name):
    """Each grouped note that lists this agent, as its sentence and the agent's own label."""
    return [f"{n['text']} | {a}" for n in draft['notes'] for a in n['agents'] if a == name or a.startswith(f'{name} (')]


def said(notes):
    """agent_role's (kind, agent, detail) records as one text."""
    return ' '.join(f'{k} {d}' for k, _, d in notes)


class CommandLineTest(unittest.TestCase):
    def test_claude_flags_map(self):
        role, notes = herdr.agent_role('lead', 'claude', ['--model', 'opus', '--effort', 'high', '--permission-mode',
                                                           'plan', 'Plan the parser work.'])
        self.assertEqual((role['model'], role['effort'], role['checkout']), ('opus', 'high', 'read-only'))
        self.assertEqual(role['brief'], 'Plan the parser work.')
        self.assertEqual(notes, [])

    def test_wide_permissions_are_not_carried_over_and_said(self):
        role, notes = herdr.agent_role('builder', 'claude', ['--permission-mode', 'bypassPermissions', 'Build it.'])
        self.assertEqual(role['checkout'], 'branch')
        self.assertEqual(notes, [('wide', 'builder', 'permission mode "bypassPermissions"')])

    def test_codex_flags_map(self):
        role, notes = herdr.agent_role('checker', 'codex', ['-m', 'gpt-5', '-c', 'model_reasoning_effort="xhigh"',
                                                             '-s', 'read-only', 'exec', 'Review the diff.'])
        self.assertEqual((role['model'], role['effort'], role['checkout']), ('gpt-5', 'xhigh', 'read-only'))
        self.assertEqual(role['brief'], 'Review the diff.')
        self.assertEqual(notes, [('exec', 'checker', '')])

    def test_codex_wide_sandbox_and_other_settings(self):
        role, notes = herdr.agent_role('builder', 'codex', ['-s', 'danger-full-access', '-a', 'never', '-c',
                                                             'hooks.x=1', '--no-alt-screen'])
        text = said(notes)
        self.assertIn('full access outside its sandbox', text)
        self.assertIn('no approval asked', text)
        self.assertIn('hooks.x', text)
        self.assertIn('--no-alt-screen', text)

    def test_brief_file_is_read_and_missing_brief_gets_standard_one(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        brief = os.path.join(d, 'rules.md')
        with open(brief, 'w') as f:
            f.write('Keep tests green.\n')
        role, notes = herdr.agent_role('builder', 'claude', ['--append-system-prompt-file', brief, 'Start on step 2.'])
        self.assertEqual(role['brief'], 'Keep tests green.\n\nStart on step 2.')
        role, notes = herdr.agent_role('lead', 'claude', ['--append-system-prompt-file', brief, 'Add a cart.'],
                                       holds_task=True)
        self.assertEqual((role['brief'], role['_task']), ('Keep tests green.', 'Add a cart.'))
        role, notes = herdr.agent_role('reviewer', 'claude', [])
        self.assertEqual(role['brief'], newteam.kind_brief('reviewer'))
        self.assertEqual(role['job'], 'Reads every diff before it merges')
        self.assertEqual(notes, [('standard-brief', 'reviewer', 'reviewer brief')])
        role, notes = herdr.agent_role('builder', 'claude', ['--append-system-prompt-file', os.path.join(d, 'gone.md')])
        self.assertIn("gone.md couldn't be read", said(notes))

    def test_bad_model_and_effort_fall_back_with_a_note(self):
        role, notes = herdr.agent_role('builder', 'claude', ['--model', 'o p u s', '--effort', 'ultra'])
        self.assertEqual((role['model'], role['effort']), (None, None))
        self.assertEqual(len(notes), 2 + 1)  # model, effort, standard brief

    def test_model_must_be_on_this_macs_list(self):
        role, notes = herdr.agent_role('builder', 'codex', ['-m', 'gpt-9-made-up', 'Go.'])
        self.assertIsNone(role['model'])
        self.assertIn("isn't on this Mac's list", said(notes))
        role, notes = herdr.agent_role('lead', 'claude', ['--model', 'claude-opus-5-5', 'Go.'])
        self.assertEqual(role['model'], 'claude-opus-5-5')  # a full Claude model name is fine
        role, notes = herdr.agent_role('lead', 'claude', ['--model', 'gpt-5', 'Go.'])
        self.assertIsNone(role['model'])

    def test_this_macs_codex_list_is_read(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        with open(os.path.join(d, 'models_cache.json'), 'w') as f:
            json.dump({'models': [{'slug': 'gpt-made-up', 'supported_reasoning_levels': [{'effort': 'high'}]}]}, f)
        _known.stop()
        try:
            with mock.patch.dict(os.environ, {'CODEX_HOME': d}):
                self.assertIn('gpt-made-up', herdr._known_models()['codex'])
        finally:
            _known.start()

    def test_relative_brief_file_is_read_from_the_agents_folder(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        os.makedirs(os.path.join(d, 'build'))
        with open(os.path.join(d, 'build', 'rules.md'), 'w') as f:
            f.write('Folder rules.')
        role, notes = herdr.agent_role('builder', 'claude', ['--append-system-prompt-file', 'build/rules.md'], base=d)
        self.assertEqual(role['brief'], 'Folder rules.')

    def test_jobs_are_read_once(self):
        herdr._jobs.cache_clear()
        with mock.patch.object(newteam, '_as_preset', wraps=newteam._as_preset) as load:
            for name in ('a', 'b', 'c'):
                herdr.agent_role(name, 'claude', ['Go.'])
        self.assertEqual(load.call_count, 1)

    def test_environment_names_only(self):
        role, notes = herdr.agent_role('builder', 'claude', ['Go.'], ['PATH', 'SECRET_TOKEN'])
        self.assertEqual(notes, [('extras', 'builder', 'SECRET_TOKEN')])

    def test_shell_wrapper_is_read_through(self):
        prog, args, env = herdr._split(['env', 'MODEL=gpt-5', 'sh', '-c', 'codex exec -m "$MODEL" "Look."'])
        self.assertEqual((prog, args), ('codex', ['exec', '-m', 'gpt-5', 'Look.']))


class RunningTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.project = os.path.realpath(git_repo(os.path.join(self.d, 'shop')))
        self.wt = os.path.join(self.d, 'shop-builder')
        subprocess.run(['git', '-C', self.project, 'worktree', 'add', '-q', '-b', 'feature/cart', self.wt], check=True)
        space = {'herdr.space': 'shopteam'}
        self.terminals = [
            {'address': 't1', 'name': 'lead', 'parent': 't0', 'cwd': self.project, 'labels': space,
             'argv': ['/usr/bin/env', 'PATH=/x', '/opt/bin/claude', '--model', 'opus', '--permission-mode', 'plan',
                      'Split the cart work.']},
            {'address': 't2', 'name': 'builder', 'parent': 't1', 'cwd': self.wt, 'labels': space,
             'argv': ['codex', '-m', 'gpt-5', '-s', 'workspace-write', 'Build the cart.']},
            {'address': 't3', 'name': 'helper', 'parent': 't1', 'cwd': self.project, 'labels': space,
             'argv': ['gemini'], 'agent': 'gemini'},
            {'address': 't4', 'name': None, 'parent': None, 'cwd': self.project, 'labels': {}, 'argv': ['zsh']},
        ]

    def test_find_groups_by_space_and_skips_shells(self):
        teams = herdr.find(terminals=self.terminals, cfg=self.d)['teams']
        self.assertEqual(teams, [{'id': 'running:shopteam', 'label': 'shopteam', 'where': 'Running in herdr',
                                  'agents': 3, 'running': True}])

    def test_draft_maps_roles_parent_project_and_branch(self):
        d = herdr.draft('running:shopteam', terminals=self.terminals)
        roles = by_name(d)
        self.assertEqual(d['project'], self.project)
        self.assertEqual(sorted(roles), ['builder', 'lead'])
        self.assertEqual(roles['lead']['checkout'], 'read-only')
        self.assertEqual(roles['builder']['parent'], 'lead')
        self.assertIsNone(roles['lead']['parent'])  # its parent is the shell it was started from
        self.assertEqual((roles['builder']['agent'], roles['builder']['model']), ('codex', 'gpt-5'))
        self.assertIn('gemini', notes_for(d, 'helper')[0])
        self.assertIn('feature/cart', ' '.join(notes_for(d, 'builder')))
        self.assertEqual(d['running'], 'shopteam')
        self.assertEqual(d['task'], 'Split the cart work.')
        newteam.validate(d['roles'])

    def test_hc_details_are_read_at_once_with_the_persons_hc(self):
        import threading, time
        seen, lock = [], threading.Lock()

        def hc(args):
            if args[0] == 'ls':
                return {'terminals': [{'address': f't{i}', 'state': 'live'} for i in range(6)]}
            with lock:
                seen.append(threading.get_ident())
            time.sleep(0.2)
            return {'address': args[1], 'argv': ['claude']}
        start = time.monotonic()
        with mock.patch.object(herdr, '_hc', side_effect=hc), mock.patch.object(herdr, '_core_up', return_value=True):
            got, problem = herdr._hc_terminals()
        self.assertEqual((len(got), problem), (6, None))
        self.assertLess(time.monotonic() - start, 0.8, 'hc get runs for each terminal at the same time')

    def test_herdr_not_running_starts_nothing_and_unreadable_says_so(self):
        with mock.patch.object(herdr, '_core_up', return_value=False), mock.patch.object(herdr, '_hc') as hc:
            self.assertEqual(herdr._hc_terminals(), ([], None))
        hc.assert_not_called()  # hc would start a herdr core of its own
        with mock.patch.object(herdr, '_core_up', return_value=True), \
                mock.patch.object(herdr, '_hc', side_effect=OSError('hc broke')):
            got, problem = herdr._hc_terminals()
        self.assertEqual(got, [])
        self.assertIn("Couldn't read what is running in herdr", problem)
        with mock.patch.object(herdr, '_hc_terminals', return_value=([], 'Couldn\'t read what is running in herdr.')):
            self.assertIn("Couldn't read", herdr.find(cfg=tempfile.mkdtemp())['unreadable'])

    def test_herdrs_own_hc_comes_first(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        for p in ('herdr', 'hc'):
            with open(os.path.join(d, p), 'w') as f:
                f.write('#!/bin/sh\n')
            os.chmod(os.path.join(d, p), 0o755)
        with mock.patch.object(herdr.setup, 'agent_path', side_effect=lambda n: os.path.join(d, n) if n == 'herdr' else '/bundle/hc'):
            self.assertEqual(herdr._hc_path(), os.path.join(d, 'hc'))

    def test_task_only_from_an_agent_known_to_report_to_no_one(self):
        d = herdr._team([('lead', 'claude', ['Plan it.'], {}, '', None), ('two', 'claude', ['Build it.'], {}, '', None)])
        self.assertEqual(d['task'], '')
        self.assertTrue(any('task' in n['text'] for n in d['notes']))
        self.assertIn('Plan it.', by_name(d)['lead']['brief'])
        d = herdr._team([('helper', 'claude', ['Help.'], {}, '', None), ('lead', 'claude', ['Plan it.'], {}, '', '')])
        self.assertEqual(d['task'], 'Plan it.')

    def test_a_role_new_team_would_refuse_is_left_out_with_a_note(self):
        real = newteam.validate

        def refuse_two(roles):
            if any(r['name'] == 'two' for r in roles) and len(roles) == 1:
                raise newteam.NewTeamError('role:0', 'Every agent needs a brief.')
            return real(roles)
        with mock.patch.object(newteam, 'validate', side_effect=refuse_two):
            d = herdr._team([('one', 'claude', ['Go.'], {}, '', ''), ('two', 'claude', ['Go.'], {}, '', 'one')])
        self.assertEqual([r['name'] for r in d['roles']], ['one'])
        self.assertIn('two (Every agent needs a brief.)', notes_for(d, 'two')[0])

    def test_gone_team(self):
        with self.assertRaisesRegex(ValueError, 'running'):
            herdr.draft('running:nope', terminals=self.terminals)


class SavedTest(unittest.TestCase):
    def setUp(self):
        self.cfg = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.cfg)
        pane = lambda name, kind: {'cwd': '/tmp/made-up-docs', 'agent_name': name, 'managed_agent_kind': kind,
                                   'agent_session': {'source': f'herdr:{kind}', 'agent': kind, 'kind': 'id', 'value': 'x'}}
        session = {'version': 3, 'workspaces': [
            {'id': 'w1', 'custom_name': None, 'identity_cwd': '/tmp/made-up-docs', 'tabs': [
                {'panes': {'1': pane('writer', 'claude'), '2': pane('Checker', 'codex'), '3': {'cwd': '/tmp'},
                           '4': pane('', 'opencode')}}]},
            {'id': 'w2', 'identity_cwd': '/tmp', 'tabs': [{'panes': {'1': {'cwd': '/tmp'}}}]}]}
        os.makedirs(os.path.join(self.cfg, 'sessions', 'night'))
        for path in ('session.json', 'sessions/night/session.json'):
            with open(os.path.join(self.cfg, path), 'w') as f:
                json.dump(session, f)
        with open(os.path.join(self.cfg, 'sessions', 'broken.json'), 'w') as f:
            f.write('{')

    def test_find_lists_workspaces_with_agents(self):
        teams = herdr.find(terminals=[], cfg=self.cfg)['teams']
        self.assertEqual([(t['id'], t['label'], t['agents']) for t in teams],
                         [('saved:default:w1', 'made-up-docs', 3), ('saved:night:w1', 'made-up-docs', 3)])
        self.assertEqual(teams[1]['where'], 'Saved in herdr, session night')

    def test_draft_says_what_herdr_does_not_save(self):
        d = herdr.draft('saved:night:w1', terminals=[], cfg=self.cfg)
        self.assertEqual(sorted(by_name(d)), ['checker', 'writer'])
        self.assertIn("doesn't save models", d['notes'][0]['text'])
        self.assertIn('opencode', notes_for(d, 'opencode')[0])
        self.assertEqual(d['task'], '')
        self.assertEqual(d['project'], '/tmp/made-up-docs')
        newteam.validate(d['roles'])


SCRIPT = r'''#!/usr/bin/env bash
# Made-up team: a lead on Claude Code and a Codex builder, each in its own folder.
set -euo pipefail
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
PARENT=$(dirname "$REPO")
SUFFIX=${TEAM_SUFFIX:-}
LEAD=lead$SUFFIX
BUILD_DIR=$PARENT/shop-builder$SUFFIX
OPUS=claude-opus-5-5
CODEX_MODEL=gpt-5

start_lead() {  # name model [first message]
  local claude parent=()
  claude=$(command -v claude) || exit 1
  hc run --bg --json --name "$1" "${parent[@]}" --cwd "$REPO" --label herdr.space=shop -- \
    env PATH="$PATH" "$claude" --model "$2" --permission-mode auto \
    --append-system-prompt-file "$REPO/build/rules.md" ${3:+"$3"}
}

start_builder() {  # parent first-message
  local codex model=$CODEX_MODEL
  codex=$(command -v codex)
  out=$(hc run --bg --json --name builder --parent "$1" --cwd "$BUILD_DIR" -- \
    env PATH="$PATH" TOKEN_X=1 "$codex" -m "$model" -c model_reasoning_effort="\"high\"" -s workspace-write "$2") ||
    exit 1
}

start_checker() {  # name brief
  out=$(hc run --bg --json --name "$1" --cwd "$REPO" -- \
    env PATH="$PATH" CODEX="$(command -v codex)" BRIEF="$2" sh -c '
      rm -f /tmp/made-up-review.md
      [ "$(git -C "$REPO" rev-parse HEAD)" = x ] || true
      "$CODEX" exec -s read-only -o /tmp/made-up-review.md "$BRIEF" </dev/null
      hc send lead "review done"') || exit 1
}

case ${1:-} in
  start)
    prompt="Plan the cart."
    lead=$(start_lead "$LEAD" "$OPUS" "$prompt")
    start_builder "$lead" "Build the cart."
    start_checker checker "Check the cart diff."
    hc run --bg --name web -- npm run dev
    ;;
  review)
    herdr agent start reviewer --kind claude --pane 3 -- --model sonnet --permission-mode plan "Review it."
    herdr agent start "$who" --kind gemini --pane 4
    ;;
esac
'''


class ScriptTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.repo = os.path.realpath(git_repo(os.path.join(self.d, 'shop')))
        os.makedirs(os.path.join(self.repo, 'build'))
        with open(os.path.join(self.repo, 'build', 'rules.md'), 'w') as f:
            f.write('House rules.')
        self.script = os.path.join(self.repo, 'build', 'team.sh')
        with open(self.script, 'w') as f:
            f.write(SCRIPT)

    def test_script_agents_through_helper_functions(self):
        d = herdr.draft(f'script:{self.script}')
        roles = by_name(d)
        self.assertEqual(sorted(roles), ['builder', 'checker', 'lead', 'reviewer'])
        checker = roles['checker']
        self.assertEqual((checker['agent'], checker['checkout']), ('codex', 'read-only'))
        self.assertTrue(any('report to no one' in n for n in notes_for(d, 'lead')))  # --parent may hide in "${parent[@]}"
        lead, builder, reviewer = roles['lead'], roles['builder'], roles['reviewer']
        self.assertEqual((lead['agent'], lead['model']), ('claude', 'claude-opus-5-5'))
        self.assertEqual(lead['brief'], 'House rules.\n\nPlan the cart.')
        self.assertTrue(any('permission mode "auto"' in n for n in notes_for(d, 'lead')))
        # The lead's parent may hide in "${parent[@]}", so the task is the first message of the first agent known
        # to report to no one.
        self.assertEqual(d['task'], 'Check the cart diff.')
        self.assertEqual((builder['agent'], builder['model'], builder['effort']), ('codex', 'gpt-5', 'high'))
        self.assertEqual(builder['brief'], 'Build the cart.')
        self.assertTrue(any('TOKEN_X' in n for n in notes_for(d, 'builder')))
        self.assertTrue(any('report to no one' in n for n in notes_for(d, 'builder')))  # its parent is only known at run time
        self.assertEqual((reviewer['model'], reviewer['checkout']), ('sonnet', 'read-only'))
        self.assertTrue(any('gemini' in n['text'] for n in d['notes']))
        self.assertFalse(any('npm' in str(n) for n in d['notes']))  # a dev server isn't an agent
        self.assertEqual(d['project'], self.repo)
        self.assertTrue(any('2 folders' in n['text'] for n in d['notes']))
        texts = [n['text'] for n in d['notes']]
        self.assertEqual(len(texts), len(set(texts)), 'one sentence per kind')  # the builder's folder is a sibling
        newteam.validate(d['roles'])

    def test_unknown_names_become_numbered(self):
        with open(self.script, 'w') as f:
            f.write('hc run --name "$NAME" -- claude "Go."\n')
        d = herdr.draft(f'script:{self.script}')
        self.assertEqual([r['name'] for r in d['roles']], ['agent-1'])
        self.assertEqual(d['project'], self.repo)  # no folder given: the script's own project

    def test_missing_script(self):
        with self.assertRaisesRegex(ValueError, "couldn't be read"):
            herdr.draft(f'script:{self.d}/nope.sh')

    def test_import_writes_a_draft_the_app_opens_and_starts_nothing(self):
        from unittest import mock
        from assieme import mcptools
        state = os.path.join(self.d, 'state')
        with mock.patch.dict(os.environ, {'XDG_STATE_HOME': state}):
            t = mcptools.Tools(None, None, opener=lambda: self.fail('an import never opens or starts anything'))
            draft = t.herdr_import(f'script:{self.script}')
            self.assertIsNone(t.draft_next(), 'the app opens it directly: no draft file is left behind')
        self.assertEqual(draft['from'], 'herdr')
        self.assertEqual(draft['project'], self.repo)
        self.assertEqual(sorted(r['name'] for r in draft['roles']), ['builder', 'checker', 'lead', 'reviewer'])
        self.assertTrue(draft['notes'])
        self.assertEqual(draft['task'], 'Check the cart diff.')
        with open(self.script, 'w') as f:
            f.write('hc run -- npm run dev\n')
        with mock.patch.dict(os.environ, {'XDG_STATE_HOME': state}), self.assertRaisesRegex(ValueError, 'No Claude'):
            mcptools.Tools(None, None).herdr_import(f'script:{self.script}')

    def test_unknown_source(self):
        with self.assertRaises(ValueError):
            herdr.draft('other:x')


if __name__ == '__main__':
    unittest.main()
