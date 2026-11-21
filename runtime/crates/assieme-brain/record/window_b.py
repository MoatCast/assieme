"""Records session B's readers into tests/data/window_b.json: the screen readers of `prompts` and the Activity rows.
Run from the repo root with the repo's .venv Python:
TZ=UTC .venv/bin/python runtime/crates/assieme-brain/record/window_b.py

Each case is hand-made. The Activity log is written under a scratch folder; the times shown as HH:MM are left out of
the comparison, since they follow the machine's time zone.
"""
import datetime as dt
import json
import os
import sys
import tempfile

CRATE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
REPO = os.path.abspath(os.path.join(CRATE, '../../..'))
sys.path.insert(0, REPO)
SCRATCH = tempfile.mkdtemp(prefix='window-b-')
os.environ['XDG_STATE_HOME'] = os.path.join(SCRATCH, 'state')
os.environ['HOME'] = os.path.join(SCRATCH, 'home')

from assieme import activity, prompts  # noqa: E402

NOW = dt.datetime(2026, 11, 21, 12, 0, 0, tzinfo=dt.timezone.utc)

MENU = '''● Bash(rm -rf build)

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for rm commands in /work
   3. No, and tell Claude what to do differently (esc)
'''
SCREENS = [
    '', 'plain text\nno menu', MENU,
    '  Choose the text style that looks best with your terminal\n  ❯ 1. Dark\n',
    'Choose your color scheme:', 'Security notes:\n\nPress Enter to continue', 'Security notes:',
    'Hooks need review before they run', 'Do you trust the files in this folder?\n ❯ 1. Yes, I trust this folder\n 2. No, exit',
    'Trust and continue?', 'Codex\n › 1. Trust and continue\n   2. Exit',
    # a numbered list in the agent's reply has no pointer: not a menu
    '⏺ Here is the plan:\n  1. First\n  2. Second\n  3. Third\n',
    ' ❯ 1. Only one item\n', ' ❯ 1. One\n   2. Two\n   3. Type something.\n   4. Chat about this\n',
    ' ❯ 1. Yes\n   2. No\n', ' ❯ 1. Yes, allow all edits during this session\n   2. Yes\n   3. No\n',
    ' ❯ 1. Yes\n   2. Yes, always\n   3. Yes, and always\n   4. No\n   5. No, never\n',
    ' > 1. ' + 'a very long choice ' * 5 + '\n   2. short\n',
    '\n\n\nQuestion?\n\n\n ❯ 1. Yes\n\n\n   2. No\n   some description\n',
    '⏺ I finished the change.\n\n  Should I also update the docs?\n\n✻ Brewed for 3s\n',
    '⏺ Done.\n  All tests pass.\n', '• Which branch should I use?\n  main or dev?\n\n› ',
    '⏺ Two paragraphs\n\n  First one.\n\n  Is this fine\n  with you?\n───────\n❯ ',
    '⏺ ' + 'x' * 400 + '?\n', 'no agent text here?\n', '⏺\n', '⏺ Fine?\n⏺ Another one?\n',
    '⏺ Is this a question?\n  It has an answer after.\n',
]


def screens():
    out = []
    for s in SCREENS:
        menu = prompts.menu(s)
        out.append({'in': s, 'trust': prompts.trust_keys(s), 'first_run': prompts.first_run_keys(s),
                    'hook_review': prompts.hook_review(s), 'menu': [list(m) for m in menu],
                    'labels': {str(k): v for k, v in prompts.permission_labels(menu).items()},
                    'context': prompts.menu_context(s), 'question': prompts.question(s)})
    return out


def iso(minutes):
    return (NOW - dt.timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')


ENTRIES = [
    {'id': 'a1', 'at': iso(600), 'action': 'fix_mode', 'agent': 'nudge', 'team': None, 'by': 'you', 'ok': True, 'fix': 'nudge',
     'old': 'ask', 'new': 'auto'},
    {'id': 'a2', 'at': iso(30), 'action': 'practice', 'agent': 'Small steps', 'team': None, 'by': 'you', 'ok': True,
     'group': 'g', 'old': True, 'new': False},
    {'id': 'a3', 'at': iso(25), 'action': 'nudge', 'agent': 'builder', 'team': 'web', 'by': 'auto', 'ok': True,
     'reason': 'It has been stuck', 'problem': 'p1', 'sent': 'try another way', 'check_until': iso(-5),
     'measured': {'what': 'x'}},
    {'id': 'a4', 'at': iso(24), 'action': 'nudge', 'agent': 'builder', 'team': 'web', 'by': 'auto', 'ok': True,
     'reason': 'Again', 'check_until': iso(10)},
    {'id': 'o1', 'at': iso(20), 'action': 'outcome', 'agent': 'builder', 'team': 'web', 'by': 'auto', 'of': 'a4', 'result': 'fixed'},
    {'id': 'a5', 'at': iso(20), 'action': 'menu', 'agent': 'builder', 'team': 'web', 'by': 'you', 'ok': True, 'reason': 'You answered its question.'},
    {'action': 'answer', 'at': iso(19), 'agent': 'builder', 'team': 'web', 'by': 'you', 'reason': 'old: the whole question'},
    {'id': 'a6', 'at': iso(18), 'action': 'tell', 'agent': 'tester', 'team': 'web', 'by': 'auto', 'ok': False, 'detail': 'no terminal'},
    {'id': 'a7', 'at': iso(18), 'action': 'tell', 'agent': 'tester', 'team': 'web', 'by': 'auto', 'ok': False},
    {'id': 'a8', 'at': iso(17), 'action': 'add_agent', 'agent': 'helper', 'team': 'api', 'by': 'you', 'ok': True},
    {'id': 'a9', 'at': iso(1700), 'action': 'add_agent', 'agent': 'old', 'team': 'api', 'by': 'you', 'ok': True},
    {'id': 'u1', 'at': iso(16), 'action': 'undo', 'agent': 'helper', 'team': 'api', 'by': 'you', 'of': 'a8', 'undid': 'Added helper'},
    {'id': 'u2', 'at': iso(15), 'action': 'undo', 'agent': 'x', 'team': None, 'by': 'you'},
    {'id': 'b1', 'at': iso(14), 'action': 'ask-changes', 'agent': 'builder', 'team': 'web', 'by': 'you', 'reason': 'a reviewer note'},
    {'id': 'b2', 'at': iso(13), 'action': 'quiet', 'agent': 'a to b', 'team': 'web', 'by': 'auto', 'title': 'Held notes'},
    {'id': 'b3', 'at': iso(12), 'action': 'merge', 'agent': 'builder', 'team': 'web', 'by': 'you', 'ok': True, 'restore': None},
    {'id': 'b4', 'at': iso(11), 'action': 'something-new', 'agent': None, 'team': 'web', 'by': 'you'},
    {'id': 'b5', 'at': 'not a time', 'action': 'run_found', 'agent': 'x', 'team': 'found', 'by': 'you'},
    {'id': 'b6', 'at': iso(10), 'action': 'deliver', 'agent': 'builder', 'team': 'web', 'by': 'you', 'ok': 0},
    {'id': 'b7', 'at': iso(9), 'action': 'bringover', 'agent': 'team', 'team': 'web', 'by': 'you', 'ok': True},
    {'id': 'b8', 'at': iso(8), 'action': 'stop_agent', 'agent': 'helper', 'team': 'api', 'by': 'you'},
]
CASES = [(None, 'all'), ('web', 'all'), ('api', 'you'), (None, 'auto'), ('nobody', 'all')]


def rows():
    log = os.path.join(SCRATCH, 'log.jsonl')
    with open(log, 'w') as f:
        f.write(''.join(json.dumps(e) + '\n' for e in ENTRIES))
    out = []
    for team, flt in CASES:
        got = activity.list_rows(team, flt, path=log, now=NOW)
        out.append({'team': team, 'filter': flt, 'rows': got['rows']})
    return {'entries': ENTRIES, 'now': NOW.strftime('%Y-%m-%dT%H:%M:%SZ'), 'cases': out}


def main():
    data = {'screens': screens(), 'activity': rows()}
    path = os.path.join(CRATE, 'tests/data/window_b.json')
    with open(path, 'w') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        f.write('\n')
    print(f'{len(data["screens"])} screens, {len(CASES)} activity cases -> {path}', file=sys.stderr)


if __name__ == '__main__':
    main()
