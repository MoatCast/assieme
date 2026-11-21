"""Record real inputs from this Mac for the replay harness.

    record.py snapshot OUT.tar          agent logs, state folder, ps, lsof and the core's answers, once
    record.py watch SECONDS OUT.jsonl   every 5 s: each terminal's status, its time and its screen, plus the agent processes

Recordings stay on this Mac (default folder ~/.local/share/assieme-dev/replay), never in the repository.
BatchWise logs are never recorded: a folder or file path that names BatchWise is skipped, and a Codex log is skipped
when its first line does (that line is tested and discarded, never kept or printed).
"""
import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time

HOME = os.path.expanduser('~')
STATE = os.path.join(os.environ.get('XDG_STATE_HOME') or os.path.join(HOME, '.local/state'), 'assieme')
HOURS = 48      # logs newer than this
KEEP = 512_000  # a log over 4 MB keeps its first 64 KB and its last 512 KB (the readers only tail)
AGENTS = r'(^|/)(claude|codex|gemini|opencode|cursor-agent)(\s|$)'


def core(method, **params):
    s = socket.socket(socket.AF_UNIX)
    s.connect(os.path.join(STATE, 'asm.sock'))
    with s, s.makefile('rwb') as f:
        f.write((json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}) + '\n').encode())
        f.flush()
        return json.loads(f.readline()).get('result')


def codex_ok(path):
    with open(path, 'rb') as f:
        return b'batchwise' not in f.readline().lower()


def copy_logs(src, dst, ok):
    """Copy recent logs keeping their times; returns the number of files."""
    n, cutoff = 0, time.time() - HOURS * 3600
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if 'batchwise' not in d.lower()]
        for name in files:
            p = os.path.join(root, name)
            if not name.endswith('.jsonl') or 'batchwise' in p.lower() or os.path.getmtime(p) < cutoff or not ok(p):
                continue
            q = os.path.join(dst, os.path.relpath(p, src))
            os.makedirs(os.path.dirname(q), exist_ok=True)
            size = os.path.getsize(p)
            with open(p, 'rb') as a, open(q, 'wb') as b:
                if size <= 4_000_000:
                    shutil.copyfileobj(a, b)
                else:
                    b.write(a.read(64_000))
                    a.seek(size - KEEP)
                    b.write(a.read())
            os.utime(q, (os.path.getatime(p), os.path.getmtime(p)))
            n += 1
    return n


def agent_pids():
    ps = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True).stdout
    import re
    pids = [l.split(None, 1)[0] for l in ps.splitlines() if re.search(AGENTS, (l.strip().split(None, 1) + [''])[1])]
    return ps, pids


def snapshot(out):
    with tempfile.TemporaryDirectory() as d:
        counts = {'claude': copy_logs(os.path.join(HOME, '.claude/projects'), f'{d}/claude', lambda p: True),
                  'codex': copy_logs(os.path.join(HOME, '.codex/sessions'), f'{d}/codex', codex_ok)}
        os.makedirs(f'{d}/state')
        for name in os.listdir(STATE):
            p = os.path.join(STATE, name)
            if os.path.isfile(p) and not name.endswith('.sock') and os.path.getsize(p) < 1_000_000:
                shutil.copy2(p, f'{d}/state/{name}')
            elif os.path.isdir(p) and name in ('teams', 'presets', 'briefs', 'drafts'):
                shutil.copytree(p, f'{d}/state/{name}')
        ps, pids = agent_pids()
        open(f'{d}/ps.txt', 'w').write(ps)
        lsof = subprocess.run(['lsof', '-a', '-d', 'cwd', '-p', ','.join(pids), '-Fn'], capture_output=True, text=True).stdout if pids else ''
        open(f'{d}/lsof.txt', 'w').write(lsof)
        answers = {}  # method + params -> the core's result, replayed by the fake core
        try:
            answers[json.dumps(['machine.list', {}])] = core('machine.list')
            for all_ in (False, True):
                r = core('terminal.list', labels={}, all=all_)
                answers[json.dumps(['terminal.list', {'labels': {}, 'all': all_}])] = r
            for t in r['terminals']:
                a = t['address']
                answers[json.dumps(['terminal.get', {'terminal': a}])] = core('terminal.get', terminal=a)
                answers[json.dumps(['terminal.read', {'terminal': a, 'lines': 0}])] = core('terminal.read', terminal=a, lines=0)
        except OSError:
            pass  # no core running: the brains meet the same missing core
        json.dump(answers, open(f'{d}/core.json', 'w'))
        json.dump({'now': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00'), 'files': counts}, open(f'{d}/meta.json', 'w'))
        with tarfile.open(out, 'w') as t:  # tar keeps file times
            t.add(d, arcname='.')
    print(out, os.path.getsize(out) // 1024, 'KiB', counts)


def watch(seconds, out):
    end = time.time() + float(seconds)
    with open(out, 'a') as f:
        while time.time() < end:
            ps, pids = agent_pids()
            row = {'t': time.time(), 'agent_processes': len(pids), 'terminals': []}
            try:
                for t in core('terminal.list', labels={}, all=False)['terminals']:
                    if t['state'] != 'finished':
                        row['terminals'].append({
                            'address': t['address'], 'kind': t['labels'].get('assieme.kind'), 'state': t['state'],
                            'status': t.get('status'), 'status_ms': t.get('status_ms'),
                            'screen': core('terminal.read', terminal=t['address'], lines=60)['text']})
            except OSError:
                pass
            f.write(json.dumps(row) + '\n')
            f.flush()
            time.sleep(5)


if __name__ == '__main__':
    {'snapshot': lambda a: snapshot(a[0]), 'watch': lambda a: watch(*a)}[sys.argv[1]](sys.argv[2:])
