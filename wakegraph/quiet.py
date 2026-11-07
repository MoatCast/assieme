"""A3 quiet send: routine notes between agents wait for the receiver's next turn instead of waking it.

How it fits together:
- The owner marks a sender -> receiver pair quiet (one key in the live view). Pairs live in quiet.json.
- Agents keep typing `hc send`. A small `hc` shim, put ahead of the real `hc` on the agents' PATH by
  `wakegraph team on`, holds notes on quiet pairs in a queue and passes everything else to the real hc.
- The live view and `wakegraph watch` deliver held notes as one batch while the receiver is busy (so the note
  joins its current turn), or after MAX_HOLD if it stays idle: one wake instead of many.

Safety: questions and URGENT/! notes always go now, `--now` forces it, and when no wakegraph loop is running
(no heartbeat for ALIVE_FOR seconds) the shim passes everything straight through. Nothing is dropped: a failed
delivery stays queued.
"""
import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time

MAX_HOLD = 20 * 60        # deliver anyway after this, even if the receiver stays idle
ALIVE_FOR = 60            # a loop that touched the heartbeat this recently is running


def state_dir():
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'wakegraph')


def _path(*parts):
    return os.path.join(state_dir(), *parts)


def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, path)  # atomic: a shim reading at the same moment never sees half a file


def pairs():
    """Quiet pairs as [{'from_cwd', 'to_cwd', 'label'}]."""
    return _read_json(_path('quiet.json'), [])


def _real(path):
    """Folders are compared resolved: /var and /private/var, or a symlinked checkout, are the same place."""
    return os.path.realpath(path) if path else path


def set_pair(from_cwd, to_cwd, label, on=True):
    from_cwd, to_cwd = _real(from_cwd), _real(to_cwd)
    keep = [p for p in pairs() if (p['from_cwd'], p['to_cwd']) != (from_cwd, to_cwd)]
    _write_json(_path('quiet.json'), keep + ([{'from_cwd': from_cwd, 'to_cwd': to_cwd, 'label': label}] if on else []))


def is_quiet(from_cwd, to_cwd):
    """The sender may run from a subfolder of its agent's folder."""
    from_cwd, to_cwd = _real(from_cwd), _real(to_cwd)
    return any((from_cwd == p['from_cwd'] or from_cwd.startswith(p['from_cwd'] + os.sep)) and to_cwd == p['to_cwd']
               for p in pairs())


def urgent(text):
    t = text.strip()
    return t.endswith('?') or t.upper().startswith(('URGENT', '!'))


def heartbeat():
    os.makedirs(state_dir(), exist_ok=True)
    with open(_path('deliverer.alive'), 'w') as f:
        f.write(str(time.time()))


def deliverer_alive(now=None):
    try:
        return (now or time.time()) - os.path.getmtime(_path('deliverer.alive')) <= ALIVE_FOR
    except OSError:
        return False


def held():
    """{to_cwd: [note, ...]} of notes waiting for delivery."""
    out = {}
    for n in _read_json(_path('held.json'), []):
        out.setdefault(n['to_cwd'], []).append(n)
    return out


@contextlib.contextmanager
def _locked():
    """Shims in several agents and the deliverer all change held.json: one at a time, or a note could be lost."""
    os.makedirs(state_dir(), exist_ok=True)
    with open(_path('held.lock'), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _hold(note):
    with _locked():
        _write_json(_path('held.json'), _read_json(_path('held.json'), []) + [note])


def _drop(delivered):
    """Remove exactly the notes that were sent; one that arrived meanwhile stays for the next round."""
    with _locked():
        _write_json(_path('held.json'), [n for n in _read_json(_path('held.json'), []) if n not in delivered])


# --- the shim ------------------------------------------------------------------------------------------------

def real_hc():
    """The first `hc` on PATH that is not our shim."""
    shim = os.path.realpath(shim_dir())
    for d in os.environ.get('PATH', '').split(os.pathsep):
        if d and os.path.realpath(d) != shim:
            p = shutil.which('hc', path=d)
            if p:
                return p
    return None


def shim_main(argv, run=subprocess.run, cwd=None, now=None):
    """`hc ...` as the agents see it. Returns the exit code. Only `hc send` on a quiet pair is ever held."""
    hc = real_hc()
    if not hc:
        sys.stderr.write('wakegraph: real hc not found on PATH\n')
        return 127
    passthrough = lambda args: run([hc] + args).returncode
    if not argv or argv[0] != 'send':
        return passthrough(argv)
    args = list(argv[1:])
    if '--now' in args:
        args.remove('--now')
        return passthrough(['send'] + args)
    words = [a for a in args if not a.startswith('-')]
    flags_with_values = {'--if'}
    if any(a in flags_with_values for a in args) or len(words) < 2 or not deliverer_alive(now):
        return passthrough(['send'] + args)  # conditional sends, odd shapes, or nobody to deliver: go now
    target, text = words[0], ' '.join(words[1:])
    if urgent(text):
        return passthrough(['send'] + args)
    try:
        info = json.loads(run([hc, 'get', target, '--json'], capture_output=True, text=True, timeout=10).stdout or '{}')
    except (ValueError, OSError, subprocess.SubprocessError):
        info = {}
    sender_cwd = _real(cwd or os.getcwd())
    if not info.get('cwd') or not is_quiet(sender_cwd, info['cwd']):
        return passthrough(['send'] + args)
    _hold({'at': now or time.time(), 'from_cwd': sender_cwd, 'from': os.path.basename(sender_cwd),
           'to': target, 'to_cwd': _real(info['cwd']), 'text': text})
    sys.stdout.write(json.dumps({'held': True, 'for': target,
                                 'note': 'quiet send: delivered with its next turn, not waking it now'}) + '\n')
    return 0


# --- delivery ------------------------------------------------------------------------------------------------

def batch_text(notes):
    lines = [f"wakegraph: {len(notes)} quiet note{'s' if len(notes) > 1 else ''} held while you were busy:"]
    for n in notes:
        lines.append(f"- from {n['from']} at {time.strftime('%H:%M', time.localtime(n['at']))}: {n['text']}")
    return '\n'.join(lines)


def deliver(agents, now=None, run=subprocess.run):
    """Send held notes: into a busy receiver's current turn, or after MAX_HOLD anyway. Returns what was sent."""
    now = now or time.time()
    heartbeat()
    by_cwd = {_real(a.cwd): a for a in agents if getattr(a, 'cwd', None)}
    hc = real_hc()
    sent = []
    for to_cwd, notes in held().items():
        a = by_cwd.get(to_cwd)
        busy = a is not None and a.state in ('working', 'waiting on subagents')
        overdue = now - min(n['at'] for n in notes) >= MAX_HOLD
        gone = a is not None and a.state == 'exited'
        if not hc or not (busy or overdue) or gone:
            continue
        res = run([hc, 'send', notes[-1]['to'], batch_text(notes)], capture_output=True, text=True, timeout=60)
        if res.returncode == 0:
            _drop(notes)
            sent.append((notes[-1]['to'], len(notes), 'busy' if busy else 'overdue'))
    return sent


# --- team on / off -------------------------------------------------------------------------------------------

def shim_dir():
    return _path('bin')


def install():
    """Write the hc shim. Returns its folder, which goes first on the agents' PATH."""
    d = shim_dir()
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, 'hc')
    with open(p, 'w') as f:
        f.write(f'#!/bin/sh\n# wakegraph quiet send: holds notes on quiet pairs, passes everything else to the real hc.\n'
                f'exec "{sys.executable}" -m wakegraph shim "$@"\n')
    os.chmod(p, 0o755)
    return d


def uninstall():
    try:
        os.remove(os.path.join(shim_dir(), 'hc'))
    except OSError:
        pass
