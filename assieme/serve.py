"""`assieme serve`: the brain as a background service.

Answers newline JSON-RPC on a Unix socket only this user can open, and runs one round of `assieme watch`
(notifications, quiet-send delivery) every 15 seconds. The app reads `brain.snapshot` through it.
"""
import json
import os
import shutil
import socketserver
import threading
import time
import traceback

from . import runtime, status, team

CLIS = {'claude': 'claude', 'codex': 'codex', 'hc': 'hc', 'herdr': 'herdr'}


def brain_socket_path():
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'assieme', 'brain.sock')


def snapshot(claude_dir, codex_dir, now, rt, which=shutil.which, hours=12):
    agents = status.collect(claude_dir, codex_dir, now, hours, herdr=status.herdr_terminals(),
                            running=status.running_cwds())
    head, alerts = status.summary(agents)
    try:
        teams = team.status(rt)
    except runtime.RuntimeError_:
        teams = []  # the assieme core isn't running: no Assieme teams yet
    return {'at': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'clis': {k: bool(which(v)) for k, v in CLIS.items()},
            'agents': [a.as_dict(now) | {'cwd': a.cwd} for a in agents],
            'alerts': alerts, 'queue': status.owner_queue(agents, now), 'stalls': status.stalls(agents, now),
            'teams': teams}


def handle(request, handlers):
    rid = request.get('id')
    fn = handlers.get(request.get('method'))
    if fn is None:
        return {'jsonrpc': '2.0', 'id': rid, 'error': {'code': -32601, 'message': f"no method {request.get('method')!r}"}}
    try:
        return {'jsonrpc': '2.0', 'id': rid, 'result': fn(**(request.get('params') or {}))}
    except Exception as e:  # a failing handler is a reply, never a dead service
        return {'jsonrpc': '2.0', 'id': rid, 'error': {'code': -32000, 'message': str(e)}}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            try:
                reply = handle(json.loads(line), self.server.handlers)
            except ValueError:
                reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'not JSON'}}
            self.wfile.write((json.dumps(reply) + '\n').encode())


def _ticker(tick, every_s):
    while True:
        try:
            tick()
        except Exception:
            traceback.print_exc()
        time.sleep(every_s)


def serve(sock, handlers, tick=None, every_s=15):
    os.makedirs(os.path.dirname(sock), exist_ok=True)
    if os.path.lexists(sock):
        os.remove(sock)
    old = os.umask(0o177)  # the socket is created 0600: only this user can talk to the brain
    try:
        server = socketserver.ThreadingUnixStreamServer(sock, _Handler)
    finally:
        os.umask(old)
    os.chmod(sock, 0o600)
    server.handlers = handlers
    server.daemon_threads = True
    if tick:
        threading.Thread(target=_ticker, args=(tick, every_s), daemon=True).start()
    server.serve_forever()
