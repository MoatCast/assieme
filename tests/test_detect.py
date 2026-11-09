"""F5–F8 and F10. Synthetic logs and throwaway git repos only."""
import datetime as dt
import json
import os
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace

from assieme import adapters, detect, live, machine, panes, process, report, status
from assieme.model import HUMAN, Job, Session

NOW = dt.datetime(2099, 1, 2, 12, 0, tzinfo=dt.timezone.utc)
SINCE = dt.datetime(2098, 12, 31, tzinfo=dt.timezone.utc)
FUTURE = 4_100_000_000


def ago(minutes):
    return NOW - dt.timedelta(minutes=minutes)


def iso(t):
    return t.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def kinds(cmd):
    return [(k, full) for k, full, _ in detect.heavy_runs(cmd)]


def find(result, kind):
    return [f for f in result['findings'] if f['kind'] == kind]


def run(sessions, **kw):
    return detect.analyse(sessions, NOW, 24, **kw)


class ReadingCommands(unittest.TestCase):
    def test_a_full_suite_through_wrappers_is_heavy(self):
        for cmd in ('pytest -q', 'uv run -q --with pytest python -m pytest -q', 'cd app && npx vitest run',
                    'CI=1 npm test', '~/.cargo/bin/cargo test', 'go test ./...', 'pytest tests/'):
            self.assertEqual(kinds(cmd), [('test', True)], cmd)

    def test_a_narrow_run_is_heavy_but_not_full(self):
        for cmd in ('pytest tests/test_x.py', 'pytest -k foo', 'cargo test foo', 'npx vitest run src/a.test.ts',
                    'pytest --lf'):
            self.assertEqual(kinds(cmd), [('test', False)], cmd)

    def test_builds_ci_and_reviews_are_heavy(self):
        self.assertEqual(kinds('cargo build --release')[0][0], 'build')
        self.assertEqual(kinds('npx tauri build --bundles app')[0][0], 'build')
        self.assertEqual(kinds('gh workflow run ci.yml')[0][0], 'ci')
        self.assertEqual(kinds('codex review')[0][0], 'review')

    def test_naming_a_test_tool_is_not_running_it(self):
        for cmd in ('grep pytest notes.md', 'pip install pytest', 'cat pytest.ini', 'echo "run pytest; pytest"',
                    'git commit -m "x\n\npytest -q passes"', 'uv run --with pytest python tool.py',
                    "cat > a.md <<'EOF'\npytest -q\nEOF", 'echo "unterminated'):
            self.assertEqual(kinds(cmd), [], cmd)

    def test_every_part_of_a_compound_command_counts(self):
        self.assertEqual(kinds('pytest -q && npm run build'), [('test', True), ('build', False)])

    def test_a_project_adds_its_own_heavy_commands(self):
        self.assertEqual(detect.heavy_runs('./train.sh --fast', ['./train.sh'])[0][0], 'project')
        self.assertEqual(detect.heavy_runs('./train.sh --fast'), [])

    def test_the_project_list_is_read_from_its_folder(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(detect.project_heavy(d), ())
            os.mkdir(os.path.join(d, '.assieme'))
            with open(os.path.join(d, '.assieme', 'heavy.txt'), 'w') as f:
                f.write('# comment\n./train.sh\n\n')
            self.assertEqual(detect.project_heavy(d), ('./train.sh',))

    def test_commands_are_found_inside_a_codex_script(self):
        script = 'text(await tools.exec_command({cmd:"pytest -q",yield_time_ms:1000}));\ntext(await tools.exec_command({cmd:\'ls\'}));'
        self.assertEqual(detect.commands_of(script), ['pytest -q', 'ls'])
        self.assertEqual(detect.commands_of('pytest -q'), ['pytest -q'])
        self.assertEqual(detect.commands_of('text(await tools.apply_patch(p));'), [])

    def test_escaped_quotes_stay_inside_a_codex_command(self):
        self.assertEqual(detect.commands_of(r'tools.x({cmd:"echo \"hi\"\nls"})'), ['echo "hi"\nls'])

    def test_an_unclosed_codex_command_full_of_backslashes_is_quick(self):
        started = time.perf_counter()
        self.assertEqual(detect.commands_of('tools.x cmd: "' + '\\a' * 26), [])
        self.assertLess(time.perf_counter() - started, 1)

    def test_read_only_commands_change_nothing(self):
        for cmd in ('ls -la', 'git status && git diff', 'hc send coord "ok"', 'cat a | grep b'):
            self.assertFalse(detect.changes_something('Bash', cmd), cmd)
        for cmd in ('git commit -m x', 'pytest -q', 'mytool', 'echo "unterminated'):
            self.assertTrue(detect.changes_something('Bash', cmd), cmd)
        self.assertTrue(detect.changes_something('Edit', None))
        self.assertTrue(detect.changes_something('apply_patch', None))
        self.assertFalse(detect.changes_something('Read', None))

    def test_a_failure_fingerprint_ignores_numbers_and_paths(self):
        a = detect.failure_sig('Exit code 1\nlots of output\nFAILED /tmp/a1/test_x.py::t - assert 1 == 2\n2 failed in 3.2s')
        b = detect.failure_sig('Exit code 1\nother output\nFAILED /tmp/b9/test_x.py::t - assert 5 == 6\n7 failed in 9.1s')
        self.assertEqual(a, b)
        self.assertNotEqual(a, detect.failure_sig('Exit code 1\nnpm ERR! cannot find a module named left-pad here'))

    def test_a_failure_with_nothing_to_say_has_no_fingerprint(self):
        self.assertIsNone(detect.failure_sig('Exit code 1'))
        self.assertIsNone(detect.failure_sig('Exit code 1\n\n'))

    def test_codex_failures(self):
        self.assertTrue(detect.codex_failed('Script failed\nWall time 0.1 seconds\nOutput:\nboom'))
        self.assertTrue(detect.codex_failed('{"exit_code":2,"output":"x"}'))
        self.assertTrue(detect.codex_failed('Process exited with code 1\nOutput:\nx'))
        self.assertFalse(detect.codex_failed('Script completed\nOutput:\n{"exit_code":0}'))

    def test_a_fix_commit_is_a_cycle(self):
        for cmd in ('git commit -m "fix: the thing"', "git add . && git commit -am 'Address review notes'",
                    "git commit -m \"$(cat <<'EOF'\nFix the race\n\nbody\nEOF\n)\""):
            self.assertTrue(detect.is_fix_commit(cmd), cmd)
        for cmd in ('git commit -m "add feature"', 'git status', 'echo fix', 'git commit -m "ruling: keep the limit"'):
            self.assertFalse(detect.is_fix_commit(cmd), cmd)


def session(agent='a', cwd='/x'):
    s = Session(agent, 'claude-code')
    s.cwd = cwd
    return s


def job(start, end, label='pytest', kind='test', full=True, id=None, branch=None):
    return Job(id or f'{label}{start}', ago(start), ago(end), [(kind, full, label)], branch)


class ContextMeter(unittest.TestCase):
    def test_growth_is_per_active_hour(self):
        s = session()
        for i in range(61):  # 100K -> 280K over one hour
            s.call(100_000 + 3000 * i, ts=ago(60 - i))
        r = run([s])
        self.assertEqual(r['agents']['a']['growth_per_hour'], 180_000)
        f = find(r, 'growth')[0]
        self.assertIn('180K', f['what'])
        self.assertTrue(f['why'] and f['fix'])

    def test_a_long_gap_does_not_hide_growth(self):
        s = session()
        for i in range(31):
            s.call(100_000 + 3000 * i, ts=ago(800 - i))  # 90K in half an hour, then lunch
        for i in range(31):
            s.call(190_000 + 3000 * i, ts=ago(30 - i))
        self.assertEqual(run([s])['agents']['a']['growth_per_hour'], 180_000)

    def test_compaction_starts_a_new_run(self):
        s = session()
        for i in range(31):
            s.call(100_000 + 3000 * i, ts=ago(120 - i))
        for i in range(31):
            s.call(20_000 + 100 * i, ts=ago(60 - i))
        self.assertEqual(run([s])['agents']['a']['growth_per_hour'], 180_000)

    def test_slow_or_short_runs_stay_quiet(self):
        slow, short = session('slow'), session('short')
        for i in range(61):
            slow.call(100_000 + 100 * i, ts=ago(60 - i))
        for i in range(5):
            short.call(100_000 + 50_000 * i, ts=ago(5 - i))
        self.assertEqual(find(run([slow, short]), 'growth'), [])

    def _wakes(self, sender, n, tokens, channel='hc'):
        s = session()
        for i in range(n):
            s.wake(sender, channel, ts=ago(n - i))
            s.call(tokens, ts=ago(n - i))
        return s

    def test_wakes_that_change_nothing_are_idle_spending(self):
        r = run([self._wakes('coord', 10, 1_000_000)])
        self.assertEqual(r['agents']['a']['idle_calls'], 10)
        self.assertEqual(r['agents']['a']['idle_tokens'], 10_000_000)
        self.assertTrue(find(r, 'idle'))

    def test_a_person_or_a_subagent_start_is_not_idle_spending(self):
        self.assertEqual(run([self._wakes(HUMAN, 10, 1_000_000, HUMAN)])['agents']['a']['idle_tokens'], 0)
        self.assertEqual(run([self._wakes('p', 10, 1_000_000, 'subagent')])['agents']['a']['idle_tokens'], 0)

    def test_a_turn_that_edited_is_not_idle(self):
        s = self._wakes('coord', 10, 1_000_000)
        for t in s.turns:
            t.changed = True
        self.assertEqual(find(run([s]), 'idle'), [])

    def test_small_idle_spending_stays_quiet(self):
        self.assertEqual(find(run([self._wakes('coord', 10, 1000)]), 'idle'), [])

    def test_share_of_the_last_hour(self):
        big, small = session('big'), session('small')
        big.call(27_000_000, ts=ago(10))
        big.call(27_000_000, ts=ago(300))  # outside the hour
        small.call(3_000_000, ts=ago(10))
        r = run([big, small])
        self.assertEqual(r['agents']['big']['share_last_hour'], 0.9)
        self.assertIn('90%', find(r, 'share')[0]['what'])

    def _hour(self, big_tokens, small_tokens):
        big, small = session('big'), session('small')
        big.call(big_tokens, ts=ago(10))
        small.call(small_tokens, ts=ago(10))
        return find(run([big, small]), 'share')

    def test_a_share_needs_70_percent_of_20m_tokens(self):
        self.assertTrue(self._hour(14_000_000, 6_000_000))      # exactly 70% of exactly 20M
        self.assertEqual(self._hour(13_000_000, 7_000_000), [])  # 65%
        self.assertEqual(self._hour(13_000_000, 1_000_000), [])  # 93%, but only 14M

    def test_the_report_says_how_fast_each_memory_size_fills(self):
        s = session()
        for i in range(61):
            s.call(100_000 + 3000 * i, ts=ago(60 - i))
        why = find(run([s]), 'growth')[0]['why']
        self.assertIn('200K memory fills in about 67 min', why)
        self.assertIn('1M memory in about 5.6 h', why)
        self.assertNotIn('hour or two', why)

    def test_one_agent_alone_is_not_a_hog(self):
        s = session()
        s.call(9_000_000, ts=ago(10))
        self.assertEqual(find(run([s]), 'share'), [])


class HeavyJobs(unittest.TestCase):
    def test_overlap_of_two_agents(self):
        a, b = session('a'), session('b')
        a.jobs.append(job(30, 20))
        b.jobs.append(job(25, 22, 'cargo'))
        f = find(run([a, b]), 'overlap')[0]
        self.assertIn('overlapped', f['what'])
        self.assertTrue(f['fix'])
        self.assertEqual(f['count'], 1)
        self.assertEqual(f['longest_seconds'], 180)

    def test_a_brief_touch_is_not_overlap(self):
        a, b = session('a'), session('b')
        a.jobs.append(Job('1', ago(30), ago(20), [('test', True, 'pytest')]))
        b.jobs.append(Job('2', ago(20) - dt.timedelta(seconds=30), ago(10), [('test', True, 'pytest')]))
        self.assertEqual(find(run([a, b]), 'overlap'), [])

    def test_a_log_copied_by_resume_counts_once(self):
        a, b = session('a'), session('a')
        a.jobs.append(job(30, 20, id='same'))
        b.jobs.append(job(30, 20, id='same'))
        self.assertEqual(find(run([a, b]), 'overlap'), [])

    def test_pressure_while_they_overlap_is_named(self):
        a, b = session('a'), session('b')
        a.jobs.append(job(30, 20))
        b.jobs.append(job(25, 22, 'cargo'))
        samples = [{'at': ago(24), 'free_pct': 9, 'load': 3, 'cpus': 8}]
        self.assertIn('9% free', find(run([a, b], machine=samples), 'overlap')[0]['what'])

    def test_pressure_outside_a_heavy_job_says_nothing(self):
        a = session('a')
        a.jobs.append(job(30, 20))
        self.assertEqual(find(run([a], machine=[{'at': ago(5), 'free_pct': 9, 'load': 40, 'cpus': 8}]), 'pressure'), [])

    def test_pressure_during_one_heavy_job_is_flagged(self):
        a = session('a')
        a.jobs.append(job(30, 20))
        f = find(run([a], machine=[{'at': ago(25), 'free_pct': 50, 'load': 40, 'cpus': 8}]), 'pressure')[0]
        self.assertIn('load 40', f['what'])

    def test_full_suite_repeats_within_an_hour(self):
        a = session('builder')
        a.jobs += [job(m, m - 1) for m in (50, 40, 30, 20)]
        f = find(run([a]), 'repeats')[0]
        self.assertIn('builder', f['what'])
        self.assertIn('x4', f['what'])

    def test_quick_full_runs_are_not_heavy(self):
        a = session('builder')
        a.jobs += [Job(str(m), ago(m), ago(m) + dt.timedelta(seconds=1), [('test', True, 'unittest')]) for m in range(50, 9, -1)]
        self.assertEqual(find(run([a]), 'repeats'), [])

    def test_spread_out_or_narrow_runs_stay_quiet(self):
        spread, narrow = session('s'), session('n')
        spread.jobs += [job(m, m - 1) for m in (300, 200, 100)]
        narrow.jobs += [job(m, m - 1, full=False) for m in (50, 40, 30, 20)]
        self.assertEqual(find(run([spread, narrow]), 'repeats'), [])


class Loops(unittest.TestCase):
    def test_the_same_failure_across_turns(self):
        s = session('builder')
        s.failures += [(ago(m), 'sig', t, 'Bash: pytest') for m, t in ((50, 0), (40, 1), (30, 2))]
        f = find(run([s]), 'loop')[0]
        self.assertIn('builder', f['what'])
        self.assertIn('pytest', f['what'])
        self.assertIn('3', f['what'])

    def test_three_times_in_one_turn_is_a_loop(self):
        s = session()
        s.failures += [(ago(m), 'sig', 0, 'Bash: pytest') for m in range(3, 0, -1)]
        self.assertTrue(find(run([s]), 'loop'))

    def test_twice_is_not_a_loop(self):
        s = session()
        s.failures += [(ago(m), 'sig', m, 'Bash: pytest') for m in (2, 1)]
        self.assertEqual(find(run([s]), 'loop'), [])

    def test_different_or_spread_out_failures_stay_quiet(self):
        a, b = session('a'), session('b')
        a.failures += [(ago(m), f'sig{m}', m, 'Bash: x') for m in (50, 40, 30)]
        b.failures += [(ago(m), 'sig', m, 'Bash: x') for m in (600, 400, 200)]
        self.assertEqual(find(run([a, b]), 'loop'), [])

    def test_review_rounds_on_one_branch(self):
        s = session('builder')
        for i in range(6):
            s.wake('reviewer', 'hc', ts=ago(60 - i), branch='feat')
        f = find(run([s]), 'review')[0]
        self.assertIn('6', f['what'])

    def test_review_commands_count_as_rounds(self):
        s = session('builder')
        s.jobs += [job(60 - i, 60 - i, 'codex', 'review', False, branch='feat') for i in range(6)]
        self.assertTrue(find(run([s]), 'review'))

    def test_a_burst_of_reviews_on_main_is_a_loop(self):
        s = session('builder')
        for i in range(6):
            s.wake('reviewer', 'hc', ts=ago(60 - i), branch='main')
        self.assertTrue(find(run([s]), 'review'))

    def test_reviews_spread_over_main_or_few_rounds_stay_quiet(self):
        a, b = session('a'), session('b')
        for i in range(6):
            a.wake('reviewer', 'hc', ts=ago(1000 - i * 150), branch='main')
        for i in range(5):
            b.wake('reviewer', 'hc', ts=ago(60 - i), branch='feat')
        self.assertEqual(find(run([a, b]), 'review'), [])

    def test_fix_cycles_add_up_across_sessions(self):
        a, b = session('a'), session('b')
        a.cycles['feat'] = 3
        b.cycles['feat'] = 3
        self.assertIn('6', find(run([a, b]), 'cycles')[0]['what'])

    def test_five_cycles_stay_quiet(self):
        a = session('a')
        a.cycles['feat'] = 5
        self.assertEqual(find(run([a]), 'cycles'), [])


class PaidCompute(unittest.TestCase):
    def rows(self, cpu, rate=None, local=None, hours=2):
        out = [{'ts': ago(60 * hours - i * 10), 'box': 'x', 'cpu': cpu, 'rate': rate, 'usd_per_hour': 0.5}
               for i in range(hours * 6 + 1)]
        return out + ([{'ts': ago(30), 'box': 'local', 'rate': local}] if local else [])

    def test_low_cpu_for_hours(self):
        f = find(run([], boxes=self.rows(18)), 'box')[0]
        self.assertIn('box x: 18% CPU for 2 h', f['what'])
        self.assertIn('$1.00', f['why'])

    def test_slower_than_the_local_run(self):
        self.assertIn('slower than the local run', find(run([], boxes=self.rows(90, 10, 50)), 'box')[0]['what'])

    def test_a_busy_box_or_a_short_sample_stays_quiet(self):
        self.assertEqual(find(run([], boxes=self.rows(90, 60, 50)), 'box'), [])
        self.assertEqual(find(run([], boxes=[{'ts': ago(5), 'box': 'x', 'cpu': 5}, {'ts': ago(1), 'box': 'x', 'cpu': 5}]), 'box'), [])

    def test_the_metrics_file_is_optional_and_tolerates_junk(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(adapters.box_metrics(os.path.join(d, 'none.jsonl'), SINCE), [])
            p = os.path.join(d, 'boxes.jsonl')
            with open(p, 'w') as f:
                f.write(json.dumps({'ts': iso(ago(5)), 'box': 'x', 'cpu': 5}) + '\nnot json\n'
                        + json.dumps({'ts': iso(ago(5))}) + '\n' + json.dumps({'ts': '2000-01-01T00:00:00Z', 'box': 'old', 'cpu': 1}) + '\n')
            rows = adapters.box_metrics(p, SINCE)
            self.assertEqual([r['box'] for r in rows], ['x'])
            self.assertEqual(rows[0]['ts'], ago(5))


class Detectors(unittest.TestCase):
    def test_every_detector_says_why_it_is_quiet(self):
        rows = run([])['detectors']
        self.assertEqual({d['id'] for d in rows},
                         {'F5 growth', 'F5 idle', 'F5 share', 'F6 overlap', 'F6 repeats', 'F6 pressure', 'F7 same failure',
                          'F7 review rounds', 'F7 fix cycles', 'F10 paid compute'})
        for d in rows:
            self.assertEqual(d['fired'], 0)
            self.assertTrue(d['reason'], d['id'])

    def test_every_finding_says_what_why_and_fix(self):
        a, b = session('a'), session('b')
        a.jobs.append(job(30, 20))
        b.jobs.append(job(25, 22, 'cargo'))
        for f in run([a, b])['findings']:
            self.assertTrue(f['what'] and f['why'] and f['fix'])
            self.assertEqual(f['line'], f"{f['what']}. {f['why']}. {f['fix']}.")


def claude_row(kind, t, content, **extra):
    return {'type': kind, 'timestamp': iso(t), 'cwd': '/tmp/x', 'gitBranch': 'feat',
            'message': {'content': content, **extra}}


def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('\n'.join(json.dumps(r) for r in rows) + '\n')
    os.utime(path, (FUTURE, FUTURE))


def bash_turn(i, t, cmd, result='ok', error=False):
    u = {'input_tokens': 1000 + i, 'output_tokens': 1}
    return [claude_row('assistant', t, [{'type': 'tool_use', 'id': f't{i}', 'name': 'Bash', 'input': {'command': cmd}}],
                       id=f'm{i}', usage=u),
            claude_row('user', t + dt.timedelta(minutes=2), [{'type': 'tool_result', 'tool_use_id': f't{i}', 'is_error': error, 'content': result}])]


class Adapters(unittest.TestCase):
    def test_claude_jobs_failures_cycles_and_changes(self):
        with tempfile.TemporaryDirectory() as d:
            rows = [claude_row('user', ago(60), '<hc from="t1 coord">go</hc>')]
            rows += bash_turn(0, ago(59), 'pytest -q', 'Exit code 1\nFAILED test_x - assert 1 == 2\n1 failed in 2s', True)
            rows += bash_turn(1, ago(50), 'git commit -m "fix it"')
            rows += [claude_row('user', ago(40), 'next please')]
            rows += bash_turn(2, ago(39), 'ls')
            write(os.path.join(d, '-p', 's.jsonl'), rows)
            s = list(adapters.claude_code(d, SINCE))[0]
        self.assertEqual(len(s.jobs), 1)
        self.assertEqual(s.jobs[0].start, ago(59))
        self.assertEqual(s.jobs[0].end, ago(57))
        self.assertEqual(s.jobs[0].runs[0][:2], ('test', True))
        self.assertEqual(s.jobs[0].branch, 'feat')
        self.assertEqual(len(s.failures), 1)
        self.assertEqual(s.failures[0][3], 'Bash: pytest')
        self.assertEqual(s.cycles['feat'], 1)
        self.assertEqual(s.turns[0].branch, 'feat')
        self.assertTrue(s.turns[0].changed)
        self.assertFalse(s.turns[1].changed)
        self.assertEqual(len(s.samples), 3)

    def test_failure_text_is_never_kept(self):
        with tempfile.TemporaryDirectory() as d:
            rows = bash_turn(0, ago(59), 'pytest -q', 'Exit code 1\nFAILED SECRET-TEXT-HERE and more', True)
            write(os.path.join(d, '-p', 's.jsonl'), rows)
            s = list(adapters.claude_code(d, SINCE))[0]
        self.assertNotIn('SECRET', repr(s.failures))

    def test_the_word_ruling_in_a_message_is_not_a_fix_cycle(self):
        with tempfile.TemporaryDirectory() as d:
            rows = [claude_row('user', ago(60 - i), f'<hc from="t1 coord">ruling {i}: keep it</hc>') for i in range(6)]
            rows += bash_turn(0, ago(50), 'ls')
            write(os.path.join(d, '-p', 's.jsonl'), rows)
            s = list(adapters.claude_code(d, SINCE))[0]
        self.assertEqual(s.cycles['feat'], 0)

    def test_a_refusal_is_not_a_failure_and_its_lost_time_is_counted(self):
        with tempfile.TemporaryDirectory() as d:
            rows = bash_turn(0, ago(59), 'git push', 'Permission to use Bash with command git push has been denied', True)
            rows += bash_turn(1, ago(50), 'ls')
            write(os.path.join(d, '-p', 's.jsonl'), rows)
            s = list(adapters.claude_code(d, SINCE))[0]
        self.assertEqual(s.failures, [])
        self.assertEqual(sum(s.refusal_seconds.values()), 7 * 60)

    def test_codex_jobs_failures_and_branch(self):
        with tempfile.TemporaryDirectory() as d:
            t = ago(30)
            rows = [{'type': 'session_meta', 'payload': {'id': 'th', 'cwd': '/tmp/x', 'git': {'branch': 'feat'}}},
                    {'timestamp': iso(t), 'payload': {'type': 'message', 'role': 'user', 'content': [{'text': '<hc from="t1 coord">go</hc>'}]}},
                    {'timestamp': iso(t), 'payload': {'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'c1',
                                                      'input': 'text(await tools.exec_command({cmd:"pytest -q"}));'}},
                    {'timestamp': iso(t + dt.timedelta(minutes=3)), 'payload': {'type': 'custom_tool_call_output', 'call_id': 'c1',
                     'output': [{'type': 'text', 'text': 'Script failed\nOutput:\nFAILED test_x - assert 1 == 2 in 2s'}]}},
                    {'timestamp': iso(t), 'payload': {'type': 'token_count', 'info': {'total_token_usage': {'total_tokens': 9},
                                                                                     'last_token_usage': {'input_tokens': 9}}}}]
            write(os.path.join(d, '2099', 'r.jsonl'), rows)
            s = list(adapters.codex(d, SINCE))[0]
        self.assertEqual(s.jobs[0].end, t + dt.timedelta(minutes=3))
        self.assertEqual(s.jobs[0].branch, 'feat')
        self.assertEqual(len(s.failures), 1)
        self.assertTrue(s.turns[0].changed)
        self.assertEqual(s.samples[0][1], 9)


class Report(unittest.TestCase):
    def test_the_report_shows_findings_and_detectors(self):
        a, b = session('a'), session('b')
        a.call(10, ts=ago(30))
        b.call(10, ts=ago(30))
        a.jobs.append(job(30, 20))
        b.jobs.append(job(25, 22, 'cargo'))
        r = report.aggregate([a, b], NOW, 24)
        self.assertEqual(find(r, 'overlap')[0]['count'], 1)
        self.assertEqual(r['agents']['a']['share_last_hour'], 0.5)
        md = report.to_md(r, {'hours': 24, 'since': 'x'})
        self.assertIn(find(r, 'overlap')[0]['line'], md)
        self.assertIn('## Detectors', md)
        self.assertIn('F10 paid compute', md)
        json.dumps(report.aggregate([a, b], NOW, 24), default=str)

    def test_report_works_without_a_clock(self):
        s = session()
        s.call(10)
        self.assertEqual(report.aggregate([s])['findings'], [])


def agent(name='builder', **kw):
    defaults = dict(name=name, source='claude-code', state='working', detail='', pending={}, heavy=[], failures=[], samples=[],
                    heavy_end={}, context=0, refused=[], peers=set(), cwd='/x', last=ago(1), waits_on=[], cmds=[], ask_kind='', ask_since=None,
                    context_limit=None, path='/x/y.jsonl')
    return SimpleNamespace(**{**defaults, **kw})


class Live(unittest.TestCase):
    def test_a_loop_gets_a_tell_fix(self):
        a = agent(failures=[(ago(m), 'sig', m, 'Bash: pytest') for m in (25, 15, 5)])
        s = [x for x in live.suggest([a], [], {'builder': 't1'}, now=NOW) if x.action == 'tell'][0]
        self.assertIn('going in circles', s.text)
        self.assertIn('Stop', s.message)
        self.assertEqual(s.label, 'Tell it to change approach')

    def test_a_loop_that_ended_a_while_ago_is_dropped(self):
        a = agent(failures=[(ago(m), 'sig', m, 'Bash: pytest') for m in (65, 55, 45)])
        self.assertEqual(status.warnings([a], NOW), [])

    def test_full_suite_repeats_get_a_tell_fix(self):
        a = agent(heavy=[(ago(m), 'test', True, 'pytest', str(m)) for m in (50, 30, 10)],
                  heavy_end={str(m): ago(m - 2) for m in (50, 30, 10)})
        s = [x for x in live.suggest([a], [], {'builder': 't1'}, now=NOW) if x.action == 'tell'][0]
        self.assertIn('x3', s.text)
        self.assertIn('narrow', s.message)

    def test_quick_full_runs_get_no_fix(self):
        a = agent(heavy=[(ago(m), 'test', True, 'unittest', str(m)) for m in (50, 30, 10)],
                  heavy_end={str(m): ago(m) + dt.timedelta(seconds=1) for m in (50, 30, 10)})
        self.assertEqual(status.warnings([a], NOW), [])

    def test_two_agents_running_heavy_jobs_now(self):
        a = agent('a', heavy=[(ago(9), 'test', True, 'pytest', 'x')], pending={'x': ('Bash', 'Bash: pytest', 'pytest')})
        b = agent('b', heavy=[(ago(5), 'build', False, 'cargo', 'y')], pending={'y': ('Bash', 'Bash: cargo', 'cargo build')})
        w = [x for x in status.warnings([a, b], NOW) if x['kind'] == 'overlap']
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]['agent'], 'b')  # the later one is the one told to wait
        self.assertIn('wait', w[0]['message'])

    def test_a_finished_heavy_job_is_not_running(self):
        a = agent('a', heavy=[(ago(9), 'test', True, 'pytest', 'x')])
        b = agent('b', heavy=[(ago(5), 'build', False, 'cargo', 'y')], pending={'y': ('Bash', 'Bash: cargo', 'cargo build')})
        self.assertEqual([x for x in status.warnings([a, b], NOW) if x['kind'] == 'overlap'], [])

    def _growing(self, per_hour=180_000, **kw):
        step = per_hour // 60
        return agent(samples=[(ago(60 - i), 100_000 + step * i) for i in range(61)], **kw)

    def _growth(self, **kw):
        return [x for x in status.warnings([self._growing(**kw)], NOW) if x['kind'] == 'growth']

    def test_fast_growth_is_a_warning_with_no_button_while_it_works(self):
        a = self._growing(context=720_000)  # 30K from 75% of 1M, at 180K an hour: 10 minutes
        w = [x for x in status.warnings([a], NOW) if x['kind'] == 'growth'][0]
        self.assertIn('180K', w['what'])
        self.assertIn('75% of its 1000K memory in about 10 min', w['why'])
        self.assertIsNone(w['message'])
        self.assertEqual([x for x in live.suggest([a], [], {'builder': 't1'}, now=NOW) if x.action], [])

    def test_a_1m_memory_far_from_full_stays_quiet_live(self):
        self.assertEqual(self._growth(context=280_000), [])  # 2.6 h away

    def test_claude_under_200k_has_no_known_size_so_stays_quiet_live(self):
        self.assertEqual(self._growth(context=199_000), [])

    def test_codex_says_its_memory_size(self):
        self.assertTrue(self._growth(source='codex', context=130_000, context_limit=200_000))  # 7 minutes
        self.assertEqual(self._growth(source='codex', context=50_000, context_limit=200_000), [])  # 33 minutes
        self.assertEqual(self._growth(source='codex', context=199_000, context_limit=None), [])

    def test_growth_under_100k_an_hour_stays_quiet_live(self):
        self.assertEqual(self._growth(per_hour=90_000, context=900_000), [])
        self.assertTrue(self._growth(per_hour=102_000, context=740_000))

    def test_a_memory_already_past_75_percent_says_so(self):
        self.assertIn('already past 75%', self._growth(context=800_000)[0]['why'])

    def test_the_compact_card_names_the_growth(self):
        a = self._growing(state='idle', peers={'x'}, context=720_000)
        s = [x for x in live.suggest([a], [], {'builder': 't1'}, now=NOW) if x.action == 'compact'][0]
        self.assertIn('growing 180K', s.text)

    def test_an_exited_agent_gets_no_warning(self):
        a = agent(state='exited', failures=[(ago(m), 'sig', m, 'Bash: pytest') for m in (25, 15, 5)])
        self.assertEqual(status.warnings([a], NOW), [])

    def test_a_box_is_the_owners_to_fix(self):
        rows = [{'ts': ago(120 - i * 10), 'box': 'x', 'cpu': 10} for i in range(13)]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'boxes.jsonl')
            with open(p, 'w') as f:
                f.write('\n'.join(json.dumps({**r, 'ts': iso(r['ts'])}) for r in rows))
            old = os.environ.get('XDG_STATE_HOME')
            os.makedirs(os.path.join(d, 'assieme'))
            os.rename(p, os.path.join(d, 'assieme', 'boxes.jsonl'))
            os.environ['XDG_STATE_HOME'] = d
            try:
                s = [x for x in live.suggest([], [], {}, now=NOW) if x.agent == 'x'][0]
            finally:
                if old is None:
                    del os.environ['XDG_STATE_HOME']
                else:
                    os.environ['XDG_STATE_HOME'] = old
        self.assertTrue(s.yours)
        self.assertIsNone(s.action)
        self.assertTrue(s.hint)

    def test_a_card_on_the_pane_uses_the_finding_label(self):
        a = agent(name='builder', state='working', cwd='/w', failures=[(ago(m), 'sig', m, 'Bash: pytest') for m in (25, 15, 5)])
        row = {'address': 't1', 'state': 'live', 'cwd': '/w', 'labels': {}, 'status': {'state': 'working'}, 'created_ms': 1}
        fix = panes.view('t', [row], [a], NOW, lambda addr: '', None)['panes'][0]['fix']
        self.assertEqual(fix['buttons'][0]['label'], 'Tell it to change approach')
        self.assertEqual(fix['buttons'][0]['action'], 'tell')

    def test_status_text_and_json_carry_warnings(self):
        a = status.Agent('builder', 'claude-code', '/x/y.jsonl')
        a.last = ago(1)
        a.failures = [(ago(m), 'sig', m, 'Bash: pytest') for m in (25, 15, 5)]
        self.assertIn('going in circles', status.to_text([a], NOW))
        self.assertEqual(json.loads(status.to_json([a], NOW))['warnings'][0]['kind'], 'loop')


class StatusLog(unittest.TestCase):
    def test_the_log_tail_gives_heavy_jobs_failures_and_samples(self):
        a = status.Agent('b', 'claude-code', '/x')
        rows = bash_turn(0, ago(59), 'pytest -q', 'Exit code 1\nFAILED test_x - assert 1 == 2 in 2s', True)
        status._claude(a, rows, False)
        self.assertEqual(a.heavy[0][1:4], ('test', True, 'pytest'))
        self.assertEqual(a.heavy_end['t0'], ago(57))
        self.assertEqual(a.failures[0][3], 'Bash: pytest')
        self.assertEqual(a.samples[0], (ago(59), 1000))

    def test_codex_too(self):
        a = status.Agent('b', 'codex', '/x')
        t = ago(30)
        status._codex(a, [{'timestamp': iso(t), 'payload': {'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'c1',
                                                            'input': 'text(await tools.exec_command({cmd:"pytest -q"}));'}},
                          {'timestamp': iso(t), 'payload': {'type': 'custom_tool_call_output', 'call_id': 'c1',
                                                            'output': [{'type': 'text', 'text': 'Script failed\nOutput:\nFAILED test_x assert 1 == 2 in 2s'}]}},
                          {'timestamp': iso(t), 'payload': {'type': 'token_count', 'info': {'last_token_usage': {'input_tokens': 9}}}}], False)
        self.assertEqual(a.heavy[0][3], 'pytest')
        self.assertEqual(a.heavy_end['c1'], t)
        self.assertEqual(len(a.failures), 1)
        self.assertEqual(a.samples[0], (t, 9))


def git(cwd, *args, when=None):
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'a', 'GIT_AUTHOR_EMAIL': 'a@a', 'GIT_COMMITTER_NAME': 'a', 'GIT_COMMITTER_EMAIL': 'a@a'}
    if when:
        env.update(GIT_AUTHOR_DATE=when.isoformat(), GIT_COMMITTER_DATE=when.isoformat())
    subprocess.run(['git', '-C', cwd, *args], check=True, capture_output=True, env=env)


REAL_NOW = dt.datetime.now(dt.timezone.utc)


def repo(d, hours_open, lines=1):
    """A main branch with one merged item: a first commit `hours_open` before its merge, and one fix commit."""
    git(d, 'init', '-b', 'main')
    t = REAL_NOW - dt.timedelta(hours=hours_open + 1)
    with open(os.path.join(d, 'a'), 'w') as f:
        f.write('base\n')
    git(d, 'add', '.')
    git(d, 'commit', '-m', 'base', when=t)
    git(d, 'switch', '-c', 'feat')
    with open(os.path.join(d, 'b'), 'w') as f:
        f.write('x\n' * lines)
    git(d, 'add', '.')
    git(d, 'commit', '-m', 'add it', when=t)
    with open(os.path.join(d, 'c'), 'w') as f:
        f.write('')
    git(d, 'add', '.')
    git(d, 'commit', '-m', 'fix it', when=t + dt.timedelta(hours=1))
    git(d, 'switch', 'main')
    git(d, 'merge', '--no-ff', '-m', 'Merge feat', 'feat', when=REAL_NOW - dt.timedelta(hours=1))


class ProcessWeight(unittest.TestCase):
    def test_a_slow_small_merge_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, 6, 3)
            r = process.report([d], REAL_NOW - dt.timedelta(days=2), REAL_NOW)
        self.assertEqual(len(r['items']), 1)
        item = r['items'][0]
        self.assertEqual((item['commits'], item['fixes'], item['lines']), (2, 1, 3))
        self.assertAlmostEqual(item['hours'], 6, delta=0.1)
        f = r['findings'][0]
        self.assertEqual(f['kind'], 'process')
        self.assertTrue(f['why'] and f['fix'])
        self.assertEqual(r['detector']['fired'], 1)

    def test_a_big_or_quick_merge_stays_quiet(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, 6, 500)
            self.assertEqual(process.report([d], REAL_NOW - dt.timedelta(days=2), REAL_NOW)['findings'], [])
        with tempfile.TemporaryDirectory() as d:
            repo(d, 2, 3)
            r = process.report([d], REAL_NOW - dt.timedelta(days=2), REAL_NOW)
            self.assertEqual(r['findings'], [])
            self.assertIn('1 merged', r['detector']['reason'])

    def test_worktrees_of_one_repo_count_once_and_gone_folders_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, 6, 3)
            wt = os.path.join(d, 'wt')
            git(d, 'worktree', 'add', wt, '-b', 'other')
            r = process.report([d, wt, os.path.join(d, 'gone'), None], REAL_NOW - dt.timedelta(days=2), REAL_NOW)
        self.assertEqual(len(r['items']), 1)

    def test_pull_requests_add_reviews_and_gate_hours(self):
        calls = []

        def gh(cmd, cwd):
            calls.append(cmd)
            return json.dumps([{'number': 7, 'createdAt': '2099-01-01T00:00:00Z', 'mergedAt': '2099-01-01T08:00:00Z',
                                'additions': 3, 'deletions': 1, 'mergeCommit': {'oid': 'abc'},
                                'commits': [{'messageHeadline': 'add'}, {'messageHeadline': 'fix: x'}],
                                'reviews': [{}, {}, {}],
                                'statusCheckRollup': [{'name': 'ci', 'startedAt': '2099-01-01T00:00:00Z',
                                                       'completedAt': '2099-01-01T02:00:00Z'}, {'context': 'old'}]}])
        with tempfile.TemporaryDirectory() as d:
            repo(d, 2, 3)
            r = process.report([d], REAL_NOW - dt.timedelta(days=2), REAL_NOW, gh=gh)
        pr = [i for i in r['items'] if i.get('reviews') is not None][0]
        self.assertEqual((pr['reviews'], pr['checks'], pr['lines'], pr['fixes']), (3, 2, 4, 1))
        self.assertEqual(r['gates']['ci'], 2.0)
        self.assertTrue(r['findings'])  # 8 h for 4 lines
        self.assertEqual(calls[0][:3], ['gh', 'pr', 'list'])

    def test_a_failing_gh_is_not_fatal(self):
        def gh(cmd, cwd):
            raise OSError('no gh')
        with tempfile.TemporaryDirectory() as d:
            repo(d, 6, 3)
            self.assertEqual(len(process.report([d], REAL_NOW - dt.timedelta(days=2), REAL_NOW, gh=gh)['items']), 1)

    def test_gate_hours_from_logs_name_the_costliest(self):
        s = session()
        s.jobs += [job(120, 60), job(50, 20, 'cargo')]
        s.refusal_seconds['deny rule `Bash(git push:*)`'] = 7200
        gates = detect.gate_hours([s])
        self.assertEqual(gates['pytest'], 1.0)
        self.assertEqual(gates['cargo'], 0.5)
        self.assertEqual(gates['refused: deny rule `Bash(git push:*)`'], 2.0)
        f = process.gate_finding(gates, {'ci': 0.5})
        self.assertIn('refused: deny rule', f['what'])
        self.assertTrue(f['why'] and f['fix'])
        self.assertIsNone(process.gate_finding({'pytest': 0.1}, {}))


class Sampler(unittest.TestCase):
    def test_a_sample_has_memory_and_load(self):
        s = machine.sample()
        self.assertTrue(0 <= s['free_pct'] <= 100)
        self.assertGreater(s['cpus'], 0)
        self.assertGreaterEqual(s['load'], 0)

    def test_one_a_minute_and_read_back(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'm', 'machine.jsonl')
            self.assertTrue(machine.tick(ago(10), p, lambda: {'free_pct': 50, 'load': 1, 'cpus': 8}))
            self.assertFalse(machine.tick(ago(10) + dt.timedelta(seconds=30), p, lambda: {'free_pct': 1, 'load': 1, 'cpus': 8}))
            self.assertTrue(machine.tick(ago(5), p, lambda: {'free_pct': 9, 'load': 1, 'cpus': 8}))
            rows = machine.read(ago(60), p)
        self.assertEqual([r['free_pct'] for r in rows], [50, 9])
        self.assertEqual(rows[0]['at'], ago(10))
        self.assertEqual(machine.read(ago(60), '/nonexistent/file'), [])

    def test_the_file_is_capped(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'machine.jsonl')
            with open(p, 'w') as f:
                f.write((json.dumps({'at': iso(ago(100)), 'free_pct': 50, 'load': 1, 'cpus': 8}) + '\n') * 30000)
            machine.tick(ago(1), p, lambda: {'free_pct': 9, 'load': 1, 'cpus': 8})
            self.assertLess(os.path.getsize(p), machine.MAX_BYTES)
            self.assertEqual(machine.read(ago(60), p)[-1]['free_pct'], 9)


if __name__ == '__main__':
    unittest.main()
