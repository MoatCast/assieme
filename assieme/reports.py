"""What agents say about themselves through the MCP server: stuck, or needing a decision.

One report per team and role, in a small file so it survives a brain restart. A report lasts until the agent's next
turn: once its hook says it is working again after the report, or its terminal is newer, the report no longer counts.
"""
import os
import threading
import time

from . import quiet

SHOWN = ('stuck', 'need_decision')  # what the panes show
STATES = SHOWN + ('done', 'working')  # these two clear an earlier report
_write = threading.Lock()  # the brain serves each call on its own thread


def _file():
    return os.path.join(quiet.state_dir(), 'reports.json')


def save(team, role, state, note='', now=None):
    if state not in STATES:
        raise ValueError(f'report one of: {", ".join(STATES)}.')
    if state in SHOWN and not note.strip():
        raise ValueError('Say in one line what is wrong or what needs deciding.')
    with _write:
        all_ = quiet._read_json(_file(), {})
        all_.pop(f'{team}/{role}', None)
        if state in SHOWN:
            all_[f'{team}/{role}'] = {'state': state, 'note': note.strip()[:300], 'ms': (now or time.time()) * 1000}
        quiet._write_json(_file(), all_)


def current(team, role, row):
    """The report that still holds for this terminal row, or None."""
    rep = quiet._read_json(_file(), {}).get(f'{team}/{role}')
    if not rep or (row.get('created_ms') or 0) > rep['ms']:
        return None
    hook = (row.get('status') or {}).get('state')
    return None if hook == 'working' and (row.get('status_ms') or 0) > rep['ms'] else rep
