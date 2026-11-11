"""Review and merge: each agent's branch, its diff and checks, and what the person decides about it.

Git is the source of truth: the branches `assieme/<team>/<role>` are the list. The brain keeps only test results
per commit and undo records, both in memory. Refusals raise ReviewError with a sentence for the screen.
"""
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import tomllib
import uuid

from . import adapters, live, preset, runtime, status, tasks, team as team_mod, worktree

MAX_DIFF = 2_000_000
TESTS_TIMEOUT_S = 900
PUSH_TIMEOUT_S = 120
DEPENDENCIES = ('node_modules', '.venv', 'venv')
EDITORS = ('cursor', 'code', 'zed', 'subl')
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
NOTE = re.compile(r'^[\s`>]*ASSIEME-NOTE\s+(\{.*\})', re.M)
CANT_START = re.compile(r'No module named pytest|Failed to spawn|command not found', re.I)
RUNNING = {'state': 'running', 'text': 'Running the tests…', 'detail': ''}
CANT_RUN = {'state': 'none', 'text': "Couldn't run the tests", 'detail': ''}
LOCAL_CHANGES = ('Your main checkout has uncommitted changes in the same files. Commit or set them aside, '
                 'then merge again.')


class ReviewError(ValueError):
    pass


def test_command(path):
    """The project's test command, read from what is in its folder; None when there isn't one we can run."""
    has = lambda name: os.path.isfile(os.path.join(path, name))
    if has('uv.lock'):
        return ['uv', 'run', 'pytest', '-q']
    if has('pytest.ini') or has('tox.ini') or has('pyproject.toml') or has('setup.py'):
        return ['python3', '-m', 'pytest', '-q']
    if has('package.json'):
        try:
            with open(os.path.join(path, 'package.json')) as f:
                script = (json.load(f).get('scripts') or {}).get('test', '')
        except (OSError, ValueError, AttributeError):
            script = ''
        if script and 'no test specified' not in script and os.path.isdir(os.path.join(path, 'node_modules')):
            return ['npm', 'test', '--silent']
        return None
    if has('Cargo.toml'):
        return ['cargo', 'test']
    if has('go.mod'):
        return ['go', 'test', './...']
    if has('Makefile'):
        with open(os.path.join(path, 'Makefile'), errors='replace') as f:
            if re.search(r'^test:', f.read(), re.M):
                return ['make', 'test']
    return None


def test_result(cmd, rc, output):
    """What a finished test run means, in words for the Checks list."""
    if rc == 5 and 'pytest' in cmd:  # pytest: nothing collected
        return {**CANT_RUN, 'text': 'No tests found'}
    # A Cargo workspace prints one result per crate; others end on the total (Vitest's file count comes first).
    pick = sum if cmd[0] == 'cargo' else (lambda xs: xs[-1] if xs else 0)
    passed = pick([int(x) for x in re.findall(r'(\d+) passed', output)])
    failed = pick([int(x) for x in re.findall(r'(\d+) failed', output)])
    total = passed + failed
    if rc == 127 or (rc != 0 and not total and CANT_START.search(output)):  # a real failure may log those words too
        return CANT_RUN
    if rc == 0:
        return {'state': 'ok', 'text': f'Tests pass, {passed} of {total}' if total else 'Tests pass', 'detail': ''}
    tail = '\n'.join(output.strip().splitlines()[-15:])
    return {'state': 'bad', 'text': f'Tests fail, {failed} of {total}' if failed else 'Tests fail', 'detail': tail}


def _numstat(text):
    """`git diff --numstat -z`: a rename puts its two paths in the next two fields."""
    parts, files, i = text.split('\0'), [], 0
    while i < len(parts) and parts[i]:
        added, deleted, path = parts[i].split('\t', 2)
        i += 1
        if not path:
            path, i = parts[i + 1], i + 2
        files.append({'path': path, 'add': 0 if added == '-' else int(added), 'del': 0 if deleted == '-' else int(deleted),
                      'binary': added == '-'})
    return files


def _claude_said(path):
    said = (adapters._text_of((o.get('message') or {}).get('content')) for o in adapters._rows(path) if o.get('type') == 'assistant')
    return [x for x in said if x]


def _codex_said(path):
    cwd, said = None, []
    for o in adapters._rows(path):
        p = o.get('payload') or {}
        if o.get('type') == 'session_meta':
            cwd = p.get('cwd')
        elif p.get('type') == 'message' and p.get('role') == 'assistant':
            said.append(' '.join(b.get('text', '') for b in p.get('content') or [] if isinstance(b, dict)))
    return cwd, said


class Review:
    def __init__(self, rt, teams, run=subprocess.run, which=shutil.which, thread=threading.Thread, log=None,
                 claude_dir=None, codex_dir=None):
        self.rt, self.teams, self.run, self.which, self.thread, self.log = rt, teams, run, which, thread, log
        self.claude_dir = claude_dir or os.path.expanduser('~/.claude/projects')
        self.codex_dir = codex_dir or os.path.expanduser('~/.codex/sessions')
        self.lock = threading.RLock()  # one merge, undo or discard at a time
        self.tests, self.test_locks, self.undos, self.transcripts, self.aheads = {}, {}, {}, {}, {}

    def _git(self, cwd, *args, **kw):
        return self.run(['git', '-C', cwd, *args], capture_output=True, encoding='utf-8', errors='replace', **kw)

    def _rows(self, name=None):
        try:
            rows = self.rt.list({team_mod.TEAM: name} if name else {}, all=True)
        except runtime.RuntimeError_:
            return []  # the assieme core isn't running: no terminals to read
        return [r for r in rows if team_mod.TEAM in (r.get('labels') or {})
                and (not name or r['labels'][team_mod.TEAM] == name)]

    def _project_from_folder(self, name):
        try:
            with open(os.path.join(self.teams, name, 'team.toml'), 'rb') as f:
                return tomllib.load(f).get('project')
        except (OSError, tomllib.TOMLDecodeError):
            return None

    def _known_teams(self):
        out = {}
        for name in os.listdir(self.teams) if os.path.isdir(self.teams) else []:
            project = self._project_from_folder(name)
            if project and os.path.isdir(project):
                out[name] = project
        for r in self._rows():
            head, sep, _ = (r.get('cwd') or '').partition('/.assieme/worktrees/')
            if sep and os.path.isdir(head):  # started from the command line: no saved folder
                out.setdefault(r['labels'][team_mod.TEAM], head)
        return out

    def _project(self, name):
        if not NAME.match(name or ''):
            raise ReviewError(f'{name!r} is not a team name.')
        project = self._known_teams().get(name)
        if not project:
            raise ReviewError(f"Can't find the project for team {name}.")
        return project

    def _has_branch(self, project, branch):
        return self._git(project, 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}').returncode == 0

    def _base(self, project):
        head = self._git(project, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
        names = [head.stdout.strip().removeprefix('origin/')] if head.returncode == 0 else []
        for name in names + ['main', 'master', self._git(project, 'branch', '--show-current').stdout.strip()]:
            if name and self._has_branch(project, name):
                return name
        raise ReviewError('This project has no main branch to compare with.')

    def _team_branches(self, project, name):
        prefix = f'refs/heads/assieme/{name}/'
        out = self._git(project, 'for-each-ref', '--format=%(refname)', prefix).stdout.split()
        return [(r.removeprefix(prefix), r.removeprefix('refs/heads/')) for r in out]

    def _ahead(self, project, base, branch):
        r = self._git(project, 'rev-list', '--count', f'{base}..{branch}')
        return int(r.stdout.strip() or 0) if r.returncode == 0 else 0

    def waiting(self):
        """Every agent's branch with commits main doesn't have, including those of stopped teams."""
        projects, out = {}, []
        for name, project in self._known_teams().items():
            projects.setdefault(project, set()).add(name)
        for project, names in projects.items():  # the app asks every few seconds: as few git calls as possible
            prefix = 'refs/heads/assieme/'
            refs = self._git(project, 'for-each-ref', '--format=%(objectname) %(committerdate:unix) %(refname) %(contents:subject)', prefix).stdout.splitlines()
            if not refs:
                continue
            try:
                base = self._base(project)
            except ReviewError:
                continue  # not a repository any more, or no commits yet
            base_tip = self._tip(project, base)
            for line in refs:
                tip, at, ref, *subject = line.split(' ', 3)
                name, _, role = ref.removeprefix(prefix).partition('/')
                if name in names and role:
                    branch = ref.removeprefix('refs/heads/')
                    ahead = self._ahead_of(project, base, base_tip, branch, tip)
                    if ahead:
                        out.append({'team': name, 'project': os.path.basename(project), 'role': role, 'branch': branch,
                                    'ahead': ahead, 'tip': tip, 'at': int(at), 'subject': ' '.join(subject)})
        return sorted(out, key=lambda w: (w['team'], w['role']))

    def _ahead_of(self, project, base, base_tip, branch, tip):
        key, tips = (project, branch), (base_tip, tip)
        if self.aheads.get(key, (None,))[0] != tips:
            self.aheads[key] = (tips, self._ahead(project, base, branch))
        return self.aheads[key][1]

    def _ctx(self, name, role):
        if not NAME.match(role or ''):
            raise ReviewError(f'{role!r} is not an agent name.')
        project = self._project(name)
        branch = f'assieme/{name}/{role}'
        if not self._has_branch(project, branch):
            raise ReviewError(f"{role} has no branch to review. It may have been merged or discarded.")
        return project, self._base(project), branch, os.path.join(project, '.assieme', 'worktrees', role)

    def _tip(self, project, branch):
        return self._git(project, 'rev-parse', f'refs/heads/{branch}').stdout.strip()

    def _dirty(self, path):
        return os.path.isdir(path) and bool(self._git(path, 'status', '--porcelain').stdout.strip())

    def _conflicts(self, project, base, branch):
        """None when git can't tell (too old, say), else the conflicting files."""
        r = self._git(project, 'merge-tree', '--write-tree', '--name-only', '--no-messages', base, branch)
        return [] if r.returncode == 0 else r.stdout.splitlines()[1:] if r.returncode == 1 else None

    def _files(self, project, base, branch):
        return _numstat(self._git(project, 'diff', '--numstat', '-z', '-M', f'{base}...{branch}').stdout)

    def _agent(self, name, role):
        rows = [r for r in self._rows(name) if r['labels'].get(team_mod.ROLE) == role and r.get('state') == 'live']
        if not rows:
            return {'live': False, 'working': False, 'address': None}
        r = max(rows, key=lambda x: x.get('created_ms') or 0)
        return {'live': True, 'working': (r.get('status') or {}).get('state') == 'working', 'address': r['address']}

    def _run_tests(self, project, path, tip, cmd):
        """In a throwaway checkout of the commit: the result is about exactly that commit, and whatever the tests
        generate can't look like the agent's uncommitted work."""
        key = (project, tip)
        checkout = os.path.join(project, '.assieme', 'test-runs', tip)
        with self.test_locks.setdefault(project, threading.Lock()):  # one heavy test suite at a time per project
            self._git(project, 'worktree', 'remove', '--force', checkout)  # left behind if the brain stopped mid-run
            try:
                if self._git(project, 'worktree', 'add', '--detach', checkout, tip).returncode != 0:
                    self.tests[key] = CANT_RUN
                    return
                for name in DEPENDENCIES:  # installed in the agent's worktree, and not part of the commit
                    if os.path.isdir(os.path.join(path, name)) and not os.path.exists(os.path.join(checkout, name)):
                        os.symlink(os.path.join(path, name), os.path.join(checkout, name))
                env = runtime.clean_env(os.environ) | {'CI': '1', 'NO_COLOR': '1'}  # no watch mode, no colour codes
                r = self.run(cmd, cwd=checkout, env=env, capture_output=True, encoding='utf-8', errors='replace',
                             stdin=subprocess.DEVNULL, timeout=TESTS_TIMEOUT_S)
                self.tests[key] = test_result(cmd, r.returncode, (r.stdout or '') + (r.stderr or ''))
            except subprocess.TimeoutExpired:
                self.tests[key] = {'state': 'bad', 'text': 'Tests took over 15 minutes, so they were stopped', 'detail': ''}
            except OSError:
                self.tests[key] = CANT_RUN
            finally:
                self._git(project, 'worktree', 'remove', '--force', checkout)

    def _tests_check(self, project, path, tip, retest=False):
        key = (project, tip)
        with self.lock:
            if retest and self.tests.get(key, {}).get('state') in ('bad', 'none'):
                del self.tests[key]  # opening Review again tries again, say after installing what was missing
            got = self.tests.get(key)
            if got:
                return got
            cmd = test_command(path) if os.path.isdir(path) else None
            self.tests[key] = RUNNING if cmd else CANT_RUN
        if cmd:
            self.thread(target=lambda: self._run_tests(project, path, tip, cmd), daemon=True).start()
        return self.tests[key]

    def _checks(self, name, role, project, base, branch, path, tip, files, retest=False):
        out = [dict(self._tests_check(project, path, tip, retest), id='tests')]
        found = self._conflicts(project, base, branch)
        if found is None:
            out.append({'id': 'conflicts', 'state': 'none', 'text': "Couldn't check for conflicts", 'detail': ''})
        elif found:
            shown = ', '.join(found[:3]) + (f' and {len(found) - 3} more' if len(found) > 3 else '')
            out.append({'id': 'conflicts', 'state': 'bad', 'text': f'Conflicts with {base} in {shown}', 'detail': ''})
        else:
            out.append({'id': 'conflicts', 'state': 'ok', 'text': f'No conflicts with {base}', 'detail': ''})
        mine = {f['path'] for f in files}
        overlaps = []
        for other, other_branch in self._team_branches(project, name):
            if other != role:
                theirs = self._git(project, 'diff', '--name-only', '-z', f'{base}...{other_branch}').stdout.split('\0')
                shared = sorted(mine & set(theirs))
                if shared:
                    overlaps.append(f'{other} also changed {", ".join(shared)}')
        out.append({'id': 'own-files', 'state': 'warn' if overlaps else 'ok',
                    'text': '; '.join(overlaps) or 'Only touched its own files', 'detail': ''})
        if self._dirty(path):
            out.append({'id': 'uncommitted', 'state': 'bad', 'text': 'Has uncommitted changes', 'detail': ''})
        return out

    def _ignored_path(self, project):
        return os.path.join(project, '.assieme', 'review-ignored.json')

    def _ignored(self, project):
        try:
            with open(self._ignored_path(project)) as f:
                return set(json.load(f))
        except (OSError, ValueError, TypeError):
            return set()

    def _set_ignored(self, project, ids):
        os.makedirs(os.path.dirname(self._ignored_path(project)), exist_ok=True)
        with open(self._ignored_path(project), 'w') as f:
            json.dump(sorted(ids), f)

    def _read_once(self, path, read):
        """A transcript's parsed form, read again only when it changes: Review polls every few seconds."""
        when = os.path.getmtime(path)
        if self.transcripts.get(path, (None,))[0] != when:
            self.transcripts[path] = (when, read(path))
        return self.transcripts[path][1]

    def _said(self, path):
        """What the agent in this folder said, from the transcripts both CLIs keep. Reviewers can't write files (Claude
        Code without Write, Codex in a read-only sandbox), but what they say is always recorded."""
        out = []
        real = os.path.realpath(path)
        slug = re.sub(r'[^A-Za-z0-9]', '-', real)  # how Claude Code names a folder's transcripts
        for f in glob.glob(os.path.join(self.claude_dir, slug, '*.jsonl')):
            out += self._read_once(f, _claude_said)
        st = os.stat(path)
        since = getattr(st, 'st_birthtime', st.st_mtime)  # sessions older than this worktree can't be about it
        for f in glob.glob(os.path.join(self.codex_dir, '**', '*.jsonl'), recursive=True):
            if os.path.getmtime(f) >= since:
                cwd, said = self._read_once(f, _codex_said)
                if cwd and os.path.realpath(cwd) == real:
                    out += said
        return out

    def _notes(self, project, role, branch):
        """What reviewers said about this branch. They are agents' words: bad lines are skipped, never fatal."""
        root, out = os.path.join(project, '.assieme', 'worktrees'), []
        ignored = self._ignored(project)
        for author in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            for raw in NOTE.findall('\n'.join(self._said(os.path.join(root, author)))):
                try:
                    n = json.loads(raw)
                    if n['branch'] not in (role, branch) or type(n['line']) is not int:
                        continue
                    note = {'author': author, 'file': str(n['file']), 'line': n['line'], 'text': str(n['text'])}
                except (ValueError, KeyError, TypeError):
                    continue
                note['id'] = hashlib.sha1(repr(sorted(note.items())).encode()).hexdigest()[:10]
                if note['text'].strip() and note['id'] not in ignored and note not in out:
                    out.append(note)
        return out

    def get(self, team, role, retest=False):
        project, base, branch, path = self._ctx(team, role)
        tip, files = self._tip(project, branch), self._files(project, base, branch)
        # The viewer reads git's plain format, whatever the person's own git settings say.
        diff = self._git(project, '-c', 'core.quotepath=off', 'diff', '-M', '--no-color', '--no-ext-diff', '--src-prefix=a/',
                         '--dst-prefix=b/', f'{base}...{branch}').stdout
        too_big = len(diff) > MAX_DIFF
        for f in files:
            f['missing'] = not os.path.isfile(os.path.join(path, f['path']))
        return {'team': team, 'role': role, 'project': os.path.basename(project), 'base': base, 'branch': branch,
                'tip': tip, 'ahead': self._ahead(project, base, branch), 'files': files,
                'diff': '' if too_big else diff, 'too_big': too_big,
                'checks': self._checks(team, role, project, base, branch, path, tip, files, retest),
                'notes': self._notes(project, role, branch), 'agent': self._agent(team, role)}

    def card(self, team, role):
        """What a board card shows about this agent's branch, or None when it has none. The tests run, as on opening
        Review, only when there is something to review."""
        try:
            project, base, branch, path = self._ctx(team, role)
        except ReviewError:
            return None
        tip, files, ahead = self._tip(project, branch), self._files(project, base, branch), self._ahead(project, base, branch)
        tests = self._tests_check(project, path, tip) if ahead else None
        return {'ahead': ahead, 'files': len(files), 'add': sum(f['add'] for f in files), 'del': sum(f['del'] for f in files),
                'tests': tests and {'state': tests['state'], 'text': tests['text']},
                'notes': [n['author'] for n in self._notes(project, role, branch)]}

    def _write_log(self, entry):
        live._log(self.log or live.log_path(), {'at': status.now_utc().strftime('%Y-%m-%dT%H:%M:%SZ'), 'by': 'you',
                                                'ok': True, **entry})

    def _same_tip(self, project, branch, tip, role):
        if self._tip(project, branch) != tip:
            raise ReviewError(f'{role} added commits since you looked. Look again first.')

    def _archive(self, project, name, role, branch, path, tip, force=False):
        """Keep the tip under refs/assieme, stop the agent, remove its folder and branch. Returns what went wrong, if
        anything. The branch must go: `worktree.ensure` refuses a role whose branch has no worktree."""
        ref = f'refs/assieme/archive/{name}/{role}/{int(time.time())}'
        if self._git(project, 'update-ref', ref, tip).returncode != 0:
            return "Couldn't save a copy of its work, so its worktree was left in place."
        address = self._agent(name, role)['address']
        if address:
            try:
                self.rt.kill(address)
            except runtime.RuntimeError_ as e:
                return f"Couldn't stop {role}: {e}"
        if os.path.isdir(path):
            r = self._git(project, 'worktree', 'remove', *(['--force'] if force else []), path)
            if r.returncode != 0:
                return f"Couldn't archive its worktree: {r.stderr.strip()}"
        r = self._git(project, 'branch', '-D', branch)
        return None if r.returncode == 0 else f"Couldn't delete its branch: {r.stderr.strip()}"

    def _checkout_of(self, project, branch):
        """The folder that has `branch` checked out, if any."""
        where = None
        for line in self._git(project, 'worktree', 'list', '--porcelain').stdout.splitlines():
            if line.startswith('worktree '):
                where = line.removeprefix('worktree ')
            elif line == f'branch refs/heads/{branch}':
                return where
        return None

    def _do_merge(self, project, base, branch, role):
        message = f"Merge {role}'s work ({branch})"
        old = self._tip(project, base)
        checkout = self._checkout_of(project, base)
        if checkout:
            if self._git(checkout, 'rev-parse', '--verify', '--quiet', 'MERGE_HEAD').returncode == 0:
                raise ReviewError(f'Your {base} checkout is in the middle of a merge. Finish it, then merge again.')
            r = self._git(checkout, 'merge', '--no-ff', '--no-edit', '-m', message, branch)
            if r.returncode != 0:
                said = (r.stderr or '') + (r.stdout or '')
                if self._git(checkout, 'rev-parse', '--verify', '--quiet', 'MERGE_HEAD').returncode == 0:
                    self._git(checkout, 'merge', '--abort')
                if 'local changes' in said or 'untracked working tree files' in said:
                    raise ReviewError(LOCAL_CHANGES)
                raise ReviewError(f"Couldn't merge: {said.strip()}")
        else:  # main isn't checked out anywhere, so no files to update
            tree = self._git(project, 'merge-tree', '--write-tree', base, branch)
            if tree.returncode != 0:  # exit 1 still prints a tree, with conflict markers in its files
                raise ReviewError(f"Couldn't merge: {(tree.stderr or tree.stdout).strip()}")
            commit = self._git(project, 'commit-tree', tree.stdout.split()[0], '-p', base, '-p', branch, '-m', message)
            done = self._git(project, 'update-ref', f'refs/heads/{base}', commit.stdout.strip(), old)
            if commit.returncode != 0 or done.returncode != 0:
                raise ReviewError(f"Couldn't merge: {(commit.stderr or done.stderr).strip()}")
        return old, self._tip(project, base)

    def merge(self, team, role, tip, force=False):
        with self.lock:
            project, base, branch, path = self._ctx(team, role)
            self._same_tip(project, branch, tip, role)
            if not self._ahead(project, base, branch):
                raise ReviewError(f'{role} has nothing new to merge.')
            if self._dirty(path):
                raise ReviewError(f'{role} has uncommitted changes. Ask it to commit them first.')
            found = self._conflicts(project, base, branch)
            if found:
                raise ReviewError(f'This conflicts with {base} in {", ".join(found)}. Ask {role} to fix that first.')
            if not force and self.tests.get((project, tip), {}).get('state') in ('bad', 'running'):
                raise ReviewError('The tests have not passed on this branch.')
            if not force and self._agent(team, role)['working']:
                raise ReviewError(f'{role} is still working.')
            notes = {n['id'] for n in self._notes(project, role, branch)}
            old, new = self._do_merge(project, base, branch, role)
            try:
                self._set_ignored(project, self._ignored(project) | notes)
            except OSError:
                pass  # the merge stands: old notes showing again is the lesser problem
            problem = self._archive(project, team, role, branch, path, tip)
            uid = uuid.uuid4().hex
            self.undos[uid] = {'team': team, 'role': role, 'project': project, 'base': base, 'branch': branch,
                               'path': path, 'tip': tip, 'old': old, 'new': new, 'notes': sorted(notes),
                               'archived': problem is None}
            self._write_log({'id': uid, 'team': team, 'agent': role, 'action': 'merge', 'undo': True,
                             'title': f"Merged {role} into {base}", 'reason': problem or 'Its worktree is archived.',
                             'restore': self.undos[uid]})
            tasks.merged(team, role, base, uid)
            return {'role': role, 'base': base, 'problem': problem, 'undo': uid}

    def undo(self, id):
        with self.lock:
            rec = self.undos.get(id)
            if not rec:
                raise ReviewError('This can no longer be undone here.')
            project, base, branch, path = rec['project'], rec['base'], rec['branch'], rec['path']
            if self._tip(project, base) != rec['new']:
                raise ReviewError(f"{base} has moved on since the merge, so it can't be undone here.")
            remote = f'refs/remotes/origin/{base}'
            if (self._git(project, 'rev-parse', '--verify', '--quiet', remote).returncode == 0
                    and self._git(project, 'merge-base', '--is-ancestor', rec['new'], remote).returncode == 0):
                raise ReviewError("This merge has already reached origin, so it can't be undone here.")
            if rec['archived'] and (self._has_branch(project, branch) or os.path.exists(path)):
                raise ReviewError(f"{rec['role']} has started again, so this can't be undone here.")
            checkout = self._checkout_of(project, base)
            r = (self._git(checkout, 'reset', '--keep', rec['old']) if checkout
                 else self._git(project, 'update-ref', f'refs/heads/{base}', rec['old'], rec['new']))
            if r.returncode != 0:
                raise ReviewError(f"Your {base} checkout has uncommitted changes in the same files. "
                                  'Commit or set them aside, then undo again.')
            if not self._has_branch(project, branch):
                self._git(project, 'branch', branch, rec['tip'])
            if not os.path.isdir(path):
                r = self._git(project, 'worktree', 'add', path, branch)
                if r.returncode != 0:
                    raise ReviewError(f"The merge is undone, but its worktree didn't come back: {r.stderr.strip()}")
            self._set_ignored(project, self._ignored(project) - set(rec['notes']))
            del self.undos[id]
            tasks.unmerged(rec['team'], id)
            self._write_log({'id': uuid.uuid4().hex, 'team': rec['team'], 'agent': rec['role'], 'action': 'undo-merge', 'of': id,
                             'undo': False, 'title': f"Undid the merge of {rec['role']}",
                             'reason': 'Its work is back, ready to review.'})
            return {'role': rec['role'], 'base': base}

    def discard(self, team, role, tip):
        with self.lock:
            project, _, branch, path = self._ctx(team, role)
            self._same_tip(project, branch, tip, role)
            notes = {n['id'] for n in self._notes(project, role, branch)}
            self._set_ignored(project, self._ignored(project) | notes)
            problem = self._archive(project, team, role, branch, path, tip, force=True)
            self._write_log({'id': uuid.uuid4().hex, 'team': team, 'agent': role, 'action': 'discard', 'undo': False,
                             'title': f"Discarded {role}'s work", 'reason': problem or 'Its worktree is removed.'})
            tasks.discarded(team, role)
            return {'role': role, 'problem': problem}

    def pull_request(self, team, role):
        project, base, branch, _ = self._ctx(team, role)
        origin = self._git(project, 'remote', 'get-url', 'origin')
        if origin.returncode != 0:
            raise ReviewError("This project has no remote called origin, so there's nowhere to open a pull request.")
        env = runtime.clean_env(os.environ) | {'GIT_TERMINAL_PROMPT': '0'}  # a password prompt would hang the brain
        try:
            r = self._git(project, 'push', '-u', 'origin', branch, env=env, timeout=PUSH_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise ReviewError("Couldn't push the branch: it took over 2 minutes.") from None
        if r.returncode != 0:
            raise ReviewError(f"Couldn't push the branch: {r.stderr.strip()}")
        logged = lambda title: self._write_log({'id': uuid.uuid4().hex, 'team': team, 'agent': role, 'undo': False,
                                                'action': 'pull-request', 'title': title, 'reason': f'Pushed {branch} to origin.'})
        if self.which('gh'):
            try:
                r = self.run(['gh', 'pr', 'create', '--fill', '--base', base, '--head', branch], cwd=project,
                             capture_output=True, encoding='utf-8', errors='replace', timeout=PUSH_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                raise ReviewError("The branch is pushed, but the pull request didn't open: it took over 2 minutes.") from None
            found = re.search(r'https://\S+', (r.stdout or '') + (r.stderr or ''))
            if found and (r.returncode == 0 or 'already exists' in (r.stderr or '')):
                logged(f'Opened a pull request for {role}')
                return {'url': found.group(0)}
            if 'known GitHub host' in (r.stderr or ''):  # a remote gh knows nothing about: nothing went wrong
                logged(f"Pushed {role}'s branch for a pull request")
                return {'url': None, 'detail': 'The branch is pushed. Open the pull request on your git host.'}
            logged(f"Pushed {role}'s branch; the pull request didn't open")
            raise ReviewError(f"The branch is pushed, but the pull request didn't open: {(r.stderr or '').strip()}")
        logged(f"Pushed {role}'s branch for a pull request")
        github = re.search(r'github\.com[:/](.+?)(?:\.git)?/?$', origin.stdout.strip())
        if github:
            return {'url': f'https://github.com/{github.group(1)}/compare/{base}...{branch}?expand=1'}
        return {'url': None, 'detail': 'The branch is pushed. Open the pull request on your git host.'}

    def ask(self, team, role, text):
        """Send the agent back to work; one that stopped starts again in the same worktree."""
        text = (text or '').strip()
        if not text:
            raise ReviewError('Say what should change.')
        self._ctx(team, role)
        agent = self._agent(team, role)
        if agent['live']:
            self.rt.send(agent['address'], text)
            restarted = False
        else:
            try:
                tm = preset.load(os.path.join(self.teams, team))
                started = team_mod.start(tm, text, self.rt, ask=lambda q: True, only=role)
            except (preset.PresetError, worktree.WorktreeError, team_mod.TeamError) as e:
                raise ReviewError(f"{role} has stopped and couldn't start again: {e}") from None
            if started[0].state != 'running':
                raise ReviewError(f"{role} has stopped and couldn't start again: {started[0].note or started[0].state}")
            restarted = True
        self._write_log({'id': uuid.uuid4().hex, 'team': team, 'agent': role, 'action': 'ask-changes', 'undo': False,
                         'title': f'Asked {role} for changes', 'reason': ''})  # never the words: they can be an agent's
        return {'restarted': restarted}

    def note(self, team, role, note, action):
        project, _, branch, _ = self._ctx(team, role)
        n = next((x for x in self._notes(project, role, branch) if x['id'] == note), None)
        if not n:
            raise ReviewError('This note has changed. Take another look.')
        if action == 'fix':
            self.ask(team, role, f"{n['author']} on {n['file']} line {n['line']}: {n['text']}\n\nPlease fix it and commit.")
        elif action != 'ignore':
            raise ReviewError(f'Unknown action {action!r}.')
        self._set_ignored(project, self._ignored(project) | {n['id']})
        return {'ok': True}

    def open_editor(self, team, role, file, line=None):
        project, base, branch, path = self._ctx(team, role)
        if file not in {f['path'] for f in self._files(project, base, branch)}:
            raise ReviewError("That file isn't in this diff.")  # the app's word isn't enough to open a path
        full = os.path.join(path, file)
        if not os.path.isfile(full):
            raise ReviewError('This file was deleted on this branch.')
        where = f'{full}:{line}' if type(line) is int and line > 0 else full
        editor = next((e for e in EDITORS if self.which(e)), None)
        cmd = (['open', '-t', full] if not editor else
               [editor, '--goto', where] if editor in ('cursor', 'code') else [editor, where])
        try:
            r = self.run(cmd, capture_output=True, encoding='utf-8', errors='replace', timeout=15)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ReviewError(f"Couldn't open an editor: {e}") from None
        if r.returncode != 0:
            raise ReviewError(f"Couldn't open an editor: {(r.stderr or '').strip()}")
        return {'ok': True}

    def handlers(self):
        return {'review.waiting': self.waiting, 'review.get': self.get, 'review.merge': self.merge,
                'review.undo': self.undo, 'review.discard': self.discard, 'review.pull_request': self.pull_request,
                'review.ask': self.ask, 'review.note': self.note, 'review.open_editor': self.open_editor}
