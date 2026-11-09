"""F5 context meter additions, F6 heavy-job guard, F7 retry loops and F10 paid compute: reading commands, and the checks.

No command, output or message text is kept here: only program names, counts, times and a fingerprint of a failure.
Every finding says what is wrong, why it matters and the one thing that fixes it. F8 is in process.py.
"""
import collections
import datetime as dt
import functools
import hashlib
import os
import re
import shlex

from .model import HUMAN, Job

GROWTH_FLAG = 150_000       # tokens per active hour, in the report: fills a 200K memory in about 80 minutes
GROWTH_LIVE = 100_000       # tokens per active hour, live, and only when...
MEMORY_FULL = 0.75          # ...it reaches this share of a known memory size...
FULL_SOON_MINUTES = 30      # ...within this long
GROWTH_MIN_HOURS = 0.5      # a shorter run says little
ACTIVE_GAP = 1800           # a longer silence is not active time
COMPACTED = 0.7             # context below this share of the call before it means it was compacted
IDLE_SHARE = 0.25           # share of an agent's input spent on wakes that changed nothing
IDLE_MIN_TOKENS = 5_000_000
HOG_SHARE = 0.7             # one agent's share of everyone's input in the last hour
HOG_MIN_TOKENS = 20_000_000
OVERLAP_MIN = 60            # seconds two heavy jobs must run together to count
FULL_REPEATS = 3            # full-suite runs in one hour...
REPEAT_MIN_SECONDS = 180    # ...that cost at least this long together: many very short runs cost nothing
LOW_FREE_PCT = 15           # free memory below this...
HIGH_LOAD = 2               # ...or load at this many times the CPU count is machine pressure
LOOP_TURNS = 3              # the same failure in this many turns...
LOOP_TOTAL = 3              # ...or this many times at all...
LOOP_WINDOW = 2 * 3600      # ...within this long
LOOP_LIVE = 30 * 60         # a loop whose last failure is older than this has ended, as far as the live view goes
REVIEW_ROUNDS = 5           # more rounds than this on one branch is a loop
FIX_CYCLES = 5              # more fix commits than this on one branch is churn
BOX_LOW_CPU = 30            # percent
BOX_MIN_HOURS = 1
BOX_LIVE_HOURS = 3

MAIN_BRANCHES = {None, '', 'main', 'master', 'HEAD'}

WRAPPERS = {'env', 'time', 'nice', 'command', 'exec', 'sudo', 'caffeinate', 'npx', 'bunx', 'pnpx'}
RUNNERS = {'uv', 'poetry', 'pipenv', 'hatch', 'pdm'}
VALUE_FLAGS = {'--with', '--python', '-p', '--project', '--directory', '--package', '--extra', '--group', '--env-file',
               '--from', '-n', '-k', '-s', '-g'}

HEAVY = [(kind, words.split()) for kind, words in [
    *[('test', w) for w in (
        'pytest', 'py.test', 'unittest', 'tox', 'nox', 'vitest', 'jest', 'mocha', 'rspec', 'phpunit', 'ctest', 'cargo test',
        'cargo nextest', 'go test', 'npm test', 'npm run test', 'pnpm test', 'pnpm run test', 'yarn test', 'yarn run test',
        'bun test', 'bun run test', 'make test', 'make check', 'mvn test', 'gradle test', 'gradlew test', 'swift test',
        'xcodebuild test', 'dotnet test', 'mix test')],
    *[('build', w) for w in (
        'cargo build', 'cargo tauri build', 'tauri build', 'vite build', 'npm run build', 'pnpm build', 'pnpm run build',
        'yarn build', 'yarn run build', 'bun run build', 'next build', 'tsc', 'go build', 'swift build', 'docker build',
        'xcodebuild', 'mvn package', 'mvn install', 'gradle build', 'gradlew build', 'make build', 'make all')],
    *[('ci', w) for w in ('gh workflow run', 'gh run rerun')],
    *[('review', w) for w in ('codex review', 'gh pr review', 'coderabbit')],
]]
HEAVY.sort(key=lambda h: -len(h[1]))  # the longest match wins: `xcodebuild test` before `xcodebuild`
NARROW_FLAGS = {'-k', '-run', '--lf', '--last-failed', '--failed', '-t', '--testnamepattern', '--filter', '--package',
                '-p', '--test', '--changed'}
WHOLE = {'run', '.', './...', 'tests', 'test', 'tests/', 'test/', '--', 'src', 'src/'}

CHANGE_TOOLS = {'Edit', 'Write', 'NotebookEdit', 'MultiEdit', 'Agent', 'Task', 'apply_patch', 'spawn_agent', 'js'}
READ_ONLY = {'ls', 'cat', 'head', 'tail', 'grep', 'egrep', 'rg', 'find', 'wc', 'awk', 'sed', 'pwd', 'cd', 'echo', 'printf', 'date',
             'sleep', 'which', 'jq', 'sort', 'uniq', 'cut', 'tr', 'stat', 'file', 'du', 'df', 'true', 'test', 'tree', 'ps', 'hc',
             'basename', 'dirname', 'realpath', 'diff', 'nl', 'less', 'column', 'xargs'}
READ_ONLY_GIT = {'status', 'log', 'diff', 'show', 'branch', 'rev-parse', 'blame', 'remote', 'ls-files', 'describe', 'fetch',
                 'merge-base', 'shortlog', 'worktree'}
READ_ONLY_GH = {'view', 'list', 'checks', 'status', 'diff'}

HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n.*?^\s*\1\s*$", re.S | re.M)
SCRIPT_CMD = re.compile(r"""\bcmd\s*:\s*(["'`])((?:\\.|(?!\1).)*)\1""", re.S)
COMMIT_MESSAGE = re.compile(r"""\bgit\s+commit\b[^\n]*?\s(?:-\w*m|--message)[\s=]+"""
                            r"""(?:["']?\$\(cat\s+<<-?\s*['"]?\w+['"]?\s*\n\s*|["'])?\s*([^\n]*)""")
FIX_SUBJECT = re.compile(r'^\W*(fix|fixes|fixed|bugfix|hotfix|address|re-?review|revert)\b', re.I)
LOG_NOISE = re.compile(r'^(exit code|script (completed|failed)|wall time|output:|process exited|chunk id|original token)', re.I)
CODEX_FAILED = re.compile(r'Script failed|Process exited with code [1-9]|"?exit_code"?\s*[:=]\s*[1-9]')


def commands_of(raw):
    """A shell command, or a Codex `exec` script -> the shell commands in it. A command held in a variable can't be read."""
    if 'tools.' not in (raw or ''):
        return [raw] if raw else []
    return [re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t'}.get(m.group(1), m.group(1)), body)
            for _, body in SCRIPT_CMD.findall(raw)]


@functools.lru_cache(maxsize=4096)
def _segments(command):
    """'a && b | c' -> [['a'], ['b'], ['c']], respecting quotes and dropping heredoc bodies. None if it can't be read."""
    text = re.sub(r'\d*>&\d+', '', HEREDOC.sub(r'\2', command))
    lexer = shlex.shlex(text, posix=True, punctuation_chars=';&|()\n')
    lexer.whitespace_split, lexer.whitespace = True, ' \t\r'
    out, now = [], []
    try:
        for tok in lexer:
            if tok and not tok.strip(';&|()\n'):
                out.append(now)
                now = []
            else:
                now.append(tok)
    except ValueError:
        return None
    return [s for s in out + [now] if s]


def _core(words):
    """Drop what only starts the real program: variables, `uv run`, `npx`, `python -m`, `timeout 60`."""
    words = list(words)
    while words:
        base = os.path.basename(words[0])
        if re.match(r'^\w+=', words[0]):
            words = words[1:]
        elif base == 'timeout':
            words = words[1:]
            while words and (words[0].startswith('-') or re.fullmatch(r'[\d.]+[smhd]?', words[0])):
                words = words[1:]
        elif base in WRAPPERS or (base in RUNNERS and words[1:2] == ['run']) or (base == 'pnpm' and words[1:2] == ['exec']):
            words = words[2:] if base in RUNNERS or base == 'pnpm' else words[1:]
            while words and words[0].startswith('-'):
                words = words[2:] if words[0] in VALUE_FLAGS and '=' not in words[0] else words[1:]
        elif re.fullmatch(r'python[\d.]*', base) and words[1:2] == ['-m']:
            words = words[2:]
        else:
            break
    return words


def _is_full(rest):
    """No path or selector means the whole suite. Anything unclear counts as narrow, so it errs towards quiet."""
    for w in rest:
        if re.match(r'^\d*[<>]', w):
            break
        if w.lower() in NARROW_FLAGS or (w.startswith('-') and w.split('=')[0].lower() in NARROW_FLAGS):
            return False
        if not w.startswith('-') and w not in WHOLE and not w.isdigit():
            return False
    return True


def heavy_runs(command, extra=()):
    """A shell command -> [(kind, full suite?, label)]. Labels name the program, never its arguments."""
    out = []
    for seg in _segments(command) or []:
        words = _core(seg)
        if not words:
            continue
        text = ' '.join(words)
        if any(text == p or text.startswith(p + ' ') for p in extra):
            out.append(('project', False, os.path.basename(words[0])))
            continue
        names = [os.path.basename(words[0])] + [w.split(':')[0] for w in words[1:]]
        for kind, want in HEAVY:
            if names[:len(want)] == want:
                full = kind == 'test' and _is_full(words[len(want):])
                out.append((kind, full, ' '.join(want)))
                break
    return out


def project_heavy(cwd):
    """A project's own heavy commands: `.assieme/heavy.txt`, one command prefix per line."""
    try:
        with open(os.path.join(cwd or '', '.assieme', 'heavy.txt')) as f:
            return tuple(s for s in (line.strip() for line in f) if s and not s.startswith('#'))
    except OSError:
        return ()


def changes_something(tool, command):
    """Could this call have changed anything? Only what is known to be read-only says no."""
    if tool in CHANGE_TOOLS or (tool or '').startswith('mcp__'):
        return True
    if not command:
        return False
    segments = _segments(command)
    if segments is None:
        return True
    for seg in segments:
        words = _core(seg)
        if not words:
            continue
        base = os.path.basename(words[0])
        if any(re.match(r'^\d*>', w) for w in words) or (base == 'sed' and '-i' in words):
            return True
        if base == 'git' and len(words) > 1 and words[1] in READ_ONLY_GIT:
            continue
        if base == 'gh' and len(words) > 2 and words[2] in READ_ONLY_GH:
            continue
        if base not in READ_ONLY:
            return True
    return False


def failure_sig(text):
    """Failed output -> a fingerprint that ignores numbers and paths, or None if it says nothing. The text isn't kept."""
    lines = [x.strip() for x in (text or '').splitlines() if x.strip() and not LOG_NOISE.match(x.strip())]
    tail = ' '.join(lines[-2:]).lower()
    tail = re.sub(r'\d+', 'n', re.sub(r'[\w.~-]*/[^\s:]*', '/', tail))
    return hashlib.sha1(tail.encode()).hexdigest()[:12] if len(tail) >= 20 else None


def codex_failed(text):
    return bool(CODEX_FAILED.search((text or '')[:4000]))


def is_fix_commit(command):
    m = COMMIT_MESSAGE.search(command or '')
    return bool(m and FIX_SUBJECT.match(m.group(1)))


def note_call(s, tool, command, ts, call_id, branch):
    """One tool call in a Session -> what F5, F6 and F7 need from it."""
    cmds = commands_of(command)
    if s.turns and not s.turns[-1].changed:
        s.turns[-1].changed = (changes_something(tool, None) or any(changes_something(tool, c) for c in cmds)
                               or 'apply_patch' in (command or '') or ('exec_command' in (command or '') and not cmds))
    for c in cmds:
        if branch not in MAIN_BRANCHES and is_fix_commit(c):
            s.cycles[branch] += 1
    runs = [r for c in cmds for r in heavy_runs(c, project_heavy(s.cwd))]
    if not runs:
        return None
    job = Job(call_id, ts, None, runs, branch)
    s.jobs.append(job)
    return job


def _finding(kind, agent, what, why, fix, message=None, label=None, **numbers):
    return {'kind': kind, 'agent': agent, 'what': what, 'why': why, 'fix': fix, 'line': f'{what}. {why}. {fix}.',
            'message': message, 'label': label, **numbers}


def _at(t):
    return t.astimezone().strftime('%H:%M')


def _runs(samples):
    """(time, context) calls -> runs between compactions, each as (growth per active hour, active hours)."""
    runs, first, prev, active = [], None, None, 0
    for ts, size in sorted(samples):
        if prev is None or size < prev[1] * COMPACTED:
            if prev is not None:
                runs.append((first, prev, active))
            first, active = (ts, size), 0
        else:
            gap = (ts - prev[0]).total_seconds()
            active += gap if gap <= ACTIVE_GAP else 0
        prev = (ts, size)
    if prev is not None:
        runs.append((first, prev, active))
    return [(round((b[1] - a[1]) / (sec / 3600)), sec / 3600) for a, b, sec in runs if sec >= GROWTH_MIN_HOURS * 3600]


def growth(samples):
    """The fastest growth per active hour in any run, or 0."""
    return max((rate for rate, _ in _runs(samples)), default=0)


def _in_window(times, seconds):
    """Sorted times -> the longest stretch that fits in the window."""
    best, start = [], 0
    for end, t in enumerate(times):
        while (t - times[start]).total_seconds() > seconds:
            start += 1
        if end - start + 1 > len(best):
            best = times[start:end + 1]
    return best


def loops(failures):
    """[(time, fingerprint, turn, label)] -> one dict per failure that keeps coming back."""
    by_sig = collections.defaultdict(dict)
    for ts, sig, turn, label in failures:
        by_sig[sig][ts] = (turn, label)
    out = []
    for sig, rows in by_sig.items():
        times = _in_window(sorted(rows), LOOP_WINDOW)
        turns = {rows[t][0] for t in times}
        if len(turns) >= LOOP_TURNS or len(times) >= LOOP_TOTAL:
            out.append({'sig': sig, 'label': rows[times[-1]][1], 'count': len(times), 'turns': len(turns), 'times': times})
    return out


def _loop_finding(agent, x, **tell):
    history = ', '.join(_at(t) for t in x['times'][-3:])
    return _finding('loop', agent, f"{agent} is going in circles: `{x['label']}` hit the same failure {x['count']} times ({history})",
                    'Retrying has not changed the result, and each try costs time and tokens',
                    'Stop it and have it try a different approach', **tell, count=x['count'])


def _pressed(sample):
    return sample['free_pct'] < LOW_FREE_PCT or sample['load'] >= HIGH_LOAD * sample['cpus']


def _pressure_words(sample):
    return f"{sample['free_pct']:.0f}% free memory, load {sample['load']:.0f}"


def _unique_jobs(sessions):
    """Every heavy job once, with its agent: a resumed session's log repeats the earlier rows."""
    seen = {}
    for s in sessions:
        for j in s.jobs:
            seen.setdefault(j.id, (s.agent, j))
    return list(seen.values())


def _end(j):
    return j.end or j.start


def gate_hours(sessions):
    """F8: hours spent per heavy command and per refusal source, from the logs. A compound command counts once."""
    hours = collections.Counter()
    for _, j in _unique_jobs(sessions):
        hours[j.runs[0][2]] += (_end(j) - j.start).total_seconds() / 3600
    for s in sessions:
        for why, seconds in s.refusal_seconds.items():
            hours[f'refused: {why}'] += seconds / 3600
    return {k: round(v, 2) for k, v in hours.items() if v > 0}


def _overlaps(jobs, machine):
    jobs = sorted(jobs, key=lambda x: x[1].start)
    out = []
    for i, (who, a) in enumerate(jobs):
        for other, b in jobs[i + 1:]:
            if b.start >= _end(a):
                break
            start, end = b.start, min(_end(a), _end(b))
            if (end - start).total_seconds() >= OVERLAP_MIN:
                out.append({'start': start, 'seconds': int((end - start).total_seconds()),
                            'who': f'{who}: {a.runs[0][2]}; {other}: {b.runs[0][2]}',
                            'pressed': next((m for m in machine if start <= m['at'] <= end and _pressed(m)), None)})
    return out


def analyse(sessions, now=None, hours=None, boxes=(), machine=()):
    """Sessions -> {'findings', 'agents': new columns per agent, 'detectors': each one's count and why it is quiet}."""
    findings, agents, stats = [], {}, {}
    per = collections.defaultdict(list)
    for s in sessions:
        per[s.agent].append(s)

    # F5
    hour_ago = now - dt.timedelta(hours=1) if now else None
    last_hour = {name: sum(n for s in ss for t, n in s.samples if t >= hour_ago) for name, ss in per.items()} if now else {}
    everyone = sum(last_hour.values())
    for name, ss in per.items():
        turns = [t for s in ss for t in s.turns if t.sender != HUMAN and t.channel not in ('subagent', 'spawn') and not t.changed]
        total = sum(s.tokens for s in ss)
        agents[name] = {'growth_per_hour': max((growth(s.samples) for s in ss), default=0),
                        'idle_calls': sum(t.calls for t in turns), 'idle_tokens': sum(t.tokens for t in turns),
                        'share_last_hour': round(last_hour[name] / everyone, 4) if everyone else 0}
        rate, idle = agents[name]['growth_per_hour'], agents[name]['idle_tokens']
        if rate >= GROWTH_FLAG:
            findings.append(_finding('growth', name, f'{name}: context grew {rate // 1000}K tokens an hour',
                                     f'At that rate a 200K memory fills in about {200_000 / rate * 60:.0f} min and a 1M memory '
                                     f'in about {1_000_000 / rate:.1f} h, and every call re-reads it all',
                                     'Compact it at its next pause, or split the work', per_hour=rate))
        if total and idle >= IDLE_MIN_TOKENS and idle >= IDLE_SHARE * total:
            findings.append(_finding('idle', name, f"{name}: {idle / 1e6:.1f}M tokens ({100 * idle / total:.0f}% of its input) "
                                     f"went on {agents[name]['idle_calls']} calls in turns that changed nothing",
                                     'Other agents woke it, and it re-read its context without doing any work',
                                     'Batch the notes it gets, or stop sending the routine ones', tokens=idle))
    hog = max(last_hour, key=last_hour.get, default=None)
    if (hog and sum(n > 0 for n in last_hour.values()) >= 2 and everyone >= HOG_MIN_TOKENS
            and last_hour[hog] >= HOG_SHARE * everyone):
        findings.append(_finding('share', hog, f'{hog}: {100 * last_hour[hog] / everyone:.0f}% of all input in the last hour',
                                 'One agent is spending most of what the team uses right now',
                                 'Check it is on the work you want most, or give it a narrower task'))

    # F6
    jobs = _unique_jobs(sessions)
    overlaps = _overlaps(jobs, machine)
    if overlaps:
        worst = max(overlaps, key=lambda o: o['seconds'])
        pressed = next((o['pressed'] for o in overlaps if o['pressed']), None)
        findings.append(_finding('overlap', None, f"Heavy jobs overlapped {len(overlaps)} time{'s' * (len(overlaps) > 1)}, the longest "
                                 f"{worst['seconds'] // 60} min at {_at(worst['start'])} ({worst['who']})"
                                 + (f', with the machine at {_pressure_words(pressed)}' if pressed else ''),
                                 'They compete for the same CPU and memory, so both run slower',
                                 'Have agents run heavy jobs one at a time', count=len(overlaps),
                                 longest_seconds=worst['seconds']))
    full = collections.defaultdict(list)
    for who, j in jobs:
        for kind, is_full, label in j.runs:
            if is_full:
                full[(who, label)].append(j)
    worst_repeat = worst_minutes = 0
    for (who, label), runs in full.items():
        cost = {j.start: (_end(j) - j.start).total_seconds() for j in runs}
        most = _in_window(sorted(cost), 3600)
        seconds = sum(cost[t] for t in most)
        worst_repeat, worst_minutes = max(worst_repeat, len(most)), max(worst_minutes, seconds / 60)
        if len(most) >= FULL_REPEATS and seconds >= REPEAT_MIN_SECONDS:
            findings.append(_finding('repeats', who, f'{who}: full suite ({label}) x{len(most)} in one hour, from {_at(most[0])}',
                                     'A full run costs the most time and machine, and a narrow run usually answers the question',
                                     'Have it run only the tests for what it changed, and the full suite once at the end',
                                     count=len(most)))
    pressed = [m for m in machine if any(j.start <= m['at'] <= _end(j) for _, j in jobs) and _pressed(m)]
    if pressed:
        worst_m = min(pressed, key=lambda m: m['free_pct'] - m['load'])
        findings.append(_finding('pressure', None, f'The machine was under pressure while heavy jobs ran: {_pressure_words(worst_m)}',
                                 f'{len(pressed)} sample{"s" * (len(pressed) > 1)} past the limit, and a Mac out of memory slows everything',
                                 'Run fewer heavy jobs at once', count=len(pressed)))

    # F7
    fail_total = 0
    for name, ss in per.items():
        rows = [x for s in ss for x in s.failures]
        fail_total += len(rows)
        findings += [_loop_finding(name, x) for x in loops(rows)]
    round_times = collections.defaultdict(list)
    for s in sessions:
        for t in s.turns:
            if t.ts and t.sender != HUMAN and 'review' in f'{t.sender} {s.agent}'.lower():
                round_times[(t.sender, s.agent, t.branch)].append(t.ts)
    for who, j in jobs:
        round_times[('', who, j.branch)] += [j.start for r in j.runs if r[0] == 'review']
    # main holds many items, so only a burst of rounds there is one loop
    rounds = {k: len(_in_window(sorted(v), LOOP_WINDOW)) if k[2] in MAIN_BRANCHES else len(v) for k, v in round_times.items()}
    for (sender, who, branch), n in rounds.items():
        if n > REVIEW_ROUNDS:
            findings.append(_finding('review', who, f"{who}: {n} review rounds on {branch or 'main'}" + (f' with {sender}' if sender else ''),
                                     'Past this many rounds, reviews are rarely finding new problems',
                                     'Decide what is good enough, or have a person settle it', count=n))
    cycles = collections.Counter()
    for s in sessions:
        cycles.update(s.cycles)
    for branch, n in cycles.items():
        if n > FIX_CYCLES:
            findings.append(_finding('cycles', None, f'{branch}: {n} fix cycles', 'One work item keeps coming back',
                                     'Stop and look at the approach, or split the item', count=n))

    # F10
    box_rows = box_findings(boxes)
    findings += box_rows

    fired = collections.Counter(f['kind'] for f in findings)
    fast = max((a['growth_per_hour'] for a in agents.values()), default=0)
    idle_top = max((a['idle_tokens'] for a in agents.values()), default=0)
    longest = max((o['seconds'] for o in overlaps), default=0)
    detectors = [
        ('F5 growth', 'growth', f'fastest {fast // 1000}K an hour in {len(agents)} agents; flags at {GROWTH_FLAG // 1000}K here; live needs a known memory size, {GROWTH_LIVE // 1000}K an hour and {MEMORY_FULL:.0%} full within {FULL_SOON_MINUTES} min'),
        ('F5 idle', 'idle', f'most by one agent {idle_top / 1e6:.1f}M; flags at {IDLE_MIN_TOKENS / 1e6:.0f}M and {IDLE_SHARE:.0%} of its input'),
        ('F5 share', 'share', f'{everyone / 1e6:.1f}M tokens in the last hour; flags one agent at {HOG_SHARE:.0%} of at least {HOG_MIN_TOKENS / 1e6:.0f}M'),
        ('F6 overlap', 'overlap', f'{len(jobs)} heavy jobs, {len(overlaps)} overlaps of {OVERLAP_MIN} s or more; background jobs look instant'),
        ('F6 repeats', 'repeats', f'most full-suite runs in one hour {worst_repeat}, costliest {worst_minutes:.0f} min; flags at {FULL_REPEATS} runs and {REPEAT_MIN_SECONDS // 60} min together'),
        ('F6 pressure', 'pressure', f'{len(machine)} machine samples, {len(pressed)} past the limit during heavy jobs'
         + ('' if machine else '; the brain has not sampled this window')),
        ('F7 same failure', 'loop', f'{fail_total} failed commands; flags at {LOOP_TURNS} turns or {LOOP_TOTAL} times in {LOOP_WINDOW // 3600} h'),
        ('F7 review rounds', 'review', f'most on one branch {max(rounds.values(), default=0)}; flags past {REVIEW_ROUNDS}; on main, within {LOOP_WINDOW // 3600} h'),
        ('F7 fix cycles', 'cycles', f'most on one branch {max(cycles.values(), default=0)}; flags past {FIX_CYCLES}; main is not counted'),
        ('F10 paid compute', 'box', f"{len({b['box'] for b in boxes if b['box'] != 'local'})} boxes with metrics"
         + ('' if boxes else '; no metrics file, nothing reports cloud boxes yet')),
    ]
    return {'findings': findings, 'agents': agents,
            'detectors': [{'id': i, 'fired': fired[k], 'reason': why} for i, k, why in detectors]}


def box_findings(rows):
    """F10: boxes using far less than what is paid for, or slower than the local run."""
    def mean(values):
        return sum(values) / len(values) if values else None
    local = mean([r['rate'] for r in rows if r['box'] == 'local' and r.get('rate') is not None])
    out = []
    for box in sorted({r['box'] for r in rows} - {'local'}):
        mine = sorted((r for r in rows if r['box'] == box), key=lambda r: r['ts'])
        span = (mine[-1]['ts'] - mine[0]['ts']).total_seconds() / 3600
        cpu = mean([r['cpu'] for r in mine if r.get('cpu') is not None])
        rate = mean([r['rate'] for r in mine if r.get('rate') is not None])
        price = mine[-1].get('usd_per_hour')
        low, slow = cpu is not None and cpu < BOX_LOW_CPU, rate is not None and local is not None and rate < local
        if span < BOX_MIN_HOURS or not (low or slow):
            continue
        words = ([f'{cpu:.0f}% CPU for {span:.0f} h'] if low else []) + (['slower than the local run'] if slow else [])
        out.append(_finding('box', box, f"box {box}: {'; '.join(words)}",
                            f'About ${price * span:.2f} paid for this stretch' if price else 'You are paying for time it is not using',
                            'Stop or shrink the box, or move the job back to this Mac'))
    return out


def live_findings(agents, now):
    """What `status` and the fix cards show: only what is happening now, from each agent's log tail."""
    out = []
    active = [a for a in agents if a.state != 'exited']
    for a in active:
        for x in loops(a.failures):
            if (now - x['times'][-1]).total_seconds() <= LOOP_LIVE:
                out.append(_loop_finding(a.name, x, label='Tell it to change approach', message=(
                    f"assieme: `{x['label']}` has failed the same way {x['count']} times. Stop retrying it. "
                    'Say what you have tried, then try a different approach.')))
        hour_ago = now - dt.timedelta(hours=1)
        full = collections.defaultdict(list)
        for ts, _, is_full, label, call in a.heavy:
            if is_full and ts >= hour_ago:
                full[label].append((a.heavy_end.get(call, now) - ts).total_seconds())
        for label, times in full.items():
            if len(times) >= FULL_REPEATS and sum(times) >= REPEAT_MIN_SECONDS:
                out.append(_finding(
                    'repeats', a.name, f'{a.name}: full suite ({label}) x{len(times)} this hour',
                    'A full run costs the most time and machine, and a narrow run usually answers the question',
                    'Have it run only the tests for what it changed', label='Ask it to run fewer tests', count=len(times),
                    message=f'assieme: you have run the full suite {len(times)} times this hour. From now on run a narrow '
                            'run for the files you changed, and the full suite once before you finish.'))
        rate, known = growth_now(a.samples), _known_memory(a)
        minutes = (MEMORY_FULL * known - a.context) / rate * 60 if rate >= GROWTH_LIVE and known else None
        if minutes is not None and minutes <= FULL_SOON_MINUTES:
            when = f'in about {minutes:.0f} min' if minutes > 0 else None
            why = (f'It reaches {MEMORY_FULL:.0%} of its {known // 1000}K memory {when}' if when
                   else f'It is already past {MEMORY_FULL:.0%} of its {known // 1000}K memory')
            out.append(_finding('growth', a.name, f'{a.name}: context growing {rate // 1000}K tokens an hour',
                                f'{why}, and every call re-reads it all', 'Compact it at its next pause', per_hour=rate))
    running = sorted(((ts, a, label) for a in active for ts, _, _, label, call in a.heavy if call in a.pending),
                     key=lambda r: r[0])
    for (first_ts, first, first_label), (ts, a, label) in zip(running, running[1:]):
        if a is not first and (now - ts).total_seconds() >= OVERLAP_MIN:
            out.append(_finding('overlap', a.name, f'{first.name} ({first_label}) and {a.name} ({label}) are both running heavy jobs',
                                'They compete for the same CPU and memory, so both run slower',
                                'Have one wait for the other', label='Ask it to wait', message=(
                                    f'assieme: {first.name} is running a heavy job too. When yours ends, wait for it '
                                    'to finish before you start another.')))
    return out


def _known_memory(a):
    """An agent's memory size only when its log proves it: Codex states it, and a Claude Code agent past 200K has 1M.
    A Claude Code agent under 200K could have either, so None."""
    if a.source == 'codex':
        return a.context_limit
    return 1_000_000 if a.context > 200_000 else None


def growth_now(samples):
    """Growth of the current run only, or 0."""
    if not samples:
        return 0
    order = sorted(samples)
    start = 0
    for i in range(1, len(order)):
        if order[i][1] < order[i - 1][1] * COMPACTED:
            start = i
    runs = _runs(order[start:])
    return runs[-1][0] if runs else 0
