"""The team tools' way in for agents in containers. A container on a Mac can't open the brain's Unix socket, so the
brain also listens on 127.0.0.1 for the eight calls an agent's tools make. Each container agent gets its own ticket,
which stands for its terminal: the call is made as that agent, whatever address it sends."""
import json
import os
import secrets
import socketserver
import threading

METHODS = {'mcp.teammates', 'mcp.message', 'mcp.report', 'mcp.brief', 'mcp.record_decision', 'mcp.tasks',
           'mcp.task_add', 'mcp.task_set'}
_lock = threading.Lock()


def _dir():
    return os.path.join(os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state'), 'assieme')


def _tickets_path():
    return os.path.join(_dir(), 'bridge-tickets.json')


def _load():
    try:
        with open(_tickets_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def issue():
    return secrets.token_urlsafe(24)


def bind(ticket, address):
    """From now on `ticket` speaks for the agent in terminal `address`."""
    with _lock:
        tickets = _load()
        tickets[ticket] = address
        os.makedirs(_dir(), exist_ok=True)
        tmp = _tickets_path() + '.tmp'
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
            json.dump(tickets, f)
        os.replace(tmp, _tickets_path())


def _drop(keep):
    with _lock:
        tickets = _load()
        left = {t: a for t, a in tickets.items() if keep(a)}
        if left != tickets:
            tmp = _tickets_path() + '.tmp'
            with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
                json.dump(left, f)
            os.replace(tmp, _tickets_path())


def prune(live):
    """Forget the tickets of terminals that are gone: hc may give their address to a new one."""
    _drop(lambda address: address in live)


def revoke(addresses):
    _drop(lambda address: address not in set(addresses))


def port():
    try:
        with open(os.path.join(_dir(), 'bridge.port')) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def handle(request, handlers):
    from . import serve  # serve starts the bridge
    address = _load().get(request.get('ticket')) if isinstance(request.get('ticket'), str) else None
    if not address or request.get('method') not in METHODS:
        return {'jsonrpc': '2.0', 'id': request.get('id'), 'error': {'code': -32001, 'message': 'Not allowed.'}}
    params = dict(request.get('params') or {}, address=address)
    return serve.handle({'id': request.get('id'), 'method': request['method'], 'params': params}, handlers)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            for line in self.rfile:
                try:
                    request = json.loads(line)
                    reply = handle(request if isinstance(request, dict) else {}, self.server.handlers)
                except ValueError:
                    reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'not JSON'}}
                self.wfile.write((json.dumps(reply) + '\n').encode())
        except (BrokenPipeError, ConnectionResetError):
            pass


def start(handlers, host='127.0.0.1'):
    """Listen on a free port in the background and write it down for `launch`. Returns the port."""
    server = socketserver.ThreadingTCPServer((host, 0), _Handler)
    server.daemon_threads = True
    server.handlers = handlers
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.makedirs(_dir(), exist_ok=True)
    with open(os.path.join(_dir(), 'bridge.port'), 'w') as f:
        f.write(str(server.server_address[1]))
    return server.server_address[1]
