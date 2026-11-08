"""Step 16: the same scripts against the real `hc` and our `asm`, each on its own session and socket.

Skipped without a built runtime (`cd runtime && ZIG=... cargo build`) or an installed hc. It never touches the live
core: every core here has its own HC_SESSION/ASM_SESSION, socket and state folder under a temporary directory.
ASSIEME_SBS_SELF=1 runs hc against hc, to check the scripts themselves are deterministic.
"""
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ASM = Path(os.environ.get('ASSIEME_CORE_BIN', ROOT / 'runtime/target/debug/assieme-core'))
HC = shutil.which('hc')
SELF = os.environ.get('ASSIEME_SBS_SELF') == '1'

pytestmark = pytest.mark.skipif(not HC or not (SELF or ASM.exists()), reason='needs an installed hc and a built runtime')

# Fields that differ between any two runs, whichever core it is.
VOLATILE = {'pid', 'created_ms', 'status_ms', 'uptime_ms', 'exited_ms', 'finished_ms', 'started_ms', 'seen_ms',
            'identity', 'version', 'seq', 'id', 'mtime_ms', 'hash', 'duration_ms', 'waited_ms', 'ms', 'last_output_ms',
            'output_ms', 'time_ms', 'elapsed_ms', 'scope', 'instance', 'program_started_ms', 'core'}
AGENT = r'''
printf '\033]7501;state=idle:app=fake\033\\'
while IFS= read -r line; do
  printf '\033]7501;state=working:app=fake\033\\'
  printf 'got:%s\n' "$line"
  case "$line" in *BLOCK*) printf '\033]7501;state=blocked:kind=permission:app=fake\033\\'; continue;; esac
  sleep 0.3
  printf '\033]7501;state=done:app=fake\033\\'
done
'''


class Side:
    def __init__(self, name, binary, prefix):
        self.name, self.binary, self.prefix = name, str(binary), prefix
        self.dir = Path(tempfile.mkdtemp(prefix='sbs', dir='/tmp'))  # short: socket paths have a low limit
        self.sock = str(self.dir / 'c.sock')
        (self.dir / 'state').mkdir()
        (self.dir / 'config').mkdir()
        (self.dir / 'work').mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(('HC_', 'ASM_'))}
        self.env = env | {f'{prefix}SESSION': f'sbs{os.getpid()}{name}', f'{prefix}SOCKET': self.sock,
                          f'{prefix}QUIET_MS': '200', 'XDG_STATE_HOME': str(self.dir / 'state'),
                          'XDG_CONFIG_HOME': str(self.dir / 'config')}

    def cli(self, *args, input=None, timeout=60):
        r = subprocess.run([self.binary, *args], env=self.env, capture_output=True, text=True, input=input,
                           timeout=timeout)
        return r.returncode, r.stdout, r.stderr

    def start(self):
        code, out, err = self.cli('server', 'start')
        assert code == 0, err or out

    def stop(self):
        self.cli('server', 'stop')
        shutil.rmtree(self.dir, ignore_errors=True)

    def connect(self):
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(60)
        s.connect(self.sock)
        return s

    def call(self, method, **params):
        """The result, or the error's kind: what a caller can rely on."""
        with self.connect() as s, s.makefile('rwb') as f:
            f.write(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode() + b'\n')
            f.flush()
            reply = json.loads(f.readline())
        if 'error' in reply:
            e = reply['error']
            return {'error': (e.get('data') or {}).get('kind', e.get('code')), 'message': e.get('message'),
                    'hint': (e.get('data') or {}).get('hint')}
        return reply['result']

    def run(self, *argv, **params):
        r = self.call('terminal.run', argv=list(argv), cwd=str(self.dir / 'work'), **params)
        assert 'error' not in r, r
        return r

    def address(self, r):
        return (r.get('terminal') or r)['address']

    def read(self, t):
        return self.call('terminal.read', terminal=t)['text']

    def until(self, what, ok, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            got = ok()
            if got:
                return got
            time.sleep(0.05)
        raise AssertionError(f'{self.name}: timed out waiting for {what}')

    def says(self, t, text):
        return self.until(f'{text!r} in {t}', lambda: text in self.read(t))

    def state(self, t, word):
        def is_it():
            status = self.call('terminal.get', terminal=t).get('status') or {}
            return status.get('state') == word
        return self.until(f'{t} {word}', is_it)


def normal(value, side):
    """The two sides' names made equal, and what differs between any two runs removed."""
    if isinstance(value, dict):
        return {k: ('<volatile>' if k in VOLATILE and v is not None else normal(v, side)) for k, v in value.items()
                if k != 'foreground'}  # sampled from the holder: present only when it answered in time
    if isinstance(value, (list, tuple)):
        return [normal(v, side) for v in value]
    if isinstance(value, str):
        value = value.replace(str(side.dir), '<dir>').replace(side.binary, '<bin>')
        value = value.replace(f'sbs{os.getpid()}{side.name}', '<session>').replace('/private<dir>', '<dir>')
        value = re.sub(r'\bHC_', 'ASM_', value)
        value = re.sub(r'\bhc\b', 'asm', value)
        value = value.replace('herdr-core', 'assieme-core').replace('Herdr Core', 'Assieme Core')
        return re.sub(r'[ \t]+$', '', value, flags=re.M)
    return value


def differences(a, b, at=''):
    """Each differing leaf with its path: readable, where a whole-record compare is not."""
    if isinstance(a, dict) and isinstance(b, dict):
        return [d for k in sorted(set(a) | set(b)) for d in differences(a.get(k, '<absent>'), b.get(k, '<absent>'), f'{at}.{k}')]
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in differences(x, y, f'{at}[{i}]')]
    return [] if a == b else [f'{at}: hc={a!r} asm={b!r}']


# The scripts. Each drives one side and returns what a caller would see.

def lifecycle(s):
    t = s.address(s.run('sh', '-c', 'echo ready; exec sleep 60', name='sleeper', labels={'role': 'a'}))
    s.says(t, 'ready')
    live = s.call('terminal.get', terminal=t)
    listed = s.call('terminal.list', labels={'role': 'a'})
    labeled = s.call('terminal.label', terminal=t, labels={'role': 'b'})
    refused = s.call('terminal.rm', terminal=t)
    killed = s.call('terminal.kill', terminal=t)
    s.until('finished', lambda: s.call('terminal.get', terminal=t).get('state') != 'live')
    gone = s.call('terminal.get', terminal=t)
    removed = s.call('terminal.rm', terminal=t)
    return dict(live=live, listed=listed, labeled=labeled, refused=refused, killed=killed, gone=gone, removed=removed,
                after=s.call('terminal.list'), missing=s.call('terminal.get', terminal=t))


def waited_run(s):
    return s.run('sh', '-c', 'echo out; echo err >&2; exit 3', wait=True)


def send_and_keys(s):
    t = s.address(s.run('cat'))
    sent = s.call('terminal.send', terminal=t, text='héllo wörld ✓')
    s.says(t, 'héllo wörld ✓')
    long = 'x' * 900  # a terminal's line buffer holds 1024
    s.call('terminal.send', terminal=t, text=long)
    s.until('long paste', lambda: s.read(t).replace('\n', '').count(long) >= 1)
    keys = s.call('terminal.keys', terminal=t, keys=['ctrl+d'])
    s.until('eof', lambda: s.call('terminal.get', terminal=t).get('exit_code') == 0)
    return dict(sent=sent, keys=keys, end=s.call('terminal.get', terminal=t), text=s.read(t))


def status_and_turns(s):
    t = s.address(s.run('sh', '-c', AGENT))
    s.state(t, 'idle')
    idle = s.call('terminal.get', terminal=t)
    mismatch = s.call('terminal.send', terminal=t, text='no', **{'if': 'working'})
    sent = s.call('terminal.send', terminal=t, text='first', wait=True)
    after = s.call('terminal.get', terminal=t)
    waited = s.call('terminal.wait', terminal=t)
    return dict(idle=idle, mismatch=mismatch, sent=sent, after=after, waited=waited)


def blocked_dialog(s):
    t = s.address(s.run('sh', '-c', AGENT))
    s.state(t, 'idle')
    s.call('terminal.send', terminal=t, text='BLOCK')
    s.state(t, 'blocked')
    refused = s.call('terminal.send', terminal=t, text='answer')
    typed = s.read(t).count('answer')
    forced = s.call('terminal.send', terminal=t, text='answer', force=True)
    s.says(t, 'got:answer')
    return dict(refused=refused, typed=typed, forced=forced, status=s.call('terminal.get', terminal=t)['status'])


def parent_and_child(s):
    p = s.address(s.run('sh', '-c', AGENT, name='boss'))
    s.state(p, 'idle')
    c = s.address(s.run('sh', '-c', AGENT, name='kid', caller=p))
    s.state(c, 'idle')
    record = s.call('terminal.get', terminal=c)
    s.call('terminal.send', terminal=c, text='hi kid', caller=p)
    s.says(c, 'got:<')
    s.state(p, 'idle')
    s.state(c, 'done')
    s.call('terminal.send', terminal=p, text='hi boss', caller=c)
    s.says(p, 'hi boss')
    s.state(p, 'done')
    s.call('terminal.kill', terminal=c)
    s.says(p, 'exited')
    return dict(record=record, kid=s.read(c), boss=s.read(p))


def events_order(s):
    with s.connect() as conn, conn.makefile('rwb') as f:
        f.write(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'events.subscribe', 'params': {}}).encode() + b'\n')
        f.flush()
        first = json.loads(f.readline())
        t = s.address(s.run('sh', '-c', AGENT))
        s.state(t, 'idle')
        s.call('terminal.send', terminal=t, text='go')
        s.state(t, 'done')
        s.call('terminal.kill', terminal=t)
        seen = []
        while not any(e['type'] in ('exit', 'finished', 'lost', 'exited') for e in seen):
            line = json.loads(f.readline())
            if line.get('method') == 'event':
                seen.append(line['params'])
    # Output and title events are timing: the facts and their order are what callers build on.
    facts = [e for e in seen if e['type'] not in ('output', 'title', 'cwd', 'printed')]
    return dict(first=first, types=[e['type'] for e in facts],
                status=[(e['data'].get('status') or {}).get('state') for e in facts if e['type'] == 'status'])


def error_shapes(s):
    bad_run = s.call('terminal.run', cwd='/definitely/not/here', argv=['true'])
    return dict(missing=s.call('terminal.get', terminal='t999'), bad_name=s.call('terminal.send', terminal='nobody', text='x'),
                no_method=s.call('terminal.nope'), no_field=s.call('terminal.send', text='x'),
                bad_key=s.call('terminal.keys', terminal='t999', keys=['nope']), bad_run=bad_run)


def framed_attach(s):
    """What the app's terminal view does: `[channel u32][kind u8][len u32][payload]`, channel 0 is JSON-RPC."""
    t = s.address(s.run('sh', '-c', 'echo before; exec cat'))
    s.says(t, 'before')
    with s.connect() as conn:
        f = conn.makefile('rb')
        conn.sendall(b'{"jsonrpc":"2.0","id":1,"method":"hello","params":{"framed":true}}\n')
        hello = json.loads(f.readline())
        body = json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'terminal.attach', 'params': {'terminal': t, 'cols': 80, 'rows': 24}}).encode()
        conn.sendall(struct.pack('<IBI', 0, 0, len(body)) + body)
        data, reply, marker_sent = b'', None, False
        end = time.monotonic() + 20
        while time.monotonic() < end and (reply is None or b'during' not in data):
            channel, kind, n = struct.unpack('<IBI', f.read(9))
            payload = f.read(n)
            if channel == 0:
                reply = json.loads(payload)
            else:
                data += payload
            if reply is not None and not marker_sent:
                s.call('terminal.send', terminal=t, text='during')
                marker_sent = True
    return dict(hello=hello, attach_ok='result' in reply, before=b'before' in data, during=b'during' in data)


def planned_restart(s):
    t = s.address(s.run('sh', '-c', 'echo kept; exec cat'))
    s.says(t, 'kept')
    pid = s.call('terminal.get', terminal=t)['pid']
    restart = s.call('server.restart')
    s.until('new core', lambda: _alive(s))
    s.call('terminal.send', terminal=t, text='still here')
    s.says(t, 'still here')
    after = s.call('terminal.get', terminal=t)
    return dict(restart=restart, same_program=after['pid'] == pid, record=after, text=s.read(t))


def _alive(s):
    try:
        return 'error' not in s.call('server.status')
    except (OSError, ValueError):  # the old core hung up mid-answer
        return False


def full_screen(s):
    """Reading a full-screen program's history, the hardest part of `read`."""
    fake = ROOT / 'runtime/crates/assieme-core/tests/fake_tui.py'
    t = s.address(s.run('python3', str(fake), '--lines', '200'))
    s.says(t, 'status wheels')
    return dict(text=s.read(t), record=s.call('terminal.get', terminal=t))


def no_interleaving(s):
    t = s.address(s.run('cat'))
    lines = [f'line-{i}-' + 'y' * 400 for i in range(6)]
    threads = [threading.Thread(target=s.call, args=('terminal.send',), kwargs=dict(terminal=t, text=x)) for x in lines]
    [th.start() for th in threads]
    [th.join() for th in threads]
    s.until('all lines', lambda: all(x in s.read(t).replace('\n', '') for x in lines))
    return dict(all_whole=True)


def interrupt_shell(s):
    t = s.address(s.run('sh', '-c', 'echo up; sleep 60; echo stopped; exec sleep 60'))
    s.says(t, 'up')
    return s.call('terminal.interrupt', terminal=t, timeout=5)


def files_and_docs(s):
    p = str(s.dir / 'work/f.txt')
    return dict(write=s.call('file.write', path=p, data='one\n'), read=s.call('file.read', path=p),
                edit=s.call('file.edit', path=p, edits=[{'old': 'one', 'new': 'two'}]), again=s.call('file.read', path=p),
                missing=s.call('file.read', path=p + '.nope'),
                doc0=s.call('doc.get', name='d'), doc1=s.call('doc.set', name='d', value={'a': 1}, version=0),
                doc2=s.call('doc.get', name='d'), machines=s.call('machine.list'))


def template_first_message(s):
    d = s.dir / 'config' / ('herdr-core' if s.prefix == 'HC_' else 'assieme-core') / 'templates'
    d.mkdir(parents=True)
    (d / 'helper.md').write_text(f'---\ncommand: sh {s.dir}/agent.sh\n---\nYour job: $TASK\n')
    (s.dir / 'agent.sh').write_text(AGENT)
    listed = s.call('templates.list')
    r = s.call('terminal.run', argv=['helper', 'do it'], cwd=str(s.dir / 'work'))
    t = s.address(r)
    s.says(t, 'do it')
    return dict(listed=listed, run=r, record=s.call('terminal.get', terminal=t))


def cli_output(s):
    t = s.address(s.run('sh', '-c', 'echo via-cli; exec sleep 60', name='c'))
    s.says(t, 'via-cli')

    def rows(*a):
        code, out, err = s.cli(*a)
        try:
            return code, json.loads(out)
        except ValueError:
            return code, out

    return dict(ls=rows('ls', '--json')[0:2], read=rows('read', t)[0:2], get=rows('get', t, '--json')[0:2],
                missing=rows('get', 't999', '--json')[0], wait=rows('wait', t, '--timeout', '1', '--json')[0:2],
                run=rows('run', 'sh', '-c', 'echo done; exit 4')[0:2], exec=rows('exec', 'sh', '-c', 'echo e; exit 5')[0:2],
                usage=rows('nonsense')[0], templates=rows('templates', '--json')[0:2])


SCRIPTS = [lifecycle, waited_run, send_and_keys, status_and_turns, blocked_dialog, parent_and_child, events_order,
           error_shapes, framed_attach, planned_restart, full_screen, no_interleaving, interrupt_shell, files_and_docs,
           template_first_message, cli_output]


def sides():
    second = Side('hc2', HC, 'HC_') if SELF else Side('asm', ASM, 'ASM_')
    return Side('hc', HC, 'HC_'), second


@pytest.fixture
def both():
    a, b = sides()
    a.start()
    b.start()
    yield a, b
    a.stop()
    b.stop()


@pytest.mark.parametrize('script', SCRIPTS, ids=lambda f: f.__name__)
def test_same_script_same_result(both, script):
    a, b = both
    # One after the other: they share a machine, not a core, and nothing here is about timing between them.
    left, right = normal(script(a), a), normal(script(b), b)
    assert differences(left, right) == []


def _schema(side):
    doc = json.loads(side.cli('schema')[1])
    return {m['name']: m for m in doc['methods']}


# Everything the fork adds to the API; anything else in it fails here. (Empty on the self-check.)
ADDED_METHODS = {'terminal.status.set'}


def test_the_api_only_grows_by_what_we_added():
    a, b = sides()
    left, right = _schema(a), _schema(b)
    assert set(left) <= set(right)
    assert set(right) - set(left) <= ADDED_METHODS
    for name, method in left.items():
        theirs = {p['name'] for p in method.get('params', [])}
        ours = {p['name'] for p in right[name].get('params', [])}
        assert theirs <= ours, name
    a.stop(), b.stop()


def test_live_core_is_untouched():
    """The scripts above must leave the shared install alone."""
    live = Path(os.path.expanduser('~/.local/state/assieme/hc.sock'))
    a, b = sides()
    assert str(live) not in (a.sock, b.sock)
    a.stop(), b.stop()
