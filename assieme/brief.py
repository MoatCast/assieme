"""The shared team brief: one small file per team with the goal, the person's rules and the decisions they made.

Every agent gets it in its first message and can read it again with the `team_brief` tool, so a question the person
has answered once isn't asked twice. Kept by team name, so it outlives a restart of the team and of the brain.
"""
import hashlib
import re
import threading
import time

from . import quiet

GOAL_MAX, RULES_MAX, QUESTION_MAX, ANSWER_MAX = 2000, 4000, 300, 500
KEEP = 50          # decisions; the oldest go first
SAME_ANSWER_S = 300
READ_FIRST = 'Before you ask the person anything, call team_brief: the answer may be there already.'
_write = threading.Lock()  # the brain serves each call on its own thread


def _file(team):
    return quiet._path('briefs', re.sub(r'[^\w.-]', '-', team) + '.json')


def load(team):
    got = quiet._read_json(_file(team), {})
    got = got if isinstance(got, dict) else {}
    decisions = [d for d in got.get('decisions') or [] if isinstance(d, dict) and d.get('q') and d.get('a')]
    return {'goal': str(got.get('goal') or ''), 'rules': str(got.get('rules') or ''), 'decisions': decisions}


def _change(team, edit):
    with _write:
        b = load(team)
        edit(b)
        quiet._write_json(_file(team), b)
    return b


def save(team, goal=None, rules=None):
    """Set the goal, the rules or both; None leaves that part as it is."""
    def edit(b):
        if goal is not None:
            b['goal'] = goal.strip()[:GOAL_MAX]
        if rules is not None:
            b['rules'] = rules.strip()[:RULES_MAX]
    return _change(team, edit)


def add(team, role, question, answer, by='you', now=None):
    """One decision. `by` is who wrote it down: 'you' when the brain saw the person's answer, 'agent' when the agent
    reported it. The same question again replaces its earlier answer."""
    q, a, ms = ' '.join(question.split())[:QUESTION_MAX], ' '.join(answer.split())[:ANSWER_MAX], (now or time.time()) * 1000
    if not q or not a:
        raise ValueError('A decision needs the question and the answer.')

    def edit(b):
        # shortcut: an agent's record soon after ours for the same agent is taken as the same answer; compare the
        # words instead if agents start losing real second decisions.
        if by == 'agent' and any(d.get('role') == role and d.get('by') == 'you' and ms - d.get('ms', 0) < SAME_ANSWER_S * 1000
                                 for d in b['decisions']):
            return
        rest = [d for d in b['decisions'] if d['q'].lower() != q.lower()]
        rest.append({'id': hashlib.sha1(f'{q}{ms}'.encode()).hexdigest()[:10], 'q': q, 'a': a, 'role': role, 'by': by, 'ms': ms})
        b['decisions'] = rest[-KEEP:]
    return _change(team, edit)


def remove(team, id):
    def edit(b):
        b['decisions'] = [d for d in b['decisions'] if d.get('id') != id]
    return _change(team, edit)


def text(team, goal=True):
    """What agents read, or '' while the brief is empty. goal=False for a first message (see `team._brief`)."""
    b = load(team)
    parts = []
    if goal and b['goal']:
        parts.append(f"Goal: {b['goal']}")
    if b['rules']:
        parts.append(f"Rules from the person:\n{b['rules']}")
    if b['decisions']:
        parts.append('Decisions the person already made:\n' + '\n'.join(f"- {d['q']} -> {d['a']}" for d in b['decisions']))
    return 'Team brief, shared by every agent in this team:\n' + '\n'.join(parts) if parts else ''
