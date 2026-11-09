"""What agents say about themselves through the MCP server: stuck, or needing a decision.

One report per team and role, in a small file so it survives a brain restart. A report lasts until the agent's next
turn (hc counts turns), or a new one replaces it. A teammate's note isn't the person's answer, so a turn it wakes
doesn't count.
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


def _all():
    got = quiet._read_json(_file(), {})
    return got if isinstance(got, dict) else {}


def save(team, role, state, note='', now=None, turn=None):
    if state not in STATES:
        raise ValueError(f'report one of: {", ".join(STATES)}.')
    if state in SHOWN and not note.strip():
        raise ValueError('Say in one line what is wrong or what needs deciding.')
    with _write:
        all_ = _all()
        had = all_.pop(f'{team}/{role}', None)
        if state in SHOWN:
            all_[f'{team}/{role}'] = {'state': state, 'note': note.strip()[:300], 'ms': (now or time.time()) * 1000,
                                       'turn': turn, 'noted': 0}
        if had or state in SHOWN:  # the panes clear on every poll: only write when something changed
            quiet._write_json(_file(), all_)


def clear(team, role):
    save(team, role, 'done')


def running_turn(row):
    """The turn in progress or just ended. hc counts a turn when it ends, so one that's under way is one more."""
    turn = row.get('turn')
    return None if turn is None else turn + ((row.get('status') or {}).get('state') == 'working')


def noted(team, role):
    """A teammate's note is on its way to this agent: the turn it wakes leaves the report standing."""
    with _write:
        all_ = _all()
        rep = all_.get(f'{team}/{role}')
        if isinstance(rep, dict):
            rep['noted'] = rep.get('noted', 0) + 1
            quiet._write_json(_file(), all_)


def current(team, role, row):
    """The report that still holds for this terminal row, or None. One that no longer holds is removed: Settings says
    an agent's words are kept only until its next turn."""
    rep = _all().get(f'{team}/{role}')
    if rep is None:
        return None
    turn = running_turn(row)
    if (not isinstance(rep, dict) or not rep.get('note') or (row.get('created_ms') or 0) > rep.get('ms', 0)
            or (turn is not None and rep.get('turn') is not None and turn > rep['turn'] + rep.get('noted', 0))):
        clear(team, role)
        return None
    return rep
