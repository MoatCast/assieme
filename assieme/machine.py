"""The optional F6 sampler: free memory and load, once a minute from the brain's tick, in a small capped file."""
import json
import os
import subprocess
import sys

from .model import parse_ts

EVERY = 60
MAX_BYTES = 256 * 1024


def path():
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'assieme', 'machine.jsonl')


def _free_pct():
    if sys.platform == 'darwin':
        out = subprocess.run(['sysctl', '-n', 'kern.memorystatus_level'], capture_output=True, text=True, timeout=5)
        return float(out.stdout.strip())
    with open('/proc/meminfo') as f:
        info = {k: int(v.split()[0]) for k, v in (line.split(':', 1) for line in f)}
    return 100 * info['MemAvailable'] / info['MemTotal']


def sample():
    return {'free_pct': _free_pct(), 'load': os.getloadavg()[0], 'cpus': os.cpu_count() or 1}


def _last_at(file):
    try:
        with open(file, 'rb') as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 512))
            return parse_ts(json.loads(f.read().splitlines()[-1]).get('at'))
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def tick(now, file=None, sampler=sample):
    """Record one sample if the last is at least a minute old. Returns whether it did. Never fatal."""
    file = file or path()
    last = _last_at(file)
    if last and (now - last).total_seconds() < EVERY:
        return False
    try:
        row = json.dumps({'at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), **sampler()})
        os.makedirs(os.path.dirname(file), exist_ok=True)
        if os.path.exists(file) and os.path.getsize(file) >= MAX_BYTES:
            with open(file, 'rb') as f:
                f.seek(-MAX_BYTES // 2, os.SEEK_END)
                keep = f.read().split(b'\n', 1)[-1]
            with open(file + '.tmp', 'wb') as f:
                f.write(keep)
            os.replace(file + '.tmp', file)
        with open(file, 'a') as f:
            f.write(row + '\n')
        return True
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return False


def read(since, file=None):
    """Samples since then, each with `at` as a datetime. Nothing if the sampler never ran."""
    try:
        with open(file or path()) as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            r = json.loads(line)
            r['at'] = parse_ts(r.get('at'))
            if r['at'] and r['at'] >= since and {'free_pct', 'load', 'cpus'} <= set(r):
                out.append(r)
        except (ValueError, AttributeError):
            continue
    return out
