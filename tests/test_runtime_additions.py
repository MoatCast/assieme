"""Step 16: what our runtime adds to hc, against the real binary on its own session and socket."""
import json
import os
import stat
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_runtime_side_by_side import AGENT, ASM, Side  # noqa: E402

pytestmark = pytest.mark.skipif(not ASM.exists(), reason='needs a built runtime')


@pytest.fixture
def core(request):
    s = Side('add', ASM, 'ASM_')
    s.env |= getattr(request, 'param', {})
    s.start()
    yield s
    s.stop()


def status(s, t):
    return s.call('terminal.get', terminal=t).get('status') or {}


def agent(s):
    t = s.address(s.run('sh', '-c', AGENT))
    s.state(t, 'idle')
    return t


class Events:
    """A plain subscriber: every event from now on."""

    def __init__(self, s):
        self.conn = s.connect()
        self.f = self.conn.makefile('rwb')
        self.f.write(b'{"jsonrpc":"2.0","id":1,"method":"events.subscribe","params":{}}\n')
        self.f.flush()
        self.f.readline()

    def delivery(self):
        while True:
            line = json.loads(self.f.readline())
            if line.get('method') == 'event' and line['params']['type'] == 'delivery':
                return line['params']['data']


# Pushed status

def test_a_pushed_status_is_merged_with_the_programs_own_and_says_where_it_came_from(core):
    t = agent(core)
    assert 'source' not in status(core, t), 'its own report has no source'
    r = core.call('terminal.status.set', terminal=t, state='blocked', kind='question', msg='from logs', source='logs')
    assert (r['status']['state'], r['status']['kind'], r['status']['source']) == ('blocked', 'question', 'logs')
    refused = core.call('terminal.send', terminal=t, text='x')
    assert refused['error'] == 'blocked', 'the core acts on a pushed status like its own'
    core.call('terminal.send', terminal=t, text='go', force=True)  # the program reports again: newest wins
    core.state(t, 'done')
    assert 'source' not in status(core, t)


def test_a_pushed_status_is_checked(core):
    t = agent(core)
    assert core.call('terminal.status.set', terminal=t, state='nonsense', source='x')['error'] == 'invalid_params'
    assert core.call('terminal.status.set', terminal=t, state='idle', kind='nope', source='x')['error'] == 'invalid_params'
    assert core.call('terminal.status.set', terminal=t, state='idle')['error'] == 'invalid_params', 'source is required'
    assert core.call('terminal.status.set', terminal='t999', state='idle', source='x')['error'] == 'not_found'


def test_a_pushed_status_ends_a_turn_and_a_wait(core):
    t = core.address(core.run('cat'))
    core.call('terminal.status.set', terminal=t, state='working', source='logs')
    core.call('terminal.status.set', terminal=t, state='done', source='logs')
    got = core.call('terminal.get', terminal=t)
    assert got['turn'] == 1 and got['reported'] is True
    assert core.call('terminal.wait', terminal=t)['ended'] is True


# Delivery control

def test_now_is_todays_send(core):
    t = agent(core)
    r = core.call('terminal.send', terminal=t, text='plain', deliver='now')
    assert 'queued' not in r and r['started'] is True
    core.says(t, 'got:plain')


def test_an_idle_agent_is_not_woken_and_notes_join_its_next_work_as_one_batch(core):
    t = agent(core)
    events = Events(core)
    a = core.call('terminal.send', terminal=t, text='note-a', deliver='next_turn')
    b = core.call('terminal.send', terminal=t, text='note-b', deliver='next_turn')
    assert (a['queued'], b['queued']) == (1, 2)
    time.sleep(1.5)
    assert 'note-a' not in core.read(t), 'held: an idle agent is not woken'
    assert status(core, t)['state'] == 'idle'
    assert len(core.call('terminal.get', terminal=t)['held']) == 2
    core.call('terminal.status.set', terminal=t, state='working', source='test')
    assert events.delivery() == {'result': 'confirmed', 'notes': [1, 2]}
    core.says(t, 'got:note-a')
    core.says(t, 'got:note-b')
    assert 'held' not in core.call('terminal.get', terminal=t)


@pytest.mark.parametrize('core', [{'ASM_HOLD_MS': '800'}], indirect=True)
def test_an_agent_that_stays_idle_gets_one_wake_after_the_hold(core):
    t = agent(core)
    events = Events(core)
    core.call('terminal.send', terminal=t, text='late-a', deliver='next_turn')
    core.call('terminal.send', terminal=t, text='late-b', deliver='next_turn')
    assert events.delivery() == {'result': 'confirmed', 'notes': [1, 2]}
    core.says(t, 'got:late-b')


@pytest.mark.parametrize('core', [{'ASM_HOLD_MS': '100'}], indirect=True)
def test_a_dialog_never_gets_a_note(core):
    t = agent(core)
    core.call('terminal.status.set', terminal=t, state='blocked', kind='permission', source='test')
    core.call('terminal.send', terminal=t, text='wait-for-me', deliver='next_turn')
    time.sleep(1.5)
    assert 'wait-for-me' not in core.read(t)
    core.call('terminal.status.set', terminal=t, state='working', source='test')
    core.says(t, 'wait-for-me')


@pytest.mark.parametrize('core', [{'ASM_HOLD_MS': '0', 'ASM_RETRY_MS': '100', 'ASM_STARTED_MS': '100'}], indirect=True)
def test_an_unconfirmed_batch_is_sent_again_then_given_up_with_an_event(core):
    t = core.address(core.run('cat'))  # says it is idle and never starts a turn
    core.call('terminal.status.set', terminal=t, state='idle', source='test')
    events = Events(core)
    core.call('terminal.send', terminal=t, text='lost-note', deliver='next_turn')
    assert events.delivery() == {'result': 'gave_up', 'notes': [1]}
    assert core.read(t).count('lost-note') >= 5, 'each try went in'
    assert 'held' not in core.call('terminal.get', terminal=t)


def test_held_notes_survive_a_planned_restart(core):
    t = agent(core)
    core.call('terminal.send', terminal=t, text='kept-note', deliver='next_turn')
    core.call('server.restart')
    core.until('new core', lambda: _alive(core))
    assert [h['text'] for h in core.call('terminal.get', terminal=t)['held']] == ['kept-note']
    core.call('terminal.status.set', terminal=t, state='working', source='test')
    core.says(t, 'got:kept-note')


def _alive(s):
    try:
        return 'error' not in s.call('server.status')
    except (OSError, ValueError):
        return False


def test_notes_for_a_terminal_that_ended_are_given_up_with_an_event(core):
    t = agent(core)
    events = Events(core)
    core.call('terminal.send', terminal=t, text='too-late', deliver='next_turn')
    core.call('terminal.kill', terminal=t)
    assert events.delivery() == {'result': 'gave_up', 'notes': [1]}


def test_next_turn_cannot_be_combined(core):
    t = agent(core)
    for extra in ({'wait': True}, {'force': True}, {'if': 'idle'}):
        r = core.call('terminal.send', terminal=t, text='x', deliver='next_turn', **extra)
        assert r['error'] == 'invalid_params', extra


# A6: permission by intent

@pytest.fixture
def launcher(core):
    """A program on PATH, with a link to it under another name."""
    bin_dir = core.dir / 'bin'
    bin_dir.mkdir()
    real = bin_dir / 'cloudrun'
    real.write_text('#!/bin/sh\necho LAUNCHED\n')
    real.chmod(real.stat().st_mode | stat.S_IEXEC)
    (bin_dir / 'nicelink').symlink_to(real)
    return real


def _denied_shell(core, launcher):
    env = {'PATH': f'{launcher.parent}:{os.environ["PATH"]}'}
    t = core.address(core.run('sh', '-c', 'echo up; exec cat', env=env, deny_programs=[launcher.name]))
    core.says(t, 'up')
    return t, env


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS only')
@pytest.mark.parametrize('how', [
    '{name}', '{path}', 'sh -c {name}', '{dir}/nicelink', 'python3 -c "import subprocess; subprocess.run([\\"{path}\\"])"',
    'env {name}', 'xargs {path} </dev/null', 'sh {path}', 'cp {path} copied; ./copied',
])
def test_a_denied_program_cannot_run_however_it_is_called(core, launcher, how):
    env = {'PATH': f'{launcher.parent}:{os.environ["PATH"]}'}
    line = how.format(name=launcher.name, path=launcher, dir=launcher.parent)
    r = core.call('terminal.run', line=line + '; echo finished', env=env, deny_programs=[launcher.name],
                  cwd=str(core.dir), wait=True)
    assert 'error' not in r, r
    assert 'LAUNCHED' not in r['output'] and 'finished' in r['output'], r['output']


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS only')
def test_naming_a_denied_program_is_fine_and_others_still_run(core, launcher):
    env = {'PATH': f'{launcher.parent}:{os.environ["PATH"]}'}
    line = f'echo {launcher.name}; command -v {launcher.name}; /usr/bin/true && echo others-ok'
    r = core.call('terminal.run', line=line, env=env, deny_programs=[launcher.name], cwd=str(core.dir), wait=True)
    assert str(launcher) in r['output'] and 'others-ok' in r['output'], r['output']


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS only')
def test_without_the_rule_it_runs_and_the_rule_does_not_leak(core, launcher):
    env = {'PATH': f'{launcher.parent}:{os.environ["PATH"]}'}
    r = core.call('terminal.run', line=launcher.name, env=env, cwd=str(core.dir), wait=True)
    assert 'LAUNCHED' in r['output']
    assert 'denied' not in r.get('terminal', r)


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS only')
def test_a_child_inherits_the_rule_when_the_core_starts_it(core, launcher):
    t, env = _denied_shell(core, launcher)
    r = core.call('terminal.run', line=f'{launcher.name}; echo finished', env=env, cwd=str(core.dir), caller=t, wait=True)
    assert 'LAUNCHED' not in r['output'] and 'finished' in r['output'], r['output']
    assert r.get('terminal', r)['denied'] == [launcher.name]


# Known limits, pinned so a change in them is noticed.

@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS only')
def test_limit_an_agent_applying_its_own_sandbox_fails_inside_a_denied_terminal(core, launcher):
    r = core.call('terminal.run', line="sandbox-exec -p '(version 1)(allow default)' sh -c 'echo INNER'; echo rc=$?",
                  deny_programs=[launcher.name], cwd=str(core.dir), wait=True)
    assert 'Operation not permitted' in r['output'] and 'rc=71' in r['output'] and 'INNER' not in r['output'], r['output']


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS only')
def test_limit_a_copy_made_beforehand_still_runs(core, launcher):
    copy = launcher.with_name('renamed')
    copy.write_bytes(launcher.read_bytes())
    copy.chmod(0o755)
    r = core.call('terminal.run', line=str(copy), deny_programs=[launcher.name], cwd=str(core.dir), wait=True)
    assert 'LAUNCHED' in r['output']


def test_a_terminal_that_reports_nothing_gets_its_note_at_once_marked_unconfirmed(core):
    t = core.address(core.run('cat'))
    events = Events(core)
    core.call('terminal.send', terminal=t, text='plain-note', deliver='next_turn')
    assert events.delivery() == {'result': 'delivered', 'notes': [1]}
    core.says(t, 'plain-note')


@pytest.mark.parametrize('bad', ['', 'a b', 'a"b', 'x)(allow process-exec*', 'a\nb', '..$'])
def test_a_program_name_cannot_change_the_rule(core, bad):
    r = core.call('terminal.run', argv=['true'], deny_programs=[bad], cwd=str(core.dir))
    assert r.get('error') == 'invalid_params', r
