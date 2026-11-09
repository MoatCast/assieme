"""The runtime layer: talks to an isolated herdr-core (`hc`) core over its socket.

This is the runtime interface: a fork of hc later replaces what this module calls, not
its callers. One JSON-RPC request and reply per line, one connection per call.
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
    """An error from hc (or from starting it), with hc's error kind and a hint."""

    def __init__(self, kind, message, hint=''):
        super().__init__(message)
        self.kind, self.hint = kind, hint


def clean_env(env):
    """The environment without agent markers: an inherited Claude Code marker turns off agents' transcripts."""
    return {k: v for k, v in env.items() if k not in _MARKERS}


def socket_path():
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'assieme', 'hc.sock')


class Runtime:
    def __init__(self, session='assieme', sock=None):
        self.session, self.sock = session, sock or socket_path()

    def ensure_core(self):
        """Start this session's core (clean environment) unless it is listening; a stale socket file is removed."""
        if self._connect() is not None:
            return
        if not shutil.which('hc'):
            raise RuntimeError_('hc_missing', 'hc is not installed',
                                'install herdr-core (hc) from https://github.com/herdrdev/herdr-core')
        if os.path.exists(self.sock):
            os.remove(self.sock)
        os.makedirs(os.path.dirname(self.sock), exist_ok=True)
        # Agents inherit the core's PATH, so their `hc send` goes through quiet send. A core started before this keeps
        # its old PATH until it restarts: its agents' notes are simply not held.
        shim = quiet.install()
        env = clean_env(os.environ) | {'HC_SESSION': self.session, 'HC_SOCKET': self.sock,
                                       'PATH': shim + os.pathsep + os.environ.get('PATH', '')}
        r = subprocess.run(['hc', 'server', 'start'], env=env, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError_('start_failed', f'hc server start failed: {(r.stderr or r.stdout).strip()}')

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
                raise RuntimeError_('unavailable', f'cannot reach the hc core at {self.sock}')
        with s, s.makefile('rwb') as f:
            f.write((json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}) + '\n').encode())
            f.flush()
            line = f.readline()
        if not line:
            raise RuntimeError_('unavailable', f'the hc core closed the connection during {method}')
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
        return self.call('terminal.list', labels=labels, all=all)['terminals']
