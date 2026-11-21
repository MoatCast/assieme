"""Records what Python's bringover.py readers answer for other tools' files: `tests/data/bringover.json`.

Run from the repository root with the repo's own Python:  .venv/bin/python runtime/crates/assieme-brain/record/bringover_fixtures.py
Folders named in the cases don't exist, so no git is asked and the answers don't depend on this Mac.
"""
import dataclasses
import datetime as dt
import json
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
sys.path.insert(0, ROOT)
from assieme import bringover  # noqa: E402

NOW = 1_800_000_000  # 15 Jan 2027
DAY = 86400


def jl(*rows):
    return '\n'.join(r if isinstance(r, str) else json.dumps(r) for r in rows) + '\n'


# --- agent history: Claude Code and Codex logs ---------------------------------------------------------------------
HISTORY_FILES = [
    # Claude Code: <claude>/<folder>/<session>.jsonl
    {'path': 'claude/-w-app/s1.jsonl', 'age_days': 1, 'content': jl({'type': 'user'}, {'cwd': '/w/app', 'message': {'model': 'opus'}})},
    {'path': 'claude/-w-app/s2.jsonl', 'age_days': 2, 'content': jl({'cwd': '/w/app/wt/a'}, {'message': {'model': 'sonnet'}})},
    {'path': 'claude/-w-app/old.jsonl', 'age_days': 31, 'content': jl({'cwd': '/w/old'})},
    {'path': 'claude/-w-app/edge.jsonl', 'age_days': 30, 'content': jl({'cwd': '/w/edge'})},
    {'path': 'claude/.hidden/s.jsonl', 'age_days': 1, 'content': jl({'cwd': '/w/hidden'})},
    {'path': 'claude/-w-app/.dot.jsonl', 'age_days': 1, 'content': jl({'cwd': '/w/dotfile'})},
    {'path': 'claude/-w-app/nocwd.jsonl', 'age_days': 1, 'content': jl({'type': 'x'}, {'cwd': 5}, {'cwd': None})},
    {'path': 'claude/-w-app/bad.jsonl', 'age_days': 1, 'content': 'not json\n[1,2]\n"str"\n' + jl({'cwd': '/w/after-bad', 'message': 'text'})},
    {'path': 'claude/-w-app/model.jsonl', 'age_days': 1, 'content': jl({'cwd': '/w/m', 'message': {'model': 5}}, {'message': {'model': 'haiku'}})},
    {'path': 'claude/-w-app/crlf.jsonl', 'age_days': 1, 'content': '{"cwd": "/w/crlf"}\r\n{"message": {"model": "x"}}\r\n'},
    {'path': 'claude/-w-app/cr.jsonl', 'age_days': 1, 'content': '{"cwd": "/w/cr"}\r{"message": {"model": "y"}}\r'},
    {'path': 'claude/-w-app/bom.jsonl', 'age_days': 1, 'content': '﻿{"cwd": "/w/bom"}\n{"cwd": "/w/after-bom"}\n'},
    # `NaN` in a row is the reading-rules module's case (pyrules), recorded there, not here.
    {'path': 'claude/-w-app/uni.jsonl', 'age_days': 1, 'content': jl({'cwd': '/w/ünï'})},
    {'path': 'claude/-w-app/late.jsonl', 'age_days': 1,
     'content': ''.join(jl({'n': i}) for i in range(200)) + jl({'cwd': '/w/after-200'})},
    {'path': 'claude/-w-app/inside.jsonl', 'age_days': 1,
     'content': ''.join(jl({'n': i}) for i in range(199)) + jl({'cwd': '/w/at-200'})},
    {'path': 'claude/-w-app/two.txt', 'age_days': 1, 'content': jl({'cwd': '/w/txt'})},
    {'path': 'claude/-w-app/deep/x.jsonl', 'age_days': 1, 'content': jl({'cwd': '/w/deep'})},
    {'path': 'claude/loose.jsonl', 'age_days': 1, 'content': jl({'cwd': '/w/loose'})},
    {'path': 'claude/-w-app/rawbad.jsonl', 'age_days': 1, 'hex': '7b22637764223a20222f772f6261\xff'.encode('latin-1').hex()
     if False else b'{"cwd": "/w/raw\xff\xfe"}\n'.hex()},
    # Codex: <codex>/<y>/<m>/<d>/<name>.jsonl
    {'path': 'codex/2027/01/10/a.jsonl', 'age_days': 5,
     'content': jl({'type': 'session_meta', 'payload': {'id': 'abc', 'cwd': '/w/cx'}}, {'type': 'turn_context', 'payload': {'model': 'gpt-5.5'}})},
    {'path': 'codex/2027/01/10/b.jsonl', 'age_days': 5, 'content': jl({'type': 'session_meta', 'payload': {'id': 7, 'cwd': '/w/cx7'}})},
    {'path': 'codex/2027/01/10/c.jsonl', 'age_days': 5, 'content': jl({'type': 'session_meta', 'payload': {'cwd': '/w/noid'}})},
    {'path': 'codex/2027/01/10/d.jsonl', 'age_days': 5, 'content': jl({'type': 'session_meta', 'payload': {'id': 'd', 'cwd': 5}})},
    {'path': 'codex/2027/01/10/e.jsonl', 'age_days': 5, 'content': jl({'type': 'session_meta', 'payload': 'x'}, {'type': 'session_meta', 'payload': {'id': 'e', 'cwd': '/w/second'}})},
    {'path': 'codex/2027/01/10/f.jsonl', 'age_days': 40, 'content': jl({'type': 'session_meta', 'payload': {'id': 'f', 'cwd': '/w/old'}})},
    {'path': 'codex/2027/01/10/g.jsonl', 'age_days': 5,
     'content': ''.join(jl({'type': 'x', 'payload': {}}) for i in range(49)) + jl({'type': 'session_meta', 'payload': {'id': 'g', 'cwd': '/w/at-50'}})},
    {'path': 'codex/2027/01/10/h.jsonl', 'age_days': 5,
     'content': ''.join(jl({'type': 'x', 'payload': {}}) for i in range(50)) + jl({'type': 'session_meta', 'payload': {'id': 'h', 'cwd': '/w/after-50'}})},
    {'path': 'codex/2027/01/10/i.jsonl', 'age_days': 5,
     'content': jl({'type': 'session_meta', 'payload': {'id': 'i', 'cwd': '/w/ctx'}}, {'type': 'turn_context', 'payload': {}}, {'type': 'turn_context', 'payload': {'model': 'late'}})},
    {'path': 'codex/2027/01/.h/j.jsonl', 'age_days': 5, 'content': jl({'type': 'session_meta', 'payload': {'id': 'j', 'cwd': '/w/h'}})},
    {'path': 'codex/2027/01/x.jsonl', 'age_days': 5, 'content': jl({'type': 'session_meta', 'payload': {'id': 'k', 'cwd': '/w/shallow'}})},
]


def run_history():
    with tempfile.TemporaryDirectory() as tmp:
        for f in HISTORY_FILES:
            path = os.path.join(tmp, f['path'])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as fh:
                fh.write(bytes.fromhex(f['hex']) if 'hex' in f else f['content'].encode('utf-8'))
            os.utime(path, (NOW - f['age_days'] * DAY, NOW - f['age_days'] * DAY))
        now = dt.datetime.fromtimestamp(NOW, dt.timezone.utc)
        got = bringover.history(os.path.join(tmp, 'claude'), os.path.join(tmp, 'codex'), now)
    return sorted(got, key=lambda r: (r['kind'], r['session'], r['cwd']))


history = {'now': NOW, 'files': [{k: v for k, v in f.items()} for f in HISTORY_FILES], 'out': run_history()}

# --- Claude Squad ----------------------------------------------------------------------------------------------------


def inst(**kw):
    wt = {'repo_path': '/r/app', 'worktree_path': '/r/gone', 'branch_name': 'feat'}
    wt.update(kw.pop('wt', {}))
    return {'title': 'one', 'program': 'claude', 'status': 0, 'worktree': wt, **kw}


SQUAD = [
    None,
    '{bad',
    '[]',
    json.dumps({'instances': [inst(), inst(title='two', program='codex --model x', status=3), inst(title='three', program='aider'),
                              inst(title=None, program='', wt={'worktree_path': None}), inst(program=None), 'junk', {'worktree': 'x'},
                              {'worktree': {'repo_path': ''}}, inst(status=1, wt={'branch_name': None}, branch='fallback'),
                              inst(program='/usr/bin/gemini', status=2), inst(program='cursor-agent', status='x') if False else inst(program='cursor-agent'),
                              inst(program='agy --x'), inst(program='claude-code'), inst(program='opencode run')]}),
    json.dumps({'instances': json.dumps([inst(title='s')])}),
    json.dumps({'instances': 'not json'}),
    json.dumps({'instances': {'a': 1}}),
    json.dumps({'instances': None}),
    json.dumps(['list']),
    json.dumps({}),
]


def run_squad(text):
    with tempfile.TemporaryDirectory() as home:
        if text is not None:
            os.makedirs(os.path.join(home, '.claude-squad'))
            with open(os.path.join(home, '.claude-squad', 'state.json'), 'w') as f:
                f.write(text)
        found, problem = bringover.read_claude_squad(home)
    return {'found': [dataclasses.asdict(f) for f in found], 'problem': problem}


squad = [{'state': t, 'out': run_squad(t)} for t in SQUAD]

# --- Vibe Kanban -----------------------------------------------------------------------------------------------------
SCHEMA = '''
CREATE TABLE projects (id TEXT, name TEXT);
CREATE TABLE repos (id TEXT, path TEXT, setup_script TEXT, archive_script TEXT, dev_server_script TEXT);
CREATE TABLE tasks (id TEXT, project_id TEXT, title TEXT, description TEXT, status TEXT);
CREATE TABLE workspaces (id TEXT, task_id TEXT, container_ref TEXT, branch TEXT, archived INTEGER, worktree_deleted INTEGER);
CREATE TABLE workspace_repos (workspace_id TEXT, repo_id TEXT);
CREATE TABLE sessions (id TEXT, workspace_id TEXT, executor TEXT);
'''
RESUME = '''
CREATE TABLE execution_processes (id TEXT, session_id TEXT);
CREATE TABLE coding_agent_turns (execution_process_id TEXT, agent_session_id TEXT);
'''
DATA = '''
INSERT INTO projects VALUES ('p1', 'Proj');
INSERT INTO repos VALUES ('r1', '/r/app', 'npm i', NULL, 'npm run dev');
INSERT INTO repos VALUES ('r2', '/r/other', NULL, NULL, NULL);
INSERT INTO tasks VALUES ('t1', 'p1', 'Fix login', 'The login breaks', 'inprogress');
INSERT INTO tasks VALUES ('t2', 'p1', 'Add docs', NULL, 'todo');
INSERT INTO tasks VALUES ('t3', 'p1', 'Old idea', 'x', 'cancelled');
INSERT INTO tasks VALUES ('t4', 'p1', 'Unknown state', 'x', 'weird');
INSERT INTO tasks VALUES ('t5', 'p9', 'Other project', 'x', 'done');
INSERT INTO workspaces VALUES ('w1', 't1', '/r/ws/w1', 'vk/fix-login', 0, 0);
INSERT INTO workspaces VALUES ('w2', 't2', '/r/ws/w2', 'vk/docs', 0, 1);
INSERT INTO workspaces VALUES ('w3', 't3', NULL, 'vk/old', 1, 0);
INSERT INTO workspaces VALUES ('w4', 'tX', '/r/ws/w4', 'vk/orphan', 0, 0);
INSERT INTO workspaces VALUES ('w5', 't1', '/r/ws/w5', 'vk/fix-login-2', 0, 0);
INSERT INTO workspaces VALUES ('w6', 't5', '/r/ws/w6', 'vk/other', 0, 0);
INSERT INTO workspace_repos VALUES ('w1', 'r1');
INSERT INTO workspace_repos VALUES ('w2', 'r1');
INSERT INTO workspace_repos VALUES ('w3', 'r1');
INSERT INTO workspace_repos VALUES ('w4', 'r2');
INSERT INTO workspace_repos VALUES ('w5', 'r1');
INSERT INTO workspace_repos VALUES ('w6', 'r2');
INSERT INTO sessions VALUES ('s1', 'w1', 'CLAUDE_CODE');
INSERT INTO sessions VALUES ('s1b', 'w1', 'CODEX');
INSERT INTO sessions VALUES ('s2', 'w2', 'cursor_agent');
INSERT INTO sessions VALUES ('s5', 'w5', 'MYSTERY');
'''
RESUME_DATA = '''
INSERT INTO execution_processes VALUES ('e1', 's1b');
INSERT INTO execution_processes VALUES ('e2', 's1b');
INSERT INTO coding_agent_turns VALUES ('e1', 'agent-old');
INSERT INTO coding_agent_turns VALUES ('e2', 'agent-new');
INSERT INTO coding_agent_turns VALUES ('e2', NULL);
'''
VIBE = {
    'full': SCHEMA + RESUME + DATA + RESUME_DATA,
    'no resume tables': SCHEMA + DATA,
    'no data': SCHEMA,
    'missing table': SCHEMA.replace('CREATE TABLE sessions (id TEXT, workspace_id TEXT, executor TEXT);', ''),
    'missing column': SCHEMA.replace('archived INTEGER, ', ''),
    'half resume tables': SCHEMA + 'CREATE TABLE execution_processes (id TEXT);' + DATA,
    'not a database': None,
}


def run_vibe(sql):
    with tempfile.TemporaryDirectory() as home:
        d = os.path.join(home, 'Library', 'Application Support', 'ai.bloop.vibe-kanban')
        os.makedirs(d)
        path = os.path.join(d, 'db.v2.sqlite')
        if sql is None:
            with open(path, 'wb') as f:
                f.write(b'this is not a database' * 50)
        else:
            con = sqlite3.connect(path)
            con.executescript(sql)
            con.commit()
            con.close()
        found, tasks, scripts, problem = bringover.read_vibe_kanban(home)
    return {'found': [dataclasses.asdict(f) for f in found], 'tasks': tasks, 'scripts': scripts, 'problem': problem}


vibe = [{'name': k, 'sql': v, 'out': run_vibe(v)} for k, v in VIBE.items()]
vibe.append({'name': 'no file', 'sql': False, 'out': {'found': [], 'tasks': {}, 'scripts': {}, 'problem': None}})

# --- the sheet's words -------------------------------------------------------------------------------------------------
F = bringover.Found
ITEMS = [F('conductor', '/p', 'a', '/p/a', 'b', 2, False, 'claude', 'opus'), F('conductor', '/p', 'b', None, 'b2', 0, True, None, None),
         F('conductor', '/p', 'c', '/p/c', 'b3', 1, False, 'weird', None), F('claude-squad', '/p', 'd', '/p/d', 'b4')]
summaries = [{'tool': items[0].tool, 'items': [dataclasses.asdict(i) for i in items], 'out': bringover._summary(items)}
             for items in (ITEMS[:1], ITEMS[:2], ITEMS[:3], ITEMS[3:], ITEMS[:1] * 3)]
becomes = [{'item': dataclasses.asdict(i), 'n': n, 'out': bringover._becomes(i, n)} for i in ITEMS for n in (1, 3)]
tools = [{'folder': f, 'home': h, 'out': bringover.tool_of(f, h)} for f, h in [
    ('/h/conductor/workspaces/app/tokyo', '/h'), ('/h/conductor', '/h'), ('/x/.conductor/oslo', '/h'), ('/h/.claude-squad/worktrees/x', '/h'),
    ('/a/vibe-kanban/worktrees/abc/app', '/h'), ('/tmp/.vibe-kanban-workspaces/x', '/h'), ('/h/other/x', '/h'), ('/h/conductorx/y', '/h'),
    ('/h/conductor/', '/h')]]
kinds = [{'program': p, 'out': bringover._kind_of(p)} for p in ['claude', '/usr/bin/codex --x', 'cursor-agent', 'agy', 'aider', '', None, 'gemini x',
                                                                  'cursor', 'antigravity', ' claude']]

path = os.path.join(os.path.dirname(__file__), '..', 'tests', 'data', 'bringover.json')
with open(path, 'w') as f:
    json.dump({'history': history, 'squad': squad, 'vibe': vibe, 'summaries': summaries, 'becomes': becomes, 'tools': tools, 'kinds': kinds},
              f, indent=0, ensure_ascii=False)
    f.write('\n')
print(len(history['out']), 'sessions,', len(squad), 'squad,', len(vibe), 'vibe')
