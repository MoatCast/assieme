"""Scheduled runs: a small team that starts on its own at set times, works on its own branches and stops when done.

The brain's tick fires runs; there is no launchd job per schedule. A time missed while the Mac slept runs once when
it wakes, never once per missed time. A run still going at the next time is skipped, and Activity says so. A run ends
when its top agent reports done: the brain stops its agents and their branches wait in Review. Nothing is merged.
Schedules are kept in schedules.json in the state folder.
"""
import datetime as dt
import os
import re
import threading
import uuid

from . import launch, live, newteam, preset, quiet, runtime, settings, status, tasks, team as team_

KINDS = ('day', 'week', 'hours', 'minutes')
DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
AT = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
MIN_MINUTES = 5  # the form offers hours; minutes are for a short test
DONE_NOTE = ('This is a scheduled run that nobody watches live. Work on your own branch and commit there; never merge '
             'or push. When the whole job is finished, call report with state "done": that ends the run and stops every '
             'agent of the team. The person reviews the branches later.')
ENDS_NOW = 'Reported. This scheduled run ends now: its agents stop and the person reviews the branches later.'
_lock = threading.Lock()  # the brain serves each call on its own thread, and the tick runs on another


class ScheduleError(ValueError):
    """A problem the form shows next to `field` ('name', 'project', 'preset', 'every' or 'task')."""

    def __init__(self, field, message):
        super().__init__(f'{field}: {message}')
        self.field, self.message = field, message


def _path():
    return quiet._path('schedules.json')


def _load():
    got = quiet._read_json(_path(), [])
    return [s for s in got if isinstance(s, dict) and s.get('id')] if isinstance(got, list) else []


def _save(items):
    quiet._write_json(_path(), items)


def _iso(t):
    return t.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# --- time ----------------------------------------------------------------------------------------------------------

def _step(every):
    n = every.get('n', 1)
    return dt.timedelta(minutes=n) if every['kind'] == 'minutes' else dt.timedelta(hours=n)


def _slot(every, created, t, tz=None):
    """The planned time at or before `t` (it may be before the schedule was made), and the time between two."""
    if every['kind'] in ('hours', 'minutes'):
        step = _step(every)
        return created + ((t - created) // step) * step, step
    local = t.astimezone(tz)
    h, m = map(int, every['at'].split(':'))
    # shortcut: a local wall time a day back keeps today's UTC offset, so a run near a DST change is an hour off
    slot = local.replace(hour=h, minute=m, second=0, microsecond=0)
    period = dt.timedelta(days=1)
    if every['kind'] == 'week':
        slot -= dt.timedelta(days=(local.weekday() - every['day']) % 7)
        period = dt.timedelta(days=7)
    if slot > local:
        slot -= period
    return slot, period


def latest(every, created, now, tz=None):
    """The last planned time at or before now, or None before the first one after the schedule was made."""
    slot, _ = _slot(every, created, now, tz)
    return slot if slot > created else None


def next_time(every, created, after, tz=None):
    slot, period = _slot(every, created, after, tz)
    return slot + period


def when_words(every):
    k = every['kind']
    if k == 'day':
        return f"Every day at {every['at']}"
    if k == 'week':
        return f"{DAYS[every['day']]}s at {every['at']}"
    n = every['n']
    unit = 'hour' if k == 'hours' else 'minute'
    return f'Every {unit}' if n == 1 else f'Every {n} {unit}s'


def _day_words(t, now, tz=None):
    t, now = t.astimezone(tz), now.astimezone(tz)
    days = (t.date() - now.date()).days
    day = ('today' if days == 0 else 'tomorrow' if days == 1 else 'yesterday' if days == -1
           else DAYS[t.weekday()] if 0 < days < 7 else t.strftime('%-d %b'))
    return f"{day} at {t.strftime('%H:%M')}"


# --- checking a schedule -------------------------------------------------------------------------------------------

def _every(raw):
    if not isinstance(raw, dict) or raw.get('kind') not in KINDS:
        raise ScheduleError('every', 'Choose when it runs.')
    k = raw['kind']
    if k in ('day', 'week'):
        if not AT.match(str(raw.get('at', ''))):
            raise ScheduleError('every', 'Give a time such as 02:00.')
        out = {'kind': k, 'at': raw['at']}
        if k == 'week':
            day = raw.get('day')
            if isinstance(day, bool) or day not in range(7):
                raise ScheduleError('every', 'Choose a day of the week.')
            out['day'] = day
        return out
    n = raw.get('n')
    low, high = (1, 24 * 7) if k == 'hours' else (MIN_MINUTES, 24 * 60)
    if isinstance(n, bool) or not isinstance(n, int) or not low <= n <= high:
        raise ScheduleError('every', f'Choose between {low} and {high} {k}.')
    return {'kind': k, 'n': n}


def _preset_folder(pid, teams):
    if pid in newteam.BUILTIN:
        return os.path.join(newteam.PRESETS_DIR, pid)
    if pid.startswith('saved:') and os.path.basename(pid[6:]) == pid[6:] and pid[6:]:
        return os.path.join(teams, pid[6:])
    raise ScheduleError('preset', 'Choose a team.')


def _roles(pid, team, teams):
    """The preset's roles, each named after the schedule's team: a role named plain `builder` would share the
    worktree of the person's own team's builder in the same project (worktrees are per role name)."""
    try:
        tm = preset.load(_preset_folder(pid, teams), practices=False)
    except (preset.PresetError, OSError) as e:
        raise ScheduleError('preset', f'This team can no longer be loaded: {e}') from None
    roles = []
    for r in tm.roles:
        d = newteam._role_dict(r)
        d['name'] = f'{team}-{r.name}'
        d['parent'] = f'{team}-{r.parent}' if r.parent else None
        roles.append(d)
    practices = tm.practices if pid.startswith('saved:') else settings.load()['practices']
    return roles, practices


def who(roles):
    """The agents and models a team runs unattended, as the app shows them: "Claude Code, Sonnet and Opus"."""
    names = {i: n for i, n in newteam.CLAUDE_MODELS}
    agents = list(dict.fromkeys(launch.NAMES.get(r.agent, r.agent) for r in roles))
    models = list(dict.fromkeys(names.get(r.model, r.model) if r.model else 'its default model' for r in roles))
    join = lambda xs: ', '.join(xs[:-1]) + ' and ' + xs[-1] if len(xs) > 1 else xs[0]
    return f'{join(agents)}, {join(models)}'


def options(teams=None):
    """The teams a schedule can start: the built-in ones, then every saved team that isn't a schedule's own."""
    teams = teams or newteam.teams_dir()
    own = {s['team'] for s in _load()}
    try:
        saved = sorted(d for d in os.listdir(teams) if os.path.isfile(os.path.join(teams, d, 'team.toml')))
    except OSError:
        saved = []
    out = []
    for pid, label in [(i, None) for i in newteam.BUILTIN] + [(f'saved:{d}', f'{d} (saved)') for d in saved if d not in own]:
        try:
            folder = _preset_folder(pid, teams)
            tm = preset.load(folder, practices=False)
        except (ScheduleError, preset.PresetError, OSError):
            continue  # a half-written or hand-edited folder: not offered
        label = label or newteam._as_preset(folder, pid)['label']
        out.append({'id': pid, 'label': label, 'who': who(tm.roles)})
    return out


def _agents(n):
    return 'its agent was' if n == 1 else f'its {n} agents were'


# --- the scheduler -------------------------------------------------------------------------------------------------

class Scheduler:
    def __init__(self, rt, start, starting=lambda: [], teams=None, now=status.now_utc, announce=None):
        """`start(project, task, roles, practices, name)` is New team's start; `starting()` its progress records."""
        self.rt, self.start, self.starting, self.now, self.announce = rt, start, starting, now, announce
        self.teams = teams or newteam.teams_dir()

    def _going(self, name):
        try:
            if any(r['state'] == 'live' for r in team_.status(self.rt, name)):
                return True
        except runtime.RuntimeError_:
            pass  # the core isn't running: nothing of this team is
        return any(r['team'] == name and not r['done'] for r in self.starting())

    def _update(self, id_, change):
        """Change one schedule under the lock, so an edit from the app and the tick never undo each other."""
        with _lock:
            items = _load()
            s = next((x for x in items if x['id'] == id_), None)
            if s is None:
                return None
            change(s)
            _save(items)
            return s

    def _log(self, s, action, title, reason, ok=True, alert=False):
        # A row that didn't work shows its detail ("It didn't work: ..."), so the why goes there too.
        live.record(action, s['name'], reason, ok=ok, detail='' if ok else reason, team=s['team'], by='auto', title=title,
                    now=self.now())
        if alert and self.announce:
            self.announce(title, reason)

    def tick(self, tz=None):
        now = self.now()
        for s in _load():
            going = self._going(s['team'])
            if s.get('running') and s.get('done'):
                self._finish(s)
                going = False
            elif s.get('running') and not going:  # its agents ended some other way
                self._update(s['id'], lambda x: x.update(running=False, done=False, skipped=None,
                                                         last={'at': (x.get('last') or {}).get('at'), 'result': 'ended'}))
                self._log(s, 'schedule_ended', f"{s['name']} stopped early",
                          'Its agents ended before its top agent said the work was done. What it committed waits in '
                          'Review.', alert=self._notify_finished())
            if s.get('removed'):
                if not going:
                    with _lock:
                        _save([x for x in _load() if x['id'] != s['id']])
                continue
            if s.get('paused'):
                continue
            slot = latest(s['every'], status.parse_ts(s['created']), now, tz)
            if slot is None or slot <= status.parse_ts(s.get('handled') or s['created']):
                continue
            self._update(s['id'], lambda x: x.update(handled=_iso(slot)))
            at = slot.astimezone(tz).strftime('%H:%M')
            if going:
                self._update(s['id'], lambda x: x.update(skipped=at))
                self._log(s, 'schedule_skip', f"Skipped {s['name']} at {at}",
                          'The last run is still going. Open its team to see what it is waiting for.')
            else:
                self._run(s, now)

    def _run(self, s, now):
        try:
            roles, practices = _roles(s['preset'], s['team'], self.teams)
            self.start(s['project'], f"{s['task'].strip()}\n\n{DONE_NOTE}", roles, practices, s['team'])
        except Exception as e:
            why = getattr(e, 'message', None) or str(e)
            self._update(s['id'], lambda x: x.update(last={'at': _iso(now), 'result': 'failed', 'detail': why}))
            self._log(s, 'schedule_failed', f"Couldn't start {s['name']}", f'{why} It tries again at its next time; '
                      'fix this in Settings, Scheduled runs.', ok=False, alert=True)
            return
        tasks.fresh(s['team'], s['task'])
        self._update(s['id'], lambda x: x.update(running=True, done=False, skipped=None,
                                                 last={'at': _iso(now), 'result': 'started'}))
        self._log(s, 'schedule_start', f"Started {s['name']}",
                  f"{when_words(s['every'])}. Its agents stop when they say they are done; their work waits in Review.")

    def _notify_finished(self):
        return settings.load()['notify']['finished']

    def _finish(self, s, result='finished'):
        try:
            n = team_.stop(self.rt, s['team'])
        except runtime.RuntimeError_:
            return False  # tried again at the next tick
        self._update(s['id'], lambda x: x.update(running=False, done=False, skipped=None,
                                                 last={'at': (x.get('last') or {}).get('at'), 'result': result}))
        if result == 'finished':
            self._log(s, 'schedule_done', f"{s['name']} finished",
                      f"Its top agent said the work is done, so {_agents(n)} stopped. Its work waits in Review; "
                      'nothing was merged.', alert=self._notify_finished())
        else:
            live.record('schedule_stop', s['name'], 'You stopped the run. What it committed waits in Review.',
                        team=s['team'], title=f"Stopped the run of {s['name']}", now=self.now())
        return True

    def done(self, team, role):
        """An agent reported done. True when that ends a scheduled run: only the team's top agent can."""
        if newteam.roster(team, self.teams).get(role, {}).get('parent'):
            return False
        hit = []
        with _lock:
            items = _load()
            for s in items:
                if s['team'] == team and s.get('running'):
                    s['done'] = True
                    hit.append(s)
            if hit:
                _save(items)
        return bool(hit)

    # --- what the app asks -----------------------------------------------------------------------------------------

    def save(self, name, project, preset, task, every, id=None):
        name = ' '.join((name or '').split())
        if not name:
            raise ScheduleError('name', 'Give it a name.')
        if not (task or '').strip():
            raise ScheduleError('task', 'Say what the team should do.')
        every = _every(every)
        project = os.path.abspath(os.path.expanduser(project or ''))
        check = newteam.project_check(project)
        if not check['ok']:
            raise ScheduleError('project', check['reason'])
        _preset_folder(preset, self.teams)
        with _lock:
            items = _load()
            old = next((x for x in items if x['id'] == id), None) if id else None
            if id and not old:
                raise ScheduleError('name', 'This schedule was removed.')
            if old:
                team = old['team']
            else:
                try:
                    saved = set(os.listdir(self.teams))
                except OSError:
                    saved = set()
                team = newteam.team_name(name, saved | {x['team'] for x in items})
            _roles(preset, team, self.teams)  # refuses a saved team that no longer loads
            fields = {'name': name[:60], 'project': project, 'preset': preset, 'task': task.strip(), 'every': every}
            if old:
                if old['every'] != every:  # counted afresh, so a new time doesn't fire at once for a past one
                    fields['created'] = fields['handled'] = _iso(self.now())
                old.update(fields)
            else:
                items.append({'id': uuid.uuid4().hex[:12], 'team': team, 'created': _iso(self.now()), 'handled': None,
                              'paused': False, 'running': False, 'done': False, 'last': None} | fields)
            _save(items)
        live.record('schedule_set', name, f'{when_words(every)}, in {os.path.basename(project)}.', team=team,
                    title=f'{"Changed" if old else "Added"} the schedule {name}')
        return self.rows()

    def pause(self, id, paused):
        def change(s):
            s['paused'] = bool(paused)
            if not paused:
                s['handled'] = _iso(self.now())  # resuming never runs the times it was paused through
        s = self._update(id, change)
        if s:
            live.record('schedule_pause', s['name'], 'It won’t start until you resume it.' if paused else
                        'It runs again at its next time.', team=s['team'],
                        title=f"{'Paused' if paused else 'Resumed'} the schedule {s['name']}")
        return self.rows()

    def stop(self, id):
        """Stop button: ends the run going now. The schedule carries on at its next time."""
        s = next((x for x in _load() if x['id'] == id), None)
        if not s or not s.get('running'):
            raise ScheduleError('run', 'This run has already ended.')
        if not self._finish(s, 'stopped'):
            raise ScheduleError('run', "Couldn't reach its agents. Try again in a moment.")
        return self.rows()

    def run_now(self, id):
        """Try again now, after a run that couldn't start."""
        s = next((x for x in _load() if x['id'] == id and not x.get('removed')), None)
        if not s:
            raise ScheduleError('run', 'This schedule was removed.')
        if self._going(s['team']):
            raise ScheduleError('run', 'It is running now.')
        self._run(s, self.now())
        return self.rows()

    def remove(self, id):
        """Its saved team and branches stay, so its work stays in Review. A run going now carries on to its end."""
        s = self._update(id, lambda x: x.update(removed=True))
        if s:
            live.record('schedule_remove', s['name'], 'Its branches stay in Review.', team=s['team'],
                        title=f"Removed the schedule {s['name']}")
        return self.rows()

    def _review_role(self, team):
        """The role whose branch Review opens: the first that edits. None before the first run."""
        try:
            roles = preset.load(os.path.join(self.teams, team), practices=False).roles
        except (preset.PresetError, OSError):
            return None
        return next((r.name for r in roles if r.checkout == 'branch'), None)

    def rows(self, tz=None):
        now, out = self.now(), []
        labels = {o['id']: o for o in options(self.teams)}
        for s in _load():
            if s.get('removed'):
                continue
            created = status.parse_ts(s['created'])
            last = s.get('last') or {}
            at = status.parse_ts(last.get('at'))
            nxt = next_time(s['every'], created, max(now, status.parse_ts(s.get('handled') or s['created'])), tz)
            result = last.get('result') if not s.get('running') else 'running'
            note = None
            if s.get('running'):
                line = (f"Running now, started {at.astimezone(tz).strftime('%H:%M') if at else 'just now'}. "
                        'Its agents stop when they say they are done.')
                if s.get('skipped'):
                    note = f"Skipped {s['skipped']}: the last run was still going."
            elif s.get('paused'):
                line = 'Paused. It won’t start until you resume it.'
            else:
                said = {'finished': 'Last run {} finished; its work waits in Review.',
                        'ended': 'Last run {} stopped before it said it was done. What it committed waits in Review.',
                        'stopped': 'Last run {} was stopped by you. What it committed waits in Review.',
                        'failed': f"Couldn’t start {{}}: {(last.get('detail') or '').rstrip('.')}."}.get(result)
                line = (said.format(_day_words(at, now, tz)) + ' ' if at and said else '') + \
                       f'Next: {_day_words(nxt, now, tz)}.'
            team = labels.get(s['preset'], {'label': s['preset'].removeprefix('saved:'), 'who': ''})
            out.append({'id': s['id'], 'name': s['name'], 'team': s['team'], 'project': s['project'],
                        'project_name': os.path.basename(s['project']), 'preset': s['preset'],
                        'preset_label': team['label'], 'who': team['who'], 'task': s['task'], 'every': s['every'],
                        'when': when_words(s['every']), 'paused': bool(s.get('paused')),
                        'state': 'running' if s.get('running') else 'paused' if s.get('paused') else 'waiting',
                        'result': result, 'line': line, 'note': note, 'next': _iso(nxt),
                        'review_role': self._review_role(s['team'])})
        return out

    def handlers(self):
        return {'schedule.list': lambda: {'schedules': self.rows(), 'teams': options(self.teams)},
                'schedule.save': lambda name, project, preset, task, every, id=None:
                    self.save(name, project, preset, task, every, id),
                'schedule.pause': lambda id, paused=True: self.pause(id, paused),
                'schedule.remove': lambda id: self.remove(id),
                'schedule.stop': lambda id: self.stop(id),
                'schedule.run_now': lambda id: self.run_now(id)}
