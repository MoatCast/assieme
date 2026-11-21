"""Records `assieme/runtime.py` into tests/data/runtime.json for `src/runtime.rs`, against a fake core: a socket that
keeps each request line and answers from the case's script. Run from the repo root:
    .venv/bin/python runtime/crates/assieme-brain/record/runtime_cases.py

In the answers `<sock>` stands for the fake core's socket path."""
import json
import os
import socket
import sys
import tempfile
import threading
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
sys.path.insert(0, ROOT)
from assieme import runtime  # noqa: E402

OK = '{"jsonrpc": "2.0", "id": 1, "result": %s}'
T = {'terminals': [{'address': 't1', 'labels': {}}, {'address': 'gpu-box/t7'}, {'address': 't2'}]}
CASES = [
    (['call', 'terminal.get', {'terminal': 't1'}], [OK % '{"address": "t1", "status": {"state": "idle"}}']),
    (['call', 'server.status', {}], [OK % 'null']),
    (['call', 'x', {'a': [1, 'é'], 'b': None}], [OK % '1']),
    (['call', 'terminal.kill', {'terminal': 'nope'}],
     ['{"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "no terminal nope", "data": {"kind": "not_found", "hint": "see asm ls"}}}']),
    (['call', 'x', {}], ['{"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "no method"}}']),
    (['call', 'x', {}], ['{"jsonrpc": "2.0", "id": 1, "error": {"data": null}}']),
    (['call', 'x', {}], ['{"jsonrpc": "2.0", "id": 1, "error": {"message": "m", "data": {"kind": "k"}}}']),
    (['call', 'x', {}], [None]),
    (['call', 'x', {}], ['not json']),
    (['call', 'x', {}], ['{"jsonrpc": "2.0", "id": 1}']),
    (['call', 'x', {}], ['{"result": 5, "error": {"message": "both"}}']),
    (['start', ['claude', '--x'], '/p', {'assieme.team': 'a'}, None, None], [OK % '{"address": "t9"}']),
    (['start', ['codex'], '/p q', {}, 'name', 't1'], [OK % '{"address": "t10", "other": 1}']),
    (['start', ['codex'], '/p', {}, '', ''], [OK % '{"address": "t11"}']),
    (['send', 't1', 'hello\nworld', 30], [OK % '{"delivered": true}']),
    (['send', 't1', 'x', 2.5], [OK % '{}']),
    (['keys', 't1', ['enter', 'down']], [OK % '{}']),
    (['interrupt', 't1', 3], [OK % '{"ok": true}']),
    (['label', 't1', {'assieme.briefed': '1', 'x': None}], [OK % '{}']),
    (['read', 't1', 0], [OK % '{"text": "a\\nb"}']),
    (['read', 't1', 40], [OK % '{"text": ""}']),
    (['read', 't1', 0], [OK % '{}']),
    (['get', 't1'], [OK % '{"address": "t1"}']),
    (['wait', 't1', 5], [OK % '{"state": "idle"}']),
    (['kill', 't1'], [OK % 'null']),
    (['list', {'assieme.team': 'a'}, False], [OK % json.dumps(T)]),
    (['list', {}, True], [OK % '{"terminals": []}']),
]
ARGS = {'call': lambda rt, m, p: rt.call(m, **p), 'start': lambda rt, *a: rt.start(*a), 'send': lambda rt, *a: rt.send(*a),
        'keys': lambda rt, *a: rt.keys(*a), 'interrupt': lambda rt, *a: rt.interrupt(*a),
        'label': lambda rt, *a: rt.label(*a), 'read': lambda rt, *a: rt.read(*a), 'get': lambda rt, *a: rt.get(*a),
        'wait': lambda rt, *a: rt.wait(*a), 'kill': lambda rt, *a: rt.kill(*a), 'list': lambda rt, *a: rt.list(*a)}


def fake_core(sock, replies, seen):
    server = socket.socket(socket.AF_UNIX)
    server.bind(sock)
    server.listen()

    def serve():
        for reply in replies:
            conn, _ = server.accept()
            with conn, conn.makefile('rwb') as f:
                seen.append(json.loads(f.readline()))
                if reply is not None:
                    f.write((reply + '\n').encode())
        server.close()
    threading.Thread(target=serve, daemon=True).start()


def main():
    out = []
    for call, replies in CASES:
        with tempfile.TemporaryDirectory(dir='/var/tmp') as d:
            sock, seen = os.path.join(d, 'asm.sock'), []
            fake_core(sock, replies, seen)
            rt = runtime.Runtime('test', sock)
            try:
                got = {'ok': ARGS[call[0]](rt, *call[1:])}
            except runtime.RuntimeError_ as e:
                got = {'error': {'kind': e.kind, 'message': str(e).replace(sock, '<sock>'), 'hint': e.hint}}
            except Exception as e:  # not the core's error: Python's own (a bad reply)
                got = {'raised': type(e).__name__}
        out.append({'call': call, 'replies': replies, 'requests': seen, **got})
    # No core and no asm to start one.
    with tempfile.TemporaryDirectory(dir='/var/tmp') as d, mock.patch.dict(os.environ, {'PATH': ''}):
        try:
            runtime.Runtime('test', os.path.join(d, 'asm.sock')).call('x')
        except runtime.RuntimeError_ as e:
            out.append({'call': ['call', 'x', {}], 'replies': [], 'requests': [], 'no_core': True,
                        'error': {'kind': e.kind, 'message': str(e), 'hint': e.hint}})
    env = {'CLAUDECODE': '1', 'CODEX_THREAD_ID': 'x', 'CODEX_HOME': '/c', 'PATH': '/bin', 'HC_X': 'y', 'CLAUDE_PID': '2'}
    data = {'calls': out, 'clean_env': {'in': env, 'out': runtime.clean_env(env)}}
    with open(os.path.join(ROOT, 'runtime/crates/assieme-brain/tests/data/runtime.json'), 'w') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print(len(out), 'calls')


if __name__ == '__main__':
    main()
