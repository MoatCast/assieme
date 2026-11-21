"""Plan limits, read from each agent kind's own logs: how much of the plan's window is used, and when it comes back.

Limits are per account, not per agent: every agent of one kind shares one entry, and the latest log line wins.
Only the limit fields are read, never message text.
- Codex writes `rate_limits` (primary and secondary windows, `used_percent`, `resets_at`) on its `token_count` events.
- Claude Code writes no running percentage to its transcript (only its status line input carries one), so a Claude
  entry exists only while a limit is reached, from `rate_limit_info` (`status`, `resetsAt`, `rateLimitType`).
"""
import glob
import os

from .model import parse_ts
from .status import _tail_rows

NEWEST = 5            # logs per kind read: the account's latest state is in the newest ones
TAIL = 128 * 1024


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0  # removed since it was listed: read as oldest, and its read below finds nothing


def _newest(paths):
    return sorted(paths, key=_mtime, reverse=True)[:NEWEST]


def _rows(paths):
    """(epoch seconds, row) for every timed row in the tails of these logs."""
    out = []
    for p in paths:
        try:
            rows = _tail_rows(p, TAIL)
        except OSError:
            continue
        for o in rows:
            ts = parse_ts(o.get('timestamp'))
            if ts:
                out.append((ts.timestamp(), o))
    return sorted(out, key=lambda x: x[0])


def _codex(paths, now):
    latest = None
    for t, o in _rows(paths):
        p = o.get('payload')
        if isinstance(p, dict) and p.get('type') == 'token_count' and isinstance(p.get('rate_limits'), dict):
            latest = (t, p['rate_limits'])
    if not latest:
        return None
    t, rl = latest
    windows = []
    for w in (rl.get('primary'), rl.get('secondary')):
        if not isinstance(w, dict) or not isinstance(w.get('used_percent'), (int, float)):
            continue
        resets = w.get('resets_at')
        if not isinstance(resets, (int, float)):
            resets = None  # missing or malformed: an unknown reset time, not a crash
        if resets is None and isinstance(w.get('resets_in_seconds'), (int, float)):  # older Codex
            resets = t + w['resets_in_seconds']
        if resets is not None and resets <= now:
            continue  # that window has started over: its old use no longer counts
        windows.append((float(w['used_percent']), resets))
    if not windows:
        return None
    used, resets = max(windows, key=lambda w: w[0])
    return {'used': min(used, 100.0), 'reached': used >= 100, 'resets_at': resets}


def _claude(paths, now):
    latest = None
    for _, o in _rows(paths):
        info = o.get('rate_limit_info')
        if isinstance(info, dict):
            latest = info
    if not latest or latest.get('status') != 'rejected':
        return None
    resets = latest.get('resetsAt')
    if not isinstance(resets, (int, float)) or resets <= now:
        return None  # past its reset (or no time given): back to work, even before a new line arrives
    return {'used': None, 'reached': True, 'resets_at': float(resets)}


def read(claude_dir, codex_dir, now):
    """{kind: {'used': percent or None, 'reached': bool, 'resets_at': epoch or None}}; a kind with no data is left out."""
    found = {'claude': _claude(_newest(glob.glob(os.path.join(claude_dir, '*', '*.jsonl'))), now),
             'codex': _codex(_newest(glob.glob(os.path.join(codex_dir, '**', '*.jsonl'), recursive=True)), now)}
    return {k: v for k, v in found.items() if v}
