"""The runtime layer: talks to Assieme's own core (`asm`, the fork in runtime/) over its socket.

This is the runtime interface: what this module calls can change, not its callers. One JSON-RPC request and reply per line, one connection per call.
"""
import json
import os
import shutil
import socket
import subprocess

from . import quiet

# Variables that mark "running inside another agent session". An inherited one turns off agents' transcripts
# (CLAUDE_CODE_CHILD_SESSION) or confuses them; configuration and logins (CODEX_HOME, CLAUDE_CODE_USE_BEDROCK,
# tokens) are kept.
_MARKERS = {'CLAUDECODE', 'CLAUDE_CODE_CHILD_SESSION', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID',
            'CLAUDE_CODE_HOST_SESSION_ID', 'CLAUDE_CODE_SSE_PORT', 'CLAUDE_CODE_MESSAGING_SOCKET',
            'CLAUDE_CODE_MESSAGING_TOKEN', 'CLAUDE_CODE_SESSION_ATTENDED', 'CLAUDE_PID', 'CODEX_SANDBOX',
            'CODEX_SANDBOX_NETWORK_DISABLED', 'CODEX_THREAD_ID'}


class RuntimeError_(Exception):
    """An error from the core (or from starting it), with its error kind and a hint."""

    def __init__(self, kind, message, hint=''):
        super().__init__(message)
        self.kind, self.hint = kind, hint


def clean_env(env):
    """The environment without agent markers: an inherited Claude Code marker turns off agents' transcripts."""
    return {k: v for k, v in env.items() if k not in _MARKERS}


def socket_path():
    if os.environ.get('ASSIEME_ASM_SOCKET'):  # a brain on a remote machine, started inside that machine's core
        return os.environ['ASSIEME_ASM_SOCKET']
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'assieme', 'asm.sock')


class Runtime:
    def __init__(self, session='assieme', sock=None):
        self.session, self.sock = session, sock or socket_path()

    def ensure_core(self):
        """Start this session's core (clean environment) unless it is listening; a stale socket file is removed."""
        if self._connect() is not None:
            return
        if not shutil.which('asm'):
            raise RuntimeError_('asm_missing', "Assieme's runtime is not installed",
                                'reinstall Assieme, which carries it')
        if os.path.exists(self.sock):
            os.remove(self.sock)
        os.makedirs(os.path.dirname(self.sock), exist_ok=True)
        # Agents inherit the core's PATH, so their `asm send` goes through quiet send. A core started before this keeps
        # its old PATH until it restarts: its agents' notes are simply not held.
        shim = quiet.install()
        # The person's own herdr settings never reach agents: their `hc` keeps its default.
        env = {k: v for k, v in clean_env(os.environ).items() if not k.startswith('HC_')}
        env |= {'ASM_SESSION': self.session, 'ASM_SOCKET': self.sock, 'PATH': shim + os.pathsep + os.environ.get('PATH', '')}
        r = subprocess.run(['asm', 'server', 'start'], env=env, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError_('start_failed', f"Assieme's runtime did not start: {(r.stderr or r.stdout).strip()}")

    def _connect(self):
        s = socket.socket(socket.AF_UNIX)
        try:
            s.connect(self.sock)
            return s
        except OSError:
            s.close()
            return None

    def call(self, method, **params):
        s = self._connect()
        if s is None:
            self.ensure_core()
            s = self._connect()
            if s is None:
                raise RuntimeError_('unavailable', f"cannot reach Assieme's runtime at {self.sock}")
        with s, s.makefile('rwb') as f:
            f.write((json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}) + '\n').encode())
            f.flush()
            line = f.readline()
        if not line:
            raise RuntimeError_('unavailable', f"Assieme's runtime closed the connection during {method}")
        msg = json.loads(line)
        if 'error' in msg:
            e = msg['error']
            data = e.get('data') or {}
            raise RuntimeError_(data.get('kind', 'error'), e.get('message', ''), data.get('hint', ''))
        return msg['result']

    def start(self, argv, cwd, labels, name=None, parent=None):
        p = {'argv': argv, 'cwd': cwd, 'labels': labels, 'wait': False}
        if name:
            p['name'] = name
        if parent:
            p['parent'] = parent
            p['outlive_parent'] = True  # the parent only hears its news: merging or stopping it must not end its reports
        return self.call('terminal.run', **p)['address']

    def send(self, t, text, timeout_s=30):
        return self.call('terminal.send', terminal=t, text=text, timeout=timeout_s)

    def keys(self, t, keys):
        self.call('terminal.keys', terminal=t, keys=keys)

    def interrupt(self, t, timeout_s=3):
        """Press the program's own stop key (Esc for an agent). It ends the turn, never the program."""
        return self.call('terminal.interrupt', terminal=t, timeout=timeout_s)

    def label(self, t, labels):
        self.call('terminal.label', terminal=t, labels=labels)

    def read(self, t, lines=0):
        return self.call('terminal.read', terminal=t, lines=lines)['text']

    def get(self, t):
        return self.call('terminal.get', terminal=t)

    def wait(self, t, timeout_s):
        return self.call('terminal.wait', terminal=t, timeout=timeout_s)

    def kill(self, t):
        self.call('terminal.kill', terminal=t)

    def list(self, labels, all=False):
        """This machine's terminals. Another machine's (`gpu-box/t7`) belong to the brain there."""
        return [t for t in self.call('terminal.list', labels=labels, all=all)['terminals'] if '/' not in t['address']]
