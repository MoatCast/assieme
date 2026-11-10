"""The task board: what a team is doing, as cards in To do, Doing, Review and Done.

A card's status is said, never guessed: an agent says it through the MCP server, the person moves the card, or the
brain acts on a fact it has about the branch (merged, discarded, not merged yet). Nothing here reads logs or terminals.
Kept in one small file, like reports, so it survives a brain restart.
"""
import contextlib
import json
import os
import re
import threading
import time

from . import live, quiet, status as status_, team as team_

STATUSES = ('todo', 'doing', 'review', 'done')
MAX_TITLE, MAX_NOTE, MAX_CARDS = 120, 200, 200
TOLD = ('New task from the person, card {id} on the board: {title}\n\n'
        'Call set_task when you start it (doing) and when it is committed and ready to look at (review).')
_write = threading.Lock()  # the brain serves each call on its own thread


def _file():
    return os.path.join(quiet.state_dir(), 'tasks.json')


def _all():
    got = quiet._read_json(_file(), {})
    return got if isinstance(got, dict) else {}


def _of(all_, team):
    b = all_.get(team)
    ok = isinstance(b, dict) and isinstance(b.get('cards'), list) and isinstance(b.get('next'), int)
    return b if ok else {'title': '', 'next': 1, 'cards': []}


def board(team):
    return _of(_all(), team)


@contextlib.contextmanager
def _edit(team):
    """Change one team's board. Nothing is written when the change raises, or changes nothing."""
    with _write:
        all_ = _all()
        before = json.dumps(all_.get(team))
        b = all_[team] = _of(all_, team)
        yield b
        if json.dumps(b) != before:
            quiet._write_json(_file(), all_)


def _ms(now=None):
    return int((now or time.time()) * 1000)


def _card(b, id):
    c = next((c for c in b['cards'] if c['id'] == str(id).strip().lstrip('#')), None)
    if not c:
        raise ValueError(f'No task {id} on this board.')
    return c


def fresh(team, task=''):
    """A new team's board: empty, titled with the first sentence of its task."""
    lines = (task or '').strip().splitlines()
    title = re.split(r'(?<=[.!?])\s', lines[0].strip(), maxsplit=1)[0].rstrip('.') if lines else ''
    with _edit(team) as b:
        b.update(title=title[:MAX_TITLE], next=1, cards=[])


def add(team, title, role=None, by='you', now=None):
    title = ' '.join((title or '').split())[:MAX_TITLE]
    if not title:
        raise ValueError('Say what needs doing.')
    with _edit(team) as b:
        if len(b['cards']) >= MAX_CARDS:
            raise ValueError('This board is full. Remove some finished cards first.')
        card = {'id': str(b['next']), 'title': title, 'role': role or None, 'status': 'todo', 'by': by, 'note': '',
                'needs_you': False, 'ms': _ms(now), 'moved_ms': _ms(now)}
        b['next'] += 1
        b['cards'].append(card)
    return card


def move(team, id, status, note=None, needs_you=False, now=None):
    """Set a card's status. `note` None keeps the note it has."""
    if status not in STATUSES:
        raise ValueError(f'status is one of: {", ".join(STATUSES)}.')
    with _edit(team) as b:
        c = _card(b, id)
        if c['status'] != status:
            c['moved_ms'] = _ms(now)
        c['status'], c['needs_you'] = status, bool(needs_you)
        if note is not None:
            c['note'] = ' '.join(str(note).split())[:MAX_NOTE]
        c.pop('was', None)  # said again since the merge: an undo no longer moves it
        c.pop('merge', None)
    return c


def remove(team, id):
    with _edit(team) as b:
        b['cards'].remove(_card(b, id))
    return {'ok': True}


# --- what the brain knows about a branch -----------------------------------------------------------------------

def _fact(team, change):
    try:
        with _edit(team) as b:
            for c in b['cards']:
                change(c)
    except OSError:
        pass  # the merge or discard stands: the card can still be moved by hand


def merged(team, role, base, undo, now=None):
    """Review merged this agent's branch: its cards in Doing and Review are done. `undo` names the merge."""
    def change(c):
        if c['role'] == role and c['status'] in ('doing', 'review'):
            c.update(was=[c['status'], c['note']], merge=undo, status='done', note=f'merged into {base}',
                     needs_you=False, moved_ms=_ms(now))
    _fact(team, change)


def unmerged(team, undo, now=None):
    def change(c):
        if c.get('merge') == undo:
            c['status'], c['note'] = c.pop('was')
            c.pop('merge')
            c['moved_ms'] = _ms(now)
    _fact(team, change)


def discarded(team, role, now=None):
    def change(c):
        if c['role'] == role and c['status'] in ('doing', 'review'):
            c.update(status='todo', note='Its work was discarded.', needs_you=False, moved_ms=_ms(now))
    _fact(team, change)


# --- what the app and the agents ask ---------------------------------------------------------------------------

def _shown(c, now=None):
    return {k: c.get(k) for k in ('id', 'title', 'role', 'status', 'by', 'note', 'needs_you')} | {
        'moved_s': max(0, (_ms(now) - c.get('moved_ms', 0)) // 1000)}


class Board:
    """The board as the app shows it: each card joined with what is true right now about its agent and branch."""

    def __init__(self, brain, reviews, me):
        self.brain, self.reviews, self.me = brain, reviews, me  # me: an agent's terminal address -> (team, role, row)

    def get(self, team):
        built = self.brain.map(team)
        panes = {p['role']: p for p in built['panes']}
        b, cards = board(team), []
        for c in b['cards']:
            c = _shown(c)
            p = panes.get(c['role'])
            if c['status'] == 'doing' and p:
                c['agent'] = {k: p.get(k) for k in ('role', 'state', 'since_s', 'detail', 'fix', 'reachable')}
            if c['status'] == 'review' and c['role']:
                c['branch'] = self.reviews.card(team, c['role'])
            cards.append(c)
        return {'team': team, 'title': b['title'], 'cards': cards, 'spend': built.get('spend'),
                'spend_ready': built.get('spend_ready', True)}

    def add(self, team, title, role=None):
        """The person's New task. A card given to a running agent is also sent to it: otherwise nobody would know."""
        card = add(team, title, role or None)
        p = next((x for x in self.brain.map(team)['panes'] if x['role'] == role), None) if role else None
        told = bool(p and p.get('live') and p.get('reachable', True))
        if told:
            rt = self.brain._resolve(team, status_.now_utc())[3]
            rt.send(p['address'], TOLD.format(id=card['id'], title=card['title']))
            live.record('send', role, 'You gave it a task on the board.', team=team, address=p['address'])
        return {'card': _shown(card), 'told': told}

    def move(self, team, id, status):
        return _shown(move(team, id, status))

    # --- agents, each for its own team only ---

    def tasks(self, address):
        name, me, _ = self.me(address)
        return {'team': name, 'you': me, 'tasks': [_shown(c) for c in board(name)['cards']]}

    def task_add(self, address, title, assignee=''):
        name, me, _ = self.me(address)
        roles = [r['labels'].get(team_.ROLE) for r in self.brain._assieme_rows(name) if r.get('state') == 'live']
        if assignee and assignee not in roles:
            raise ValueError(f'No running agent named {assignee}. Your team: {", ".join(roles)}.')
        return {'task': _shown(add(name, title, assignee or None, by=me))}

    def task_set(self, address, task, status, note='', waiting_for_person=False):
        name, _, _ = self.me(address)
        detail = 'Done.'
        role = _card(board(name), task)['role']
        if status == 'done' and role and (self.reviews.card(name, role) or {}).get('ahead'):
            status = 'review'  # a fact about the branch beats the agent's word
            detail = f"{role}'s branch isn't merged yet, so the card is in Review. The person merges it there."
        return {'task': _shown(move(name, task, status, note, waiting_for_person)), 'detail': detail}

    def handlers(self):
        return {'board.get': self.get, 'board.add': self.add, 'board.move': self.move, 'board.remove': remove,
                'mcp.tasks': self.tasks, 'mcp.task_add': self.task_add, 'mcp.task_set': self.task_set}
