"""Records session A's readers into tests/data/readers_a.json: plan limits, the log adapters, status's log reading,
recent actions, and detect's command readers. Run from the repo root with the repo's .venv Python:
.venv/bin/python runtime/crates/assieme-brain/record/readers_a.py

Each case is a hand-made folder tree (files as runs of lines, with fixed file times) and the calls made on it. The
tree is written under a scratch folder and every call uses paths relative to it, so the Rust test rebuilds the same
tree and gets the same paths. Inputs of the limits cases come from the rust-anything spike
(docs/superpowers/specs/rust-brain-fixtures/limits_fixtures.py); their outputs are recorded again here.
"""
import datetime as dt
import json
import os
import sqlite3
import sys
import tempfile

CRATE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
REPO = os.path.abspath(os.path.join(CRATE, '../../..'))
sys.path.insert(0, REPO)
SCRATCH = tempfile.mkdtemp(prefix='readers-a-')
os.environ['XDG_STATE_HOME'] = os.path.join(SCRATCH, 'state')  # settings: the defaults
os.environ['HOME'] = '/home/fx'  # fixed: agent names shorten against it; nothing is read there
os.chdir(SCRATCH)

from assieme import adapters, detect, limits, live, status  # noqa: E402

NOW = 1_800_000_000.0
NOW_DT = dt.datetime.fromtimestamp(NOW, dt.timezone.utc)
HOME = os.environ['HOME']


def stamp(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


# --- trees -----------------------------------------------------------------------------------------------------------

class Tree:
    """Files written as lines joined by \\n with a trailing \\n, each with a fixed time."""

    def __init__(self, root):
        self.root, self.files, self.dirs, self.dbs, self.n = root, [], [], [], 0

    def put(self, path, lines, mtime=None):
        self.n += 1
        mtime = NOW - 3600 + self.n if mtime is None else mtime  # written later: newer
        full = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        raw = [x if isinstance(x, bytes) else (x if isinstance(x, str) else json.dumps(x)).encode() for x in lines]
        with open(full, 'wb') as f:
            f.write(b''.join(x + b'\n' for x in raw))
        runs = []  # [text, lines, times]: `lines` lines, each `text` written `times` times
        for x in raw:
            text, times = x.decode('latin-1'), 1
            if len(text) > 64 and len(set(text)) == 1:
                text, times = text[0], len(text)
            if runs and runs[-1][0] == text and runs[-1][2] == times:
                runs[-1][1] += 1
            else:
                runs.append([text, 1, times])
        self.files.append({'path': path, 'lines': runs, 'mtime': mtime})
        os.utime(full, (mtime, mtime))

    def mkdir(self, path, mtime):
        os.makedirs(os.path.join(self.root, path), exist_ok=True)
        self.dirs.append({'path': path, 'mtime': mtime})

    def db(self, path, sql):
        full = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        con = sqlite3.connect(full)
        con.executescript(';\n'.join(sql))
        con.commit()
        con.close()
        self.dbs.append({'path': path, 'sql': sql})

    def done(self):
        for d in self.dirs:  # last: writing a file inside changes its folder's time
            full = os.path.join(self.root, d['path'])
            os.utime(full, (d['mtime'], d['mtime']))
        return {'files': self.files, 'dirs': self.dirs, 'dbs': self.dbs}


# --- outputs ---------------------------------------------------------------------------------------------------------

def t(x):
    return None if x is None else x.timestamp()


def session(s):
    return {'agent': s.agent, 'source': s.source, 'calls': s.calls, 'tokens': s.tokens, 'output': s.output,
            'peak': s.peak, 'cwd': s.cwd,
            'turns': [[u.sender, u.channel, u.calls, u.tokens, u.text, t(u.ts), u.changed, u.branch] for u in s.turns],
            'refusals': [[k[0], k[1], n] for k, n in s.refusals.items()], 'rule_notes': s.rule_notes,
            'samples': [[t(a), n] for a, n in s.samples],
            'jobs': [[j.id, t(j.start), t(j.end), [list(r) for r in j.runs], j.branch] for j in s.jobs],
            'failures': [[t(a), sig, n, label] for a, sig, n, label in s.failures],
            'cycles': [[k, n] for k, n in s.cycles.items()],
            'refusal_seconds': [[k, n] for k, n in s.refusal_seconds.items()]}


def agent(a):
    return {'name': a.name, 'source': a.source, 'path': a.path, 'state': a.state, 'last': t(a.last),
            'turn_start': t(a.turn_start), 'detail': a.detail,
            'pending': [[k, v[0], v[1], v[2]] for k, v in a.pending.items()], 'cmds': a.cmds,
            'sends': [[t(x), y] for x, y in a.sends], 'sent_at': t(a.sent_at), 'wakes': [[t(x), y] for x, y in a.wakes],
            'waits_on': a.waits_on, 'peers': sorted(p.name for p in a.peers), 'thread': a.thread,
            'parent_thread': a.parent_thread, 'children': [agent(c) for c in a.children], 'cwd': a.cwd,
            'asked': a.asked, 'turn_by': a.turn_by, 'ask_kind': a.ask_kind, 'ask_since': t(a.ask_since),
            'context': a.context, 'refused': [[t(x), *rest] for x, *rest in a.refused],
            'context_limit': a.context_limit, 'heavy': [[t(x), *rest] for x, *rest in a.heavy],
            'heavy_end': [[k, t(v)] for k, v in a.heavy_end.items()],
            'failures': [[t(x), sig, t(turn), label] for x, sig, turn, label in a.failures],
            'samples': [[t(x), n] for x, n in a.samples]}


def run(fn, debug=os.environ.get("DEBUG_READERS")):
    try:
        return {'ok': fn()}
    except Exception as e:  # noqa: BLE001 - recorded, the Rust must fail the same way
        if debug:
            import traceback; traceback.print_exc(limit=-6)
        return {'error': type(e).__name__}


CASES = []


def case(name, build, calls):
    root = os.path.join('fx', name)
    tree = Tree(root)
    build(tree)
    got = tree.done()
    got['name'] = name
    got['calls'] = []
    for fn, args, call in calls:
        got['calls'].append({'fn': fn, 'args': args, 'out': run(lambda: call(root, args))})
    CASES.append(got)


def limits_call(root, a):
    return limits.read(os.path.join(root, 'claude'), os.path.join(root, 'codex'), a['now'])


def claude_call(root, a):
    return [session(s) for s in adapters.claude_code(os.path.join(root, 'claude'), dt.datetime.fromtimestamp(a['since'], dt.timezone.utc),
                                                     a.get('show_text', False), user_settings=os.path.join(root, 'none.json'))]


def codex_call(root, a):
    return [session(s) for s in adapters.codex(os.path.join(root, 'codex'), dt.datetime.fromtimestamp(a['since'], dt.timezone.utc),
                                               a.get('show_text', False))]


def gemini_call(root, a):
    return [session(s) for s in adapters.gemini(os.path.join(root, 'gemini'), dt.datetime.fromtimestamp(a['since'], dt.timezone.utc),
                                                a.get('show_text', False))]


def opencode_call(root, a):
    return [session(s) for s in adapters.opencode(os.path.join(root, 'opencode.db'), dt.datetime.fromtimestamp(a['since'], dt.timezone.utc),
                                                  a.get('show_text', False))]


def box_call(root, a):
    return [{**r, 'ts': t(r['ts'])} for r in adapters.box_metrics(os.path.join(root, 'boxes.jsonl'),
                                                                     dt.datetime.fromtimestamp(a['since'], dt.timezone.utc))]


def collect_call(root, a):
    running = None if a.get('running') is None else set(a['running'])
    return [agent(x) for x in status.collect(os.path.join(root, 'claude'), os.path.join(root, 'codex'),
                                             dt.datetime.fromtimestamp(a['now'], dt.timezone.utc), a.get('hours', 12),
                                             a.get('show_text', False), running=running, rules_for=lambda cwd: [])]


def stalls_call(root, a):
    now = dt.datetime.fromtimestamp(a['now'], dt.timezone.utc)
    agents = status.collect(os.path.join(root, 'claude'), os.path.join(root, 'codex'), now, 12, False,
                            rules_for=lambda cwd: [])
    return {'stalls': status.stalls(agents, now), 'queue': status.owner_queue(agents, now),
            'warnings': detect.live_findings(agents, now)}


def actions_call(root, a):
    return [[t(x), y] for x, y in status.recent_actions(os.path.join(root, a['path']), a['source'], a.get('show_text', False),
                                                        a.get('n', 4))]


def live_actions_call(root, a):
    return live.recent_actions(os.path.join(root, a['path']), a['n'])


# --- the spike's limits cases ------------------------------------------------------------------------------------------

def codex_row(t_, primary, secondary=None, ts=None, **extra):
    return {'timestamp': ts or stamp(t_), 'type': 'event_msg',
            'payload': {'type': 'token_count', 'rate_limits': {'primary': primary, 'secondary': secondary, **extra}}}


def claude_row(t_, status_, resets, ts=None):
    return {'timestamp': ts or stamp(t_), 'type': 'system',
            'rate_limit_info': {'status': status_, 'resetsAt': resets, 'rateLimitType': 'five_hour'}}


L = {}


def lcase(name):
    def deco(fn):
        L[name] = fn
        return fn
    return deco


@lcase('codex_resets_in_seconds')
def _(p, c, x):
    p(f'{x}/a.jsonl', [codex_row(NOW - 60, {'used_percent': 50.0, 'resets_in_seconds': 900})])


@lcase('codex_over_100')
def _(p, c, x):
    p(f'{x}/a.jsonl', [codex_row(NOW - 60, {'used_percent': 150.0, 'resets_at': NOW + 60})])


@lcase('codex_int_percent_and_int_reset')
def _(p, c, x):
    p(f'{x}/a.jsonl', [codex_row(NOW - 60, {'used_percent': 72, 'resets_at': 1_800_003_600})])


@lcase('codex_bool_percent')
def _(p, c, x):
    p(f'{x}/a.jsonl', [codex_row(NOW - 60, {'used_percent': True, 'resets_at': NOW + 60})])


@lcase('codex_string_reset')
def _(p, c, x):
    p(f'{x}/a.jsonl', [codex_row(NOW - 60, {'used_percent': 10.0, 'resets_at': 'soon'})])


@lcase('codex_tie_takes_first')
def _(p, c, x):
    p(f'{x}/a.jsonl', [codex_row(NOW - 60, {'used_percent': 40.0, 'resets_at': NOW + 60},
                                 {'used_percent': 40.0, 'resets_at': NOW + 99})])


@lcase('codex_no_reset_time')
def _(p, c, x):
    p(f'{x}/a.jsonl', [codex_row(NOW - 60, {'used_percent': 20.0}, {'used_percent': 30.0, 'resets_at': None})])


@lcase('codex_nan_percent')
def _(p, c, x):
    p(f'{x}/a.jsonl', ['{"timestamp": "%s", "payload": {"type": "token_count", "rate_limits": '
                       '{"primary": {"used_percent": NaN, "resets_at": %s}, '
                       '"secondary": {"used_percent": 5.0, "resets_at": %s}}}}' % (stamp(NOW - 60), NOW + 60, NOW + 60)])


@lcase('codex_negative_and_huge')
def _(p, c, x):
    p(f'{x}/a.jsonl', [codex_row(NOW - 60, {'used_percent': -5.0, 'resets_at': NOW + 60},
                                 {'used_percent': 1e308, 'resets_at': NOW + 60})])


@lcase('codex_big_int')
def _(p, c, x):
    p(f'{x}/a.jsonl', ['{"timestamp": "%s", "payload": {"type": "token_count", "rate_limits": '
                       '{"primary": {"used_percent": 12, "resets_at": 100000000000000000000000000001}}}}' % stamp(NOW - 60)])


@lcase('codex_secondary_only')
def _(p, c, x):
    p(f'{x}/a.jsonl', [codex_row(NOW - 60, 'oops', {'used_percent': 33.0, 'resets_at': NOW + 60})])


@lcase('codex_rate_limits_not_dict')
def _(p, c, x):
    p(f'{x}/a.jsonl', [codex_row(NOW - 120, {'used_percent': 33.0, 'resets_at': NOW + 60}),
                       {'timestamp': stamp(NOW - 60), 'payload': {'type': 'token_count', 'rate_limits': [1]}},
                       {'timestamp': stamp(NOW - 30), 'payload': None}])


@lcase('codex_reset_equals_now')
def _(p, c, x):
    p(f'{x}/a.jsonl', [codex_row(NOW - 60, {'used_percent': 33.0, 'resets_at': NOW}, {'used_percent': 3.0, 'resets_at': NOW + 1})])


@lcase('newest_five_by_mtime')
def _(p, c, x):
    for i in range(7):
        p(f'{x}/f{i}.jsonl', [codex_row(NOW - 600 + (500 if i == 0 else i), {'used_percent': 10.0 + i, 'resets_at': NOW + 60})],
          mtime=NOW - 1000 + i * 10)


@lcase('junk_lines')
def _(p, c, x):
    p(f'{x}/a.jsonl', ['not json', '[1, 2]', '"str"', '{"payload": {}}',
                       {'timestamp': 12345, 'payload': {'type': 'token_count', 'rate_limits': {'primary': {'used_percent': 99.0}}}},
                       {'timestamp': 'yesterday', 'payload': {'type': 'token_count', 'rate_limits': {'primary': {'used_percent': 98.0}}}},
                       codex_row(NOW - 60, {'used_percent': 11.0, 'resets_at': NOW + 60}), '', '   ', '{"truncated": '])


@lcase('timestamps_ordering_with_offsets')
def _(p, c, x):
    p(f'{x}/a.jsonl', [codex_row(0, {'used_percent': 61.0, 'resets_at': NOW + 60}, ts='2027-01-15T09:00:00+02:00'),
                       codex_row(0, {'used_percent': 62.0, 'resets_at': NOW + 60}, ts='2027-01-15T08:00:00Z')])


@lcase('naive_timestamp_is_utc')
def _(p, c, x):
    p(f'{x}/a.jsonl', [codex_row(0, {'used_percent': 63.0, 'resets_at': NOW + 60}, ts='2027-01-15T08:00:01'),
                       codex_row(0, {'used_percent': 64.0, 'resets_at': NOW + 60}, ts='2027-01-15T08:00:00Z')])


ODD_TS = ['2027-01-15', '2027-01-15 08:00:00', '20270115T080000Z', '2027-01-15T08:00Z', '2027-01-15T08Z',
          '2027-01-15T08:00:00,5Z', '2027-W03-5', '2027-01-15T08:00:00.123456789Z', '2027-01-15T08:00:00.1234567Z',
          '2027-01-15T08:00:00+0200', '2027-01-15T08:00:00+02', '2027-01-15T24:00:00Z', '2027-01-15T08:00:00.000-00:00',
          '2027-01-15T08:00:00.000z', '+2027-01-15T08:00:00Z', '2027-1-15T08:00:00Z', '  2027-01-15T08:00:00Z',
          '2027-02-29T08:00:00Z', '2027-01-15T08:00:60Z', '2027-01-15T08:00:00.Z', '２０２７-01-15T08:00:00Z']
for _i, _ts in enumerate(ODD_TS):
    def _(p, c, x, ts=_ts):
        p(f'{x}/a.jsonl', [codex_row(0, {'used_percent': 70.0, 'resets_at': NOW + 60}, ts=ts)])
    L[f'odd_ts_{_i:02d}'] = _


@lcase('hidden_folders_and_files')
def _(p, c, x):
    p(f'{x}/.hidden/a.jsonl', [codex_row(NOW - 60, {'used_percent': 90.0, 'resets_at': NOW + 60})])
    p(f'{x}/.a.jsonl', [codex_row(NOW - 60, {'used_percent': 91.0, 'resets_at': NOW + 60})])
    p(f'{c}/.p/s.jsonl', [claude_row(NOW - 60, 'rejected', NOW + 60)])
    p(f'{c}/p/.s.jsonl', [claude_row(NOW - 60, 'rejected', NOW + 60)])


@lcase('claude_depth')
def _(p, c, x):
    p(f'{c}/top.jsonl', [claude_row(NOW - 60, 'rejected', NOW + 60)])
    p(f'{c}/p/q/deep.jsonl', [claude_row(NOW - 60, 'rejected', NOW + 60)])
    p(f'{x}/1/2/3/4/deep.jsonl', [codex_row(NOW - 60, {'used_percent': 12.0, 'resets_at': NOW + 60})])


@lcase('tail_cuts_first_line')
def _(p, c, x):
    pad = {'timestamp': stamp(NOW - 500), 'pad': 'p' * 1000}
    p(f'{x}/a.jsonl', [codex_row(NOW - 30, {'used_percent': 99.0, 'resets_at': NOW + 60})]
      + [pad] * 140 + [codex_row(NOW - 400, {'used_percent': 14.0, 'resets_at': NOW + 60})])


@lcase('tail_exact_size')
def _(p, c, x):
    first = json.dumps(codex_row(NOW - 30, {'used_percent': 15.0, 'resets_at': NOW + 60}))
    p(f'{x}/a.jsonl', [first, 'x' * (128 * 1024 - len(first) - 2)])


@lcase('tail_one_over')
def _(p, c, x):
    first = json.dumps(codex_row(NOW - 30, {'used_percent': 16.0, 'resets_at': NOW + 60}))
    p(f'{x}/a.jsonl', [first, 'x' * (128 * 1024 - len(first) - 1)])


@lcase('bad_utf8_and_bom')
def _(p, c, x):
    p(f'{x}/a.jsonl', [b'\xef\xbb\xbf' + json.dumps(codex_row(NOW - 10, {'used_percent': 17.0, 'resets_at': NOW + 60})).encode(),
                       json.dumps(codex_row(NOW - 60, {'used_percent': 18.0, 'resets_at': NOW + 60})).encode()[:-1] + b'\xff}',
                       json.dumps(codex_row(NOW - 90, {'used_percent': 19.0, 'resets_at': NOW + 60})).encode()])


@lcase('unicode_line_separators')
def _(p, c, x):
    a = json.dumps(codex_row(NOW - 60, {'used_percent': 20.0, 'resets_at': NOW + 60}), ensure_ascii=False)
    b = json.dumps(codex_row(NOW - 30, {'used_percent': 21.0, 'resets_at': NOW + 60}), ensure_ascii=False)
    p(f'{x}/a.jsonl', [(a + ' ' + b).encode(), a + '\x1c' + b, a + '\r' + b])


@lcase('duplicate_keys')
def _(p, c, x):
    p(f'{x}/a.jsonl', ['{"timestamp": "%s", "payload": {"type": "token_count", "rate_limits": '
                       '{"primary": {"used_percent": 22.0, "used_percent": 23.0, "resets_at": %s}}}}' % (stamp(NOW - 60), NOW + 60)])


@lcase('same_timestamp_two_files')
def _(p, c, x):
    p(f'{x}/old.jsonl', [codex_row(NOW - 60, {'used_percent': 24.0, 'resets_at': NOW + 60})], mtime=NOW - 500)
    p(f'{x}/new.jsonl', [codex_row(NOW - 60, {'used_percent': 25.0, 'resets_at': NOW + 60})], mtime=NOW - 100)


@lcase('claude_reset_types')
def _(p, c, x):
    p(f'{c}/a/s.jsonl', [claude_row(NOW - 60, 'rejected', '1800001800')])
    p(f'{c}/b/s.jsonl', [claude_row(NOW - 50, 'rejected', True)])


@lcase('claude_int_reset')
def _(p, c, x):
    p(f'{c}/a/s.jsonl', [claude_row(NOW - 60, 'rejected', 1_800_001_800)])


@lcase('claude_info_not_dict_later')
def _(p, c, x):
    p(f'{c}/a/s.jsonl', [claude_row(NOW - 60, 'rejected', NOW + 60),
                         {'timestamp': stamp(NOW - 30), 'rate_limit_info': 'allowed'},
                         {'timestamp': stamp(NOW - 20), 'rate_limit_info': None}])


@lcase('claude_other_status_and_untimed')
def _(p, c, x):
    p(f'{c}/a/s.jsonl', [claude_row(NOW - 60, 'rejected', NOW + 60), {'rate_limit_info': {'status': 'allowed'}}])


@lcase('claude_allowed_later')
def _(p, c, x):
    p(f'{c}/a/s.jsonl', [claude_row(NOW - 60, 'rejected', NOW + 60), claude_row(NOW - 30, 'allowed', NOW + 60)])


@lcase('both_kinds')
def _(p, c, x):
    p(f'{c}/a/s.jsonl', [claude_row(NOW - 60, 'rejected', NOW + 60)])
    p(f'{x}/a.jsonl', [codex_row(NOW - 60, {'used_percent': 26.0, 'resets_at': NOW + 60})])


@lcase('codex_payload_not_dict')
def _(p, c, x):
    p(f'{x}/a.jsonl', [codex_row(NOW - 60, {'used_percent': 27.0, 'resets_at': NOW + 60}),
                       {'timestamp': stamp(NOW - 30), 'payload': 'text'}])


@lcase('codex_payload_falsy')
def _(p, c, x):
    p(f'{x}/a.jsonl', [codex_row(NOW - 60, {'used_percent': 28.0, 'resets_at': NOW + 60}),
                       {'timestamp': stamp(NOW - 30), 'payload': 0}, {'timestamp': stamp(NOW - 20), 'payload': []}])


@lcase('codex_claude_error_order')
def _(p, c, x):
    p(f'{c}/a/s.jsonl', [claude_row(NOW - 60, 'rejected', NOW + 60)])
    p(f'{x}/a.jsonl', [codex_row(NOW - 60, {'used_percent': 29.0, 'resets_at': 'later'})])


@lcase('missing_folders')
def _(p, c, x):
    pass


for _name, _fn in L.items():
    def build(tree, fn=_fn):
        fn(tree.put, 'claude', 'codex')
    calls = [('limits.read', {'now': NOW}, limits_call)]
    if _name in ('codex_reset_equals_now', 'claude_int_reset'):
        calls.append(('limits.read', {'now': NOW - 0.5}, limits_call))
    case(f'limits_{_name}', build, calls)


def _folder_named_jsonl(tree):  # a folder matching *.jsonl is listed, ranks by its time, and reads as nothing
    tree.mkdir('codex/dir.jsonl/inner', NOW - 5)
    tree.mkdir('codex/dir.jsonl', NOW - 5)
    for i in range(5):
        tree.put(f'codex/f{i}.jsonl', [codex_row(NOW - 60, {'used_percent': 30.0 + i, 'resets_at': NOW + 60})], mtime=NOW - 100 + i)


case('limits_folder_named_jsonl', _folder_named_jsonl, [('limits.read', {'now': NOW}, limits_call)])


# --- Claude Code and Codex logs ------------------------------------------------------------------------------------------

PROJ = re_home = '-'.join(['', *HOME.strip('/').split('/')])  # Claude Code's folder name for the home folder
TEAM = f'{PROJ}-src-team'
ALL_CALLS = []


def cl_user(ts, text, cwd='/w/team-a', **extra):
    return {'type': 'user', 'timestamp': stamp(ts), 'cwd': cwd, 'gitBranch': extra.pop('branch', 'feat'),
            'message': {'role': 'user', 'content': text}, **extra}


def cl_result(ts, tool_id, content, is_error=False, cwd='/w/team-a'):
    return {'type': 'user', 'timestamp': stamp(ts), 'cwd': cwd, 'gitBranch': 'feat',
            'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': tool_id, 'content': content,
                                                      'is_error': is_error}]}}


def cl_asst(ts, blocks, stop=None, usage=(1000, 0, 0, 10), mid=None, cwd='/w/team-a'):
    inp, read, made, out = usage
    return {'type': 'assistant', 'timestamp': stamp(ts), 'cwd': cwd, 'gitBranch': 'feat',
            'message': {'id': mid or f'm{ts}', 'role': 'assistant', 'stop_reason': stop, 'content': blocks,
                        'usage': {'input_tokens': inp, 'cache_read_input_tokens': read,
                                  'cache_creation_input_tokens': made, 'output_tokens': out}}}


def bash(i, cmd):
    return {'type': 'tool_use', 'id': i, 'name': 'Bash', 'input': {'command': cmd}}


def tool(i, name, **inp):
    return {'type': 'tool_use', 'id': i, 'name': name, 'input': inp}


def text(s):
    return {'type': 'text', 'text': s}


def cx_meta(thread, cwd, parent=None, other=None, branch='feat'):
    src = {'subagent': {'thread_spawn': {'parent_thread_id': parent}}} if parent else (
        {'subagent': {'other': other}} if other else 'cli')
    return {'type': 'session_meta', 'timestamp': stamp(NOW - 3000),
            'payload': {'id': thread, 'cwd': cwd, 'source': src, 'git': {'branch': branch}}}


def cx(ts, payload, type_='response_item'):
    return {'type': type_, 'timestamp': stamp(ts), 'payload': payload}


def cx_user(ts, s):
    return cx(ts, {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': s}]})


def cx_asst(ts, s):
    return cx(ts, {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': s}]})


def cx_call(ts, call, cmd, name='exec_command'):
    return cx(ts, {'type': 'function_call', 'name': name, 'call_id': call, 'arguments': json.dumps({'cmd': cmd})})


def cx_out(ts, call, out):
    return cx(ts, {'type': 'function_call_output', 'call_id': call, 'output': out})


def cx_tokens(ts, inp, total, window=258400, out=50):
    return cx(ts, {'type': 'token_count', 'info': {'last_token_usage': {'input_tokens': inp, 'output_tokens': out},
                                                   'total_token_usage': {'total_tokens': total},
                                                   'model_context_window': window}}, 'event_msg')


def cx_event(ts, kind):
    return cx(ts, {'type': kind}, 'event_msg')


def logs_case(name, build, extra=()):
    since = NOW - 12 * 3600
    calls = [('adapters.claude_code', {'since': since}, claude_call),
             ('adapters.claude_code', {'since': since, 'show_text': True}, claude_call),
             ('adapters.codex', {'since': since}, codex_call),
             ('adapters.codex', {'since': since, 'show_text': True}, codex_call),
             ('status.collect', {'now': NOW}, collect_call),
             ('status.collect', {'now': NOW, 'show_text': True}, collect_call),
             ('status.collect', {'now': NOW + 1800, 'running': []}, collect_call),
             ('status.stalls', {'now': NOW}, stalls_call),
             ('status.stalls', {'now': NOW + 1800}, stalls_call), *extra]
    case(name, build, calls)


def _team(tree):
    """Two Claude Code agents and a reviewer messaging through asm; a Codex agent with a subagent."""
    a, b, r = f'claude/{TEAM}-a', f'claude/{TEAM}-b', f'claude/{PROJ}-src-other-reviewer'
    tree.put(f'{a}/s1.jsonl', [
        cl_user(NOW - 4000, 'Build the parser', permissionMode='default'),
        cl_asst(NOW - 3990, [text('On it.'), bash('t1', 'cargo test')], 'tool_use', (5000, 20000, 1000, 50)),
        cl_result(NOW - 3700, 't1', 'error: test failed at src/lib.rs:12\nthread main panicked: assertion failed left 3 right 4', True),
        cl_asst(NOW - 3690, [bash('t2', 'asm send b "please review"')], 'tool_use', (5000, 26000, 1000, 50)),
        cl_result(NOW - 3680, 't2', 'sent'),
        cl_asst(NOW - 3670, [text('Sent it to b.')], 'end_turn', (5000, 27000, 0, 30)),
    ], mtime=NOW - 3670)
    tree.put(f'{a}/s0.jsonl', [cl_user(NOW - 9000, 'old session'), cl_asst(NOW - 8990, [text('ok')], 'end_turn')],
             mtime=NOW - 8990)
    tree.put(f'{b}/s.jsonl', [
        cl_user(NOW - 3600, '<asm from="team a">please review</asm>'),
        cl_asst(NOW - 3590, [bash('u1', 'git diff main'), bash('u2', 'pytest')], 'tool_use', (900, 100, 0, 5)),
        cl_result(NOW - 3500, 'u1', 'diff'),
        cl_asst(NOW - 3400, [tool('u3', 'Edit', file_path='/w/team-b/src/x.py')], 'tool_use', (950, 100, 0, 5)),
        cl_result(NOW - 3390, 'u3', 'ok'),
        cl_asst(NOW - 3380, [bash('u4', 'asm wait a')], 'tool_use', (960, 100, 0, 5)),
    ], mtime=NOW - 3380)
    tree.put(f'{b}/s/subagents/agent-1.jsonl', [
        cl_user(NOW - 3450, 'Look at the tests', cwd='/w/team-b'),
        cl_asst(NOW - 3440, [bash('v1', 'ls')], 'tool_use', cwd='/w/team-b'),
    ], mtime=NOW - 3440)
    tree.put(f'{b}/s/subagents/agent-1.meta.json', [{'agentType': 'Explore'}], mtime=NOW - 3440)
    tree.put(f'{r}/s.jsonl', [
        cl_user(NOW - 600, 'What should I check first', cwd='/w/other'),
        cl_asst(NOW - 590, [text('Shall I start with the parser?')], 'end_turn', (300000, 0, 0, 20), cwd='/w/other'),
    ], mtime=NOW - 590)
    tree.put('codex/2027/01/15/rollout-1.jsonl', [
        cx_meta('th1', '/w/codexer'),
        cx_user(NOW - 2000, '<environment_context>cwd</environment_context>'),
        cx_user(NOW - 1990, 'Fix the flaky test'),
        cx_event(NOW - 1990, 'task_started'),
        cx_tokens(NOW - 1980, 40000, 40100),
        cx_tokens(NOW - 1979, 40000, 40100),
        cx_call(NOW - 1970, 'c1', ['bash', '-lc', 'npm test']),
        cx_out(NOW - 1900, 'c1', 'Process exited with code 1\nFAIL src/a.test.ts\nExpected 3 to equal 4 in the flaky suite'),
        cx_call(NOW - 1890, 'c2', 'git push'),
        cx_out(NOW - 1880, 'c2', 'sandbox_apply: Operation not permitted'),
        cx_tokens(NOW - 1870, 52000, 92200),
        cx_asst(NOW - 1860, 'Should I skip it?'),
        cx_event(NOW - 1850, 'task_complete'),
    ], mtime=NOW - 1850)
    tree.put('codex/2027/01/15/rollout-2.jsonl', [
        cx_meta('th2', '/w/codexer', parent='th1'),
        cx_user(NOW - 1960, 'Check the logs'),
        cx_event(NOW - 1960, 'task_started'),
        cx_tokens(NOW - 1950, 3000, 3100),
        cx_call(NOW - 1940, 'd1', 'sleep 1'),
    ], mtime=NOW - 1940)
    tree.put('codex/2027/01/15/rollout-3.jsonl', [
        cx_meta('th3', '/w/codexer', other='guardian'),
        cx_user(NOW - 1000, 'review'),
        cx_tokens(NOW - 990, 1000, 1100),
        cx_event(NOW - 980, 'task_complete'),
    ], mtime=NOW - 980)


logs_case('team', _team, [
    ('status.recent_actions', {'path': f'claude/{TEAM}-a/s1.jsonl', 'source': 'claude-code'}, actions_call),
    ('status.recent_actions', {'path': f'claude/{TEAM}-b/s.jsonl', 'source': 'claude-code', 'show_text': True, 'n': 10},
     actions_call),
    ('status.recent_actions', {'path': 'codex/2027/01/15/rollout-1.jsonl', 'source': 'codex', 'n': 10}, actions_call),
    ('status.recent_actions', {'path': 'codex/missing.jsonl', 'source': 'codex'}, actions_call),
])


def _states(tree):
    """One project per health state."""
    p = f'claude/{PROJ}-st'
    tree.put(f'{p}-working/s.jsonl', [cl_user(NOW - 300, 'go', cwd='/w/working'),
                                      cl_asst(NOW - 60, [bash('a', 'make build')], 'tool_use', cwd='/w/working')])
    tree.put(f'{p}-thinking/s.jsonl', [cl_user(NOW - 30, 'go', cwd='/w/thinking')])
    tree.put(f'{p}-silent/s.jsonl', [cl_user(NOW - 4000, 'go', cwd='/w/silent'),
                                     cl_asst(NOW - 3990, [bash('a', 'npm run build -- --watch')], 'tool_use', cwd='/w/silent')])
    tree.put(f'{p}-noreply/s.jsonl', [cl_user(NOW - 9000, 'go', cwd='/w/noreply')])
    tree.put(f'{p}-repeat/s.jsonl', [cl_user(NOW - 300, 'go', cwd='/w/repeat')] + [
        x for i in range(4) for x in (cl_asst(NOW - 200 + i, [bash(f'r{i}', 'curl localhost:8080')], 'tool_use', cwd='/w/repeat'),
                                      cl_result(NOW - 199 + i, f'r{i}', 'refused', True, cwd='/w/repeat'))]
             + [cl_asst(NOW - 100, [bash('r9', 'curl localhost:8080')], 'tool_use', cwd='/w/repeat')])
    tree.put(f'{p}-ask/s.jsonl', [cl_user(NOW - 300, 'plan it', cwd='/w/ask'),
                                  cl_asst(NOW - 250, [tool('q', 'ExitPlanMode', plan='x')], 'tool_use', cwd='/w/ask')])
    tree.put(f'{p}-question/s.jsonl', [cl_user(NOW - 300, 'go', cwd='/w/question'),
                                       cl_asst(NOW - 250, [tool('q', 'AskUserQuestion', questions=[])], 'tool_use', cwd='/w/question')])
    tree.put(f'{p}-waiting/s.jsonl', [cl_user(NOW - 4000, 'go', cwd='/w/waiting'),
                                      cl_asst(NOW - 3990, [bash('w', 'hc wait builder --timeout 60')], 'tool_use', cwd='/w/waiting')])
    tree.put(f'{p}-interrupted/s.jsonl', [cl_user(NOW - 300, 'go', cwd='/w/interrupted'),
                                          cl_asst(NOW - 250, [bash('i', 'sleep 100')], 'tool_use', cwd='/w/interrupted'),
                                          cl_user(NOW - 240, [text('[Request interrupted by user for tool use]')], cwd='/w/interrupted')])
    tree.put(f'{p}-compact/s.jsonl', [cl_user(NOW - 300, 'go', cwd='/w/compact'),
                                      cl_asst(NOW - 250, [text('done')], 'end_turn', (180000, 0, 0, 1), cwd='/w/compact'),
                                      {'type': 'system', 'subtype': 'compact_boundary'}])
    tree.put(f'{p}-meta/s.jsonl', [cl_user(NOW - 300, 'go', cwd='/w/meta'),
                                   cl_asst(NOW - 250, [text('Done?')], 'end_turn', cwd='/w/meta'),
                                   cl_user(NOW - 240, 'caveat', cwd='/w/meta', isMeta=True)])
    tree.put(f'{p}-damaged/s.jsonl', [cl_user(NOW - 300, 'go', cwd='/w/damaged'),
                                      {'type': 'assistant', 'timestamp': stamp(NOW - 250), 'message': 'text'}])
    tree.put(f'{p}-old/s.jsonl', [cl_user(NOW - 300, 'go')], mtime=NOW - 13 * 3600)
    tree.put(f'{p}-notime/s.jsonl', [{'type': 'user', 'message': {'content': 'x'}}])
    tree.put(f'{p}-heavy/s.jsonl', [cl_user(NOW - 3000, 'go', cwd='/w/heavy')] + [
        x for i in range(4) for x in (cl_asst(NOW - 2900 + i * 300, [bash(f'h{i}', 'uv run pytest -q')], 'tool_use', cwd='/w/heavy'),
                                      cl_result(NOW - 2800 + i * 300, f'h{i}', 'ok', cwd='/w/heavy'))]
             + [cl_asst(NOW - 1000, [text('Tests pass.')], 'end_turn', cwd='/w/heavy')])
    tree.put('codex/2027/01/15/rollout-a.jsonl', [
        cx_meta('t-approve', '/w/approve'), cx_user(NOW - 300, 'go'), cx_event(NOW - 300, 'task_started'),
        cx(NOW - 250, {'type': 'exec_approval_request'}, 'event_msg')])
    tree.put('codex/2027/01/15/rollout-b.jsonl', [
        cx_meta('t-compacted', '/w/compacted'), cx_user(NOW - 300, 'go'), cx_event(NOW - 300, 'task_started'),
        cx_tokens(NOW - 290, 90000, 90100), {'type': 'compacted', 'timestamp': stamp(NOW - 280), 'payload': {}},
        cx(NOW - 270, {'type': 'custom_tool_call', 'name': 'apply_patch', 'call_id': 'p', 'input': '*** Begin Patch'}),
        cx(NOW - 260, {'type': 'custom_tool_call_output', 'call_id': 'p',
                       'output': [{'type': 'text', 'text': 'Success'}]}),
        cx_event(NOW - 250, 'turn_aborted')])
    tree.put('codex/2027/01/15/rollout-c.jsonl', [
        cx_meta('t-old', '/w/approve'), cx_user(NOW - 900, 'older run of the same folder'),
        cx_event(NOW - 900, 'task_started')], mtime=NOW - 900)
    tree.put('codex/2027/01/15/rollout-d.jsonl', [
        cx_meta('t-exec', '/w/exec'), cx_user(NOW - 300, 'go'), cx_event(NOW - 300, 'task_started'),
        cx(NOW - 290, {'type': 'function_call', 'name': 'exec', 'call_id': 'e',
                       'arguments': 'await tools.exec_command({cmd: "cargo test\\n", yield_time_ms: 1000})'}),
        cx_out(NOW - 200, 'e', 'Script failed\nerror[E0308]: mismatched types in src/main.rs line 12 col 4'),
        cx(NOW - 190, {'type': 'function_call', 'name': 'exec_command', 'call_id': 'f', 'arguments': '{not json'}),
        cx(NOW - 180, {'type': 'function_call', 'name': 'shell', 'call_id': 'g', 'arguments': {'command': ['git', 'status']}})])
    tree.put('codex/2027/01/15/rollout-e.jsonl', [
        cx_meta('t-input', '/w/input'), cx_user(NOW - 300, 'go'), cx_event(NOW - 300, 'task_started'),
        cx(NOW - 250, {'type': 'request_user_input'}, 'event_msg')])
    tree.put('codex/2027/01/15/rollout-f.jsonl', [b'{"timestamp": "bad', cx_user(NOW - 300, 'go')])  # first row damaged


logs_case('states', _states, [
    ('status.collect', {'now': NOW, 'hours': 0.05}, collect_call),
    ('status.recent_actions', {'path': f'claude/{PROJ}-st-repeat/s.jsonl', 'source': 'claude-code', 'n': 3}, actions_call),
    ('status.recent_actions', {'path': 'codex/2027/01/15/rollout-d.jsonl', 'source': 'codex', 'show_text': True}, actions_call),
    ('status.recent_actions', {'path': 'codex/2027/01/15/rollout-b.jsonl', 'source': 'codex'}, actions_call),
])


def _work_waiting(tree):
    """A message to an idle agent that started no turn; two agents waiting on each other; a report, not a request."""
    p = f'claude/{PROJ}-ww'
    tree.put(f'{p}-lead/s.jsonl', [cl_user(NOW - 1000, 'go', cwd='/w/lead'),
                                   cl_asst(NOW - 990, [bash('s', 'asm send --if idle builder "do it"')], 'tool_use', cwd='/w/lead'),
                                   cl_result(NOW - 985, 's', 'ok', cwd='/w/lead'),
                                   cl_asst(NOW - 980, [text('Sent.')], 'end_turn', cwd='/w/lead')])
    tree.put(f'{p}-builder/s.jsonl', [cl_user(NOW - 2000, 'go', cwd='/w/builder'),
                                      cl_asst(NOW - 1990, [text('Done.')], 'end_turn', cwd='/w/builder')])
    tree.put(f'{p}-x/s.jsonl', [cl_user(NOW - 1000, '<asm from="y">ping</asm>', cwd='/w/x'),
                                cl_asst(NOW - 990, [bash('s', 'asm send y "ping back" && echo ok')], 'tool_use', cwd='/w/x'),
                                cl_result(NOW - 985, 's', 'ok', cwd='/w/x'),
                                cl_asst(NOW - 980, [text('Asked y.')], 'end_turn', cwd='/w/x')])
    tree.put(f'{p}-y/s.jsonl', [cl_user(NOW - 1100, 'go', cwd='/w/y'),
                                cl_asst(NOW - 1090, [bash('s', "asm send x 'ping'")], 'tool_use', cwd='/w/y'),
                                cl_result(NOW - 1085, 's', 'ok', cwd='/w/y'),
                                cl_asst(NOW - 1080, [text('Asked x.')], 'end_turn', cwd='/w/y'),
                                cl_user(NOW - 970, '<teammate-message teammate_id="x">ping back</teammate-message>', cwd='/w/y'),
                                cl_asst(NOW - 960, [bash('s2', 'asm send x "thanks"')], 'tool_use', cwd='/w/y'),
                                cl_result(NOW - 955, 's2', 'ok', cwd='/w/y'),
                                cl_asst(NOW - 950, [text('Thanked x.')], 'end_turn', cwd='/w/y')])
    tree.put(f'{p}-q/s.jsonl', [cl_user(NOW - 1000, 'go', cwd='/w/q'),
                                cl_asst(NOW - 990, [bash('s', 'asm send r "help"')], 'tool_use', cwd='/w/q'),
                                cl_result(NOW - 985, 's', 'ok', cwd='/w/q'),
                                cl_asst(NOW - 980, [text('Asked r.')], 'end_turn', cwd='/w/q')])
    tree.put(f'{p}-r/s.jsonl', [cl_user(NOW - 1000, '<hc from="term q">help</hc>', cwd='/w/r'),
                                cl_asst(NOW - 990, [tool('k', 'AskUserQuestion')], 'tool_use', cwd='/w/r')])


logs_case('work_waiting', _work_waiting)


def _damaged(tree):
    """Odd rows the readers must survive: a cut first line past the tail, a BOM, bad UTF-8, U+2028 line ends."""
    p = f'claude/{PROJ}-dm'
    pad = json.dumps({'type': 'progress', 'timestamp': stamp(NOW - 5000), 'pad': 'p' * 2000})
    tree.put(f'{p}-big/s.jsonl', [json.dumps(cl_user(NOW - 6000, 'cut off', cwd='/w/big'))] + [pad] * 300
             + [cl_asst(NOW - 100, [bash('a', 'ls')], 'tool_use', cwd='/w/big')])
    tree.put(f'{p}-bom/s.jsonl', [b'\xef\xbb\xbf' + json.dumps(cl_user(NOW - 100, 'bom', cwd='/w/bom')).encode(),
                                  json.dumps(cl_asst(NOW - 90, [text('ok \xff')], 'end_turn', cwd='/w/bom')).encode()[:-1] + b'\xff}',
                                  (json.dumps(cl_user(NOW - 80, 'a', cwd='/w/bom'), ensure_ascii=False) + ' '
                                   + json.dumps(cl_asst(NOW - 70, [text('b')], 'end_turn', cwd='/w/bom'))).encode()])
    tree.put(f'{p}-types/s.jsonl', [cl_user(NOW - 100, ['x', 1, text('typed'), {'type': 'image'}], cwd='/w/types'),
                                    {'type': 'assistant', 'timestamp': stamp(NOW - 90), 'message': {
                                        'content': 'plain', 'stop_reason': 'end_turn', 'usage': {}}},
                                    {'type': 'assistant', 'timestamp': stamp(NOW - 80), 'message': {
                                        'content': [1, None, {'type': 'tool_use', 'name': 'Read', 'id': 'r'}],
                                        'stop_reason': 'tool_use', 'usage': {'input_tokens': 7}}}])
    tree.put('codex/x/rollout.jsonl', [cx_meta('t', None), cx(NOW - 100, None),
                                       cx(NOW - 90, {'type': 'message', 'role': 'user', 'content': None}),
                                       cx(NOW - 80, {'type': 'function_call', 'name': None, 'call_id': None, 'arguments': 7})])


logs_case('damaged', _damaged)


def _gemini(tree):
    tree.put('gemini/projects.json', [{'projects': {'/w/gem': 'gem-1'}}])
    tree.put('gemini/tmp/gem-1/chats/session-1.jsonl', [
        {'sessionId': 's', 'projectHash': 'h'},
        {'id': 'u1', 'type': 'user', 'timestamp': stamp(NOW - 300), 'content': [{'text': 'hello'}, {'text': 5}]},
        {'id': 'g1', 'type': 'gemini', 'timestamp': stamp(NOW - 290), 'content': 'hi'},
        {'id': 'g1', 'type': 'gemini', 'timestamp': stamp(NOW - 290), 'content': 'hi',
         'tokens': {'input': 1200, 'output': 30, 'thoughts': 5}},
        {'id': 'u2', 'type': 'user', 'timestamp': stamp(NOW - 200), 'content': '<asm from="lead">next</asm>'},
        {'id': 'g2', 'type': 'gemini', 'timestamp': stamp(NOW - 190), 'tokens': {'input': 1500, 'output': None}},
        {'id': 'u0', 'type': 'user', 'timestamp': stamp(NOW - 50 * 3600), 'content': 'too old'},
    ])
    tree.put('gemini/tmp/unknown/chats/session-2.jsonl', [
        {'id': 'u1', 'type': 'user', 'timestamp': stamp(NOW - 300), 'content': 'x'},
        {'id': 'g1', 'type': 'gemini', 'timestamp': stamp(NOW - 290), 'tokens': {'input': 10}}])
    tree.put('boxes.jsonl', [{'ts': stamp(NOW - 7200), 'box': 'gpu1', 'cpu': 10, 'rate': 5, 'usd_per_hour': 2.5},
                             {'ts': stamp(NOW - 100), 'box': 'gpu1', 'cpu': 12, 'rate': 6, 'usd_per_hour': 2.5},
                             {'ts': stamp(NOW - 100), 'box': 7, 'cpu': 50},
                             {'ts': stamp(NOW - 100), 'box': '', 'cpu': 1},
                             {'ts': stamp(NOW - 9 * 3600), 'box': 'old'}, 'junk', [1]])
    tree.put('actions.jsonl', [{'id': str(i), 'action': 'nudge', 'ok': True} for i in range(7)] + ['junk', '[1]'])
    tree.db('opencode.db', [
        'CREATE TABLE session (id TEXT, directory TEXT, parent_id TEXT, time_updated INTEGER)',
        'CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT)',
        'CREATE TABLE part (id TEXT, message_id TEXT, time_created INTEGER, data TEXT)',
        f"INSERT INTO session VALUES ('s1', '/w/oc', NULL, {int(NOW * 1000) - 1000})",
        f"INSERT INTO session VALUES ('s2', '/w/oc', 's1', {int(NOW * 1000) - 500})",
        f"INSERT INTO session VALUES ('s3', '/w/oc', NULL, {int((NOW - 99 * 3600) * 1000)})",
        f"INSERT INTO message VALUES ('m1', 's1', {int(NOW * 1000) - 900}, '{{\"role\": \"user\"}}')",
        f"INSERT INTO part VALUES ('p1', 'm1', 1, '{{\"type\": \"text\", \"text\": \"hello there\"}}')",
        f"INSERT INTO part VALUES ('p2', 'm1', 2, '{{\"type\": \"file\"}}')",
        f"INSERT INTO message VALUES ('m2', 's1', {int(NOW * 1000) - 800}, "
        "'{\"role\": \"assistant\", \"tokens\": {\"input\": 100, \"output\": 20, \"reasoning\": 3, \"cache\": {\"read\": 400, \"write\": 5}}}')",
        f"INSERT INTO message VALUES ('m3', 's1', {int(NOW * 1000) - 700}, 'not json')",
        f"INSERT INTO message VALUES ('m4', 's2', {int(NOW * 1000) - 400}, '{{\"role\": \"user\"}}')",
        f"INSERT INTO message VALUES ('m5', 's2', {int(NOW * 1000) - 300}, '{{\"role\": \"assistant\", \"tokens\": {{\"input\": 9}}}}')",
    ])


case('other_tools', _gemini, [
    ('adapters.gemini', {'since': NOW - 12 * 3600}, gemini_call),
    ('adapters.gemini', {'since': NOW - 12 * 3600, 'show_text': True}, gemini_call),
    ('adapters.opencode', {'since': NOW - 12 * 3600, 'show_text': True}, opencode_call),
    ('adapters.box_metrics', {'since': NOW - 3 * 3600}, box_call),
    ('live.recent_actions', {'path': 'actions.jsonl', 'n': 5}, live_actions_call),
    ('live.recent_actions', {'path': 'actions.jsonl', 'n': 50}, live_actions_call),
    ('live.recent_actions', {'path': 'missing.jsonl', 'n': 5}, live_actions_call),
])

# --- detect's command readers -------------------------------------------------------------------------------------

COMMANDS = [
    '', 'ls -la', 'pytest', 'pytest tests/test_a.py', 'pytest -k parser', 'pytest -q', 'pytest tests', 'pytest -x 2>&1 | tail',
    'uv run pytest', 'uv run --with x pytest -q', 'python -m pytest', 'python3.12 -m unittest', 'npx vitest run',
    'cargo test', 'cargo test -p brain', 'cargo test --package brain', 'cargo test -- --nocapture', 'cargo build --release',
    'cargo tauri build', 'go test ./...', 'go test ./pkg/x', 'npm test', 'npm run test -- --watch', 'npm run build',
    'pnpm build', 'yarn test --testNamePattern foo', 'bun test', 'make test', 'make check', 'make', 'xcodebuild test -scheme X',
    'xcodebuild -scheme X', 'docker build .', 'gh workflow run ci.yml', 'gh run rerun 1', 'codex review', 'coderabbit review',
    'gh pr review 1 --approve', 'timeout 60 pytest', 'timeout -s KILL 5m cargo test', 'FOO=1 BAR=2 pytest', 'env FOO=1 pytest',
    'sudo -u x make build', 'nice -n 10 cargo build', 'caffeinate -i npm test', 'pnpm exec vitest', 'poetry run pytest -k x',
    'cd app && npm test && npm run build', 'git status; cargo test', 'echo "pytest" | cat', "bash -c 'pytest'",
    'cat <<EOF > x\npytest\nEOF\ncargo test', 'cat <<-\'END\'\n\tmake test\n\tEND', 'echo "unterminated', 'a && (b || c)',
    'tsc --noEmit', 'vite build', 'next build', 'swift test --filter X', 'dotnet test', 'mix test', 'jest src/a.test.ts',
    'mvn test', './gradlew test', 'gradlew build', 'git commit -m "fix: the parser"', 'git commit -am "Add x"',
    'git commit --message="hotfix crash"', 'git commit -m "$(cat <<\'EOF\'\nFix the loop\nEOF\n)"', 'git log', 'git push',
    'gh pr view 1', 'gh pr create', 'sed -i s/a/b/ x', 'sed s/a/b/ x', 'echo hi > out.txt', 'grep x 2>/dev/null',
    'rg foo | head', 'asm send b hi', 'hc wait builder', 'cargo test 1>&2', 'pytest 0', 'pytest .', 'pytest > log.txt',
    'API_KEY=secret ./deploy.sh', '/usr/bin/env python script.py', 'pytest\ncargo build',
    'await tools.exec_command({cmd: "cargo test\\n"}); await tools.exec_command({cmd: \'npm run build\'})',
    'tools.exec_command({cmd: `make test`})', 'tools.x(y)',
]

FAILURES = [
    '', 'short', 'error: test failed at src/lib.rs:12\nthread main panicked',
    'Exit code: 1\nWall time: 3s\nOutput:\nFAILED tests/test_a.py::test_one - AssertionError: 3 != 4\n1 failed in 0.2s',
    'Process exited with code 2\nChunk ID: 1\n/usr/local/bin/x: line 3: command not found: frobnicate',
    '\n\n   \nTraceback (most recent call last):\n  File "/a/b.py", line 9\nValueError: bad value 42 for ~/x/y',
    'a b the quick brown fox jumps over 12 lazy dogs', 'ÉRREUR: fichier /tmp/x introuvable à la ligne 7',
]

CODEX_OUT = ['', 'Script failed', 'Process exited with code 0', 'Process exited with code 1', '{"exit_code": 2}',
             'exit_code=0', 'x' * 4000 + 'Script failed', 'exit_code: 3']


def strings_case():
    out = {'heavy_runs': [], 'changes_something': [], 'commands_of': [], 'is_fix_commit': [], 'failure_sig': [],
           'codex_failed': [], 'program': [], 'command_label': [], 'wake_sender': [], 'text_of': []}
    for c in COMMANDS:
        out['heavy_runs'].append({'in': c, 'extra': [], 'out': [list(r) for r in detect.heavy_runs(c)]})
        out['changes_something'].append({'in': c, 'tool': 'Bash', 'out': detect.changes_something('Bash', c)})
        out['commands_of'].append({'in': c, 'out': detect.commands_of(c)})
        out['is_fix_commit'].append({'in': c, 'out': detect.is_fix_commit(c)})
        out['program'].append({'in': c, 'out': adapters.program(c)})
        for show in (False, True):
            out['command_label'].append({'in': c, 'show_text': show, 'out': adapters.command_label('Bash', c, show)})
    for c, extra in [('./scripts/e2e.sh --all', ['./scripts/e2e.sh']), ('./scripts/e2e.sh', ['./scripts/e2e.sh --all']),
                     ('just ci fast', ['just ci']), ('just cip', ['just ci'])]:
        out['heavy_runs'].append({'in': c, 'extra': extra, 'out': [list(r) for r in detect.heavy_runs(c, tuple(extra))]})
    for name in ('Edit', 'Read', 'mcp__x__y', 'Agent', None):
        out['changes_something'].append({'in': None, 'tool': name, 'out': detect.changes_something(name, None)})
    for f in FAILURES + COMMANDS:
        out['failure_sig'].append({'in': f, 'out': detect.failure_sig(f)})
    for o in CODEX_OUT + FAILURES:
        out['codex_failed'].append({'in': o, 'out': detect.codex_failed(o)})
    for w in ['hi', '<asm from="lead">x</asm>', '<asm from="t1 lead/sub">x', '<hc from="t2">x', '<hc from=" x">',
              '<teammate-message teammate_id="a.b-c">', '  <task-notification>done', 'x <task-notification>',
              '<asm from="a"><hc from="b">', '<asm from="é">']:
        out['wake_sender'].append({'in': w, 'out': list(adapters.wake_sender(w))})
    for c in ['x', '', [], [text('a'), text('b')], [{'type': 'tool_result'}], [text('a'), 'b', {'type': 'text'}], None, 5,
              {'type': 'text', 'text': 'a'}]:
        out['text_of'].append({'in': c, 'out': adapters._text_of(c)})
    return out


def main():
    data = {'now': NOW, 'cases': CASES, 'strings': strings_case(), 'home_folder': PROJ}
    path = os.path.join(CRATE, 'tests/data/readers_a.json')
    with open(path, 'w') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        f.write('\n')
    print(f'{len(CASES)} cases, {sum(len(c["calls"]) for c in CASES)} calls -> {path}', file=sys.stderr)


if __name__ == '__main__':
    main()
