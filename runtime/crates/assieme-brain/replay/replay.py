"""Replay one recording to the Python brain and the Rust brain, then compare what they did.

    replay.py RECORDING.tar [--asm PATH] [--python PATH] [--wait SECONDS]

Each brain runs in a scratch HOME and XDG_STATE_HOME with the recording's logs, a pinned clock (ASSIEME_NOW),
`ps` and `lsof` answered from the recording, `gh` and the notifiers stubbed and logged, outbound network refused
(sandbox-exec) and a fake core that answers from the recording and logs every call. Compared: the brain's replies,
the fake core's log, the stub log and the scratch tree (every file's hash, so git repositories left behind too).
Generated ids and times are masked. Prints the differences, or "same".
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
NET = '(version 1)(allow default)(deny network-outbound (remote ip))'  # unix sockets stay open
STUBS = {'ps': 'cat "$REC/ps.txt"', 'lsof': 'cat "$REC/lsof.txt"',
         **{n: f'echo "{n} $*" >> "$LOG"; exit 1' for n in ('gh', 'osascript', 'notify-send', 'open', 'asm')}}
MASKS = [(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', '<uuid>'),
         (r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)?', '<time>'), (r'\b1[67]\d{11}\b', '<ms>')]


def fake_core(sock, answers, log):
    server = socket.socket(socket.AF_UNIX)
    server.bind(sock)
    server.listen()

    def one(conn):
        with conn, conn.makefile('rwb') as f:
            for line in f:
                req = json.loads(line)
                log.append([req['method'], req.get('params')])
                key = json.dumps([req['method'], req.get('params')])
                reply = ({'result': answers[key]} if key in answers
                         else {'error': {'code': -32000, 'message': 'not in the recording'}})
                f.write((json.dumps({'jsonrpc': '2.0', 'id': req.get('id'), **reply}) + '\n').encode())
                f.flush()

    def loop():
        while True:
            threading.Thread(target=one, args=(server.accept()[0],), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()


def rpc(sock, method, params=None):
    s = socket.socket(socket.AF_UNIX)
    s.connect(sock)
    with s, s.makefile('rwb') as f:
        f.write((json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}) + '\n').encode())
        f.flush()
        return json.loads(f.readline())


def mask(text, scratch):
    text = text.replace(scratch, '<scratch>')
    for pat, to in MASKS:
        text = re.sub(pat, to, text)
    return text


def run(name, command, recording, wait):
    with tempfile.TemporaryDirectory(dir='/var/tmp') as scratch:
        scratch = os.path.realpath(scratch)
        rec, home, state, bins = (f'{scratch}/{d}' for d in ('rec', 'home', 'state', 'bin'))
        os.makedirs(rec), os.makedirs(bins), os.makedirs(f'{home}/.codex'), os.makedirs(f'{state}/assieme')
        with tarfile.open(recording) as t:
            t.extractall(rec, filter='fully_trusted')  # keeps file times
        os.makedirs(f'{home}/.claude')
        os.rename(f'{rec}/claude', f'{home}/.claude/projects')
        os.rename(f'{rec}/codex', f'{home}/.codex/sessions')
        for f in os.listdir(f'{rec}/state'):
            os.rename(f'{rec}/state/{f}', f'{state}/assieme/{f}')
        log = f'{scratch}/stubs.log'
        for n, body in STUBS.items():
            with open(f'{bins}/{n}', 'w') as f:
                f.write(f'#!/bin/sh\n{body}\n')
            os.chmod(f'{bins}/{n}', 0o755)
        meta = json.load(open(f'{rec}/meta.json'))
        calls = []
        sock = f'{state}/assieme/asm.sock'
        fake_core(sock, json.load(open(f'{rec}/core.json')), calls)
        env = {'HOME': home, 'XDG_STATE_HOME': state, 'REC': rec, 'LOG': log, 'ASSIEME_NOW': meta['now'],
               'PATH': f'{bins}:/usr/bin:/bin:{os.path.dirname(sys.executable)}', 'ASM_SOCKET': sock,
               'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': ROOT}
        brain = f'{state}/assieme/brain.sock'
        p = subprocess.Popen(['sandbox-exec', '-p', NET, *command(brain)], env=env, cwd=scratch,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(300):  # the socket file can exist before it accepts
            try:
                socket.socket(socket.AF_UNIX).connect(brain)
                break
            except OSError:
                time.sleep(0.1)
        replies = {m: rpc(brain, m) for m in ('brain.snapshot',)}
        time.sleep(wait)  # one 15 s round of notifications and quiet delivery
        p.terminate()
        out = p.communicate()[0].decode(errors='replace')
        tree = {}
        for r, _, files in os.walk(scratch):
            for f in files:
                path = os.path.join(r, f)
                rel = os.path.relpath(path, scratch)
                if not rel.startswith(('rec/', 'bin/', 'home/.claude/', 'home/.codex/sessions/')) and not os.path.islink(path) \
                        and not f.endswith(('.sock', '.port', '.pid', '.lock')) and os.path.exists(path):
                    tree[mask(rel, scratch)] = hashlib.sha1(mask(open(path, errors='replace').read(), scratch).encode()).hexdigest()[:10]
        stubs = open(log).read() if os.path.exists(log) else ''
        text = json.dumps({'replies': replies, 'core': calls, 'stubs': stubs.splitlines(), 'tree': tree}, indent=1, sort_keys=True)
        return mask(text, scratch).splitlines(), out


def main():
    a = argparse.ArgumentParser()
    a.add_argument('recording')
    a.add_argument('--asm', default=f'{ROOT}/runtime/target/debug/asm')
    a.add_argument('--python', default='python3')
    a.add_argument('--wait', type=float, default=20)
    a = a.parse_args()
    py, rs = (run(n, c, a.recording, a.wait) for n, c in (
        ('python', lambda s: [a.python, '-m', 'assieme', 'serve']),
        ('rust', lambda s: [a.asm, 'brain', '--socket', s])))
    diff = list(difflib.unified_diff(py[0], rs[0], 'python', 'rust', lineterm='', n=1))
    print('\n'.join(diff) or 'same')
    return 1 if diff else 0


if __name__ == '__main__':
    sys.exit(main())
