"""A3 quiet send: routine notes between agents wait for the receiver's next turn instead of waking it.

How it fits together:
- The owner marks a sender -> receiver pair quiet (Settings or a saving tip in the app).
  Pairs live in quiet.json.
- Agents keep typing `asm send`. A small `asm` stand-in holds notes on quiet pairs in a queue and passes everything
  else to the real asm. Agents started in Assieme get it from the core's PATH; for others, `assieme quiet on` prints the line.
- The brain's service tick delivers held notes as one batch while the receiver is busy (so the note
  joins its current turn), or after MAX_HOLD if it stays idle: one wake instead of many.

Safety: questions and URGENT/! notes always go now, `--now` forces it, and when no assieme loop is running
(no heartbeat for ALIVE_FOR seconds) the shim passes everything straight through. A failed delivery stays queued;
notes for an agent that has gone are given up after GIVE_UP_AFTER, with a row in Activity.
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
GIVE_UP_AFTER = 24 * 3600  # notes for an agent that has gone wait this long, in case it comes back
ALIVE_FOR = 60            # a loop that touched the heartbeat this recently is running


def state_dir():
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'assieme')


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
    out = [p for p in _opted_out() if p != [from_cwd, to_cwd]]  # the person's last word on a pair is remembered
    _write_json(_path('no_auto_hold.json'), out + ([] if on else [[from_cwd, to_cwd]]))
    _write_json(_path('quiet.json'), keep + ([{'from_cwd': from_cwd, 'to_cwd': to_cwd, 'label': label}] if on else []))


def _opted_out():
    got = _read_json(_path('no_auto_hold.json'), [])
    return [p for p in got if isinstance(p, list) and len(p) == 2] if isinstance(got, list) else []


def opted_out(from_cwd, to_cwd):
    """True once a person un-held this pair: Assieme never holds it again by itself."""
    return [_real(from_cwd), _real(to_cwd)] in _opted_out()


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

def real_asm():
    """The first `asm` on PATH that is not our stand-in. Never an `hc`: that is the person's own herdr."""
    shim = os.path.realpath(shim_dir())
    for d in os.environ.get('PATH', '').split(os.pathsep):
        if d and os.path.realpath(d) != shim:
            p = shutil.which('asm', path=d)
            if p:
                return p
    return None


def shim_main(argv, run=subprocess.run, cwd=None, now=None):
    """`asm ...` as the agents see it. Returns the exit code. Only `asm send` on a quiet pair is ever held."""
    asm = real_asm()
    if not asm:
        sys.stderr.write('assieme: real asm not found on PATH\n')
        return 127
    passthrough = lambda args: run([asm] + args).returncode
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
        info = json.loads(run([asm, 'get', target, '--json'], capture_output=True, text=True, timeout=10).stdout or '{}')
    except (ValueError, OSError, subprocess.SubprocessError):
        info = {}
    sender_cwd = _real(cwd or os.getcwd())
    if not info.get('cwd') or not is_quiet(sender_cwd, info['cwd']):
        return passthrough(['send'] + args)
    _hold({'at': now or time.time(), 'from_cwd': sender_cwd, 'from': os.path.basename(sender_cwd),
           'to': target, 'to_cwd': _real(info['cwd']), 'text': text,
           'session': os.environ.get('ASM_SESSION'), 'socket': os.environ.get('ASM_SOCKET'),
           'role': (info.get('labels') or {}).get('assieme.role'), 'team': (info.get('labels') or {}).get('assieme.team')})
    sys.stdout.write(json.dumps({'held': True, 'for': target,
                                 'note': 'quiet send: delivered with its next turn, not waking it now'}) + '\n')
    return 0


# --- delivery ------------------------------------------------------------------------------------------------

def batch_text(notes):
    lines = [f"assieme: {len(notes)} quiet note{'s' if len(notes) > 1 else ''} held while you were busy:"]
    for n in notes:
        lines.append(f"- from {n['from']} at {time.strftime('%H:%M', time.localtime(n['at']))}: {n['text']}")
    return '\n'.join(lines)


def hold_note(to, to_cwd, text, session, socket, now=None, sender='assieme', from_cwd=''):
    """A note from Assieme itself or an agent's message tool, delivered the same way: with the agent's next turn."""
    _hold({'at': now or time.time(), 'from_cwd': from_cwd, 'from': sender, 'to': to, 'to_cwd': _real(to_cwd), 'text': text,
           'session': session, 'socket': socket})


def _env(note):
    """The core a note must go back through. A plain `asm send` would reach the default session."""
    return os.environ | {'ASM_SESSION': note['session'], 'ASM_SOCKET': note['socket']} if note.get('session') else None


def _still_there(asm, note, run):
    """Terminal addresses get reused: the note goes only if this address is still the agent it was held for."""
    try:
        r = run([asm, 'get', note['to'], '--json'], capture_output=True, text=True, timeout=10, env=_env(note))
        return r.returncode == 0 and _real(json.loads(r.stdout or '{}').get('cwd')) == note['to_cwd']
    except (ValueError, OSError, subprocess.SubprocessError):
        return False


def _moved_to(asm, note, run, others):
    """Where an agent restarted in the same folder now is: the one live terminal there that no other held note is for.
    None when there is none, or several (never guess which agent a note was for)."""
    try:
        r = run([asm, 'ls', '--json'], capture_output=True, text=True, timeout=10, env=_env(note))
        ts = json.loads(r.stdout or '{}').get('terminals') or [] if r.returncode == 0 else []
    except (ValueError, OSError, subprocess.SubprocessError, AttributeError):
        return None
    here = [t.get('address') for t in ts if t.get('state', 'live') == 'live' and _real(t.get('cwd')) == note['to_cwd']
            and t.get('address') not in others]
    return here[0] if len(here) == 1 else None


def deliver(agents, now=None, run=subprocess.run, on_batch=None, on_give_up=None):
    """Send held notes: into a busy receiver's current turn, or after MAX_HOLD anyway. Returns what was sent."""
    now = now or time.time()
    heartbeat()
    by_cwd = {_real(a.cwd): a for a in agents if getattr(a, 'cwd', None)}
    asm = real_asm()
    sent = []
    for to_cwd, all_notes in held().items():
        a = by_cwd.get(to_cwd)
        busy = a is not None and a.state in ('working', 'waiting on subagents')
        gone = a is not None and a.state == 'exited'
        batches = {}
        for n in all_notes:
            batches.setdefault((n.get('session'), n['to']), []).append(n)  # agents sharing a folder each get theirs
        for notes in batches.values():
            overdue = now - min(n['at'] for n in notes) >= MAX_HOLD
            if not asm or not (busy or overdue):
                continue
            to = notes[-1]['to']
            if not gone and not _still_there(asm, notes[-1], run):
                to = _moved_to(asm, notes[-1], run, {k[1] for k in batches} - {to})
            if gone or not to:
                if now - min(n['at'] for n in notes) >= GIVE_UP_AFTER:
                    _drop(notes)
                    if on_give_up:
                        on_give_up(notes)
                continue
            res = run([asm, 'send', to, batch_text(notes)], capture_output=True, text=True, timeout=60,
                      env=_env(notes[-1]))
            if res.returncode == 0:
                _drop(notes)
                why = 'busy' if busy else 'overdue'
                sent.append((to, len(notes), why))
                if on_batch:
                    on_batch(notes, why)
    return sent


def savings(pair, entries, edge, total_tokens):
    """Share of the team's tokens a quiet pair saved: wake-ups avoided (notes minus batches) x that edge's average."""
    if not edge or not edge.get('wakes') or not total_tokens:
        return 0
    counts = [e['from'][pair['from_cwd']] for e in entries
              if e.get('action') == 'deliver' and e.get('to_cwd') == pair['to_cwd'] and pair['from_cwd'] in (e.get('from') or {})]
    return max(0, sum(counts) - len(counts)) * edge['tokens'] / edge['wakes'] / total_tokens


# --- quiet on / off-------------------------------------------------------------------------------------------

BLOCKER = 'git-remote-assieme-push-blocked'


def shim_dir():
    return _path('bin')


def install():
    """Write the asm stand-in. Returns its folder, which goes first on the agents' PATH."""
    d = shim_dir()
    os.makedirs(d, exist_ok=True)
    try:
        os.remove(os.path.join(d, 'hc'))  # the stand-in from before the switch would shadow the person's own herdr
    except OSError:
        pass
    p = os.path.join(d, 'asm')
    with open(p, 'w') as f:
        f.write(f'#!/bin/sh\n# assieme quiet send: holds notes on quiet pairs, passes everything else to the real asm.\n'
                f'exec "{sys.executable}" -P -m assieme shim "$@"\n')  # -P: never import from the worktree
    os.chmod(p, 0o755)
    p = os.path.join(d, BLOCKER)  # git runs this for assieme-push-blocked:// remotes (see launch.BLOCK_PUSH)
    with open(p, 'w') as f:
        f.write('#!/bin/sh\necho "Assieme blocks pushes for this team. Ask the person to push, or turn the safety practice off." >&2\nexit 1\n')
    os.chmod(p, 0o755)
    return d


def uninstall():
    for name in ('asm', 'hc', BLOCKER):
        try:
            os.remove(os.path.join(shim_dir(), name))
        except OSError:
            pass
