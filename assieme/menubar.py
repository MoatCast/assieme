"""The menu bar panel and Mac notifications: what the icon counts, what its panel shows, what should pop up.

Nothing is detected here. Teams and buttons come from `teammap.Brain` (the same cards as the Team tab), finished work
from `review.waiting`, stalls from `status`. The app only draws what this returns, so every rule is tested here.
"""
import datetime as dt
import json
import os
import re
import threading

from . import live, panes, quiet, review, settings, status, teammap

PAUSE_S = 3600
ANSWER_WAIT_S = 30    # a plain-word question waits this long for its suggested answers, so they're on the notification
TESTS_WAIT_S = 120    # finished work waits this long for its tests, so the notification can say how they went
FORGET_S = 600        # a key is forgotten this long after it stops being true: a flicker doesn't notify twice
APP_GONE_S = 60       # without a poll for this long the brain's plain notifications take over again
MAX_BODY = 200
MAX_LABEL = 40        # a menu item, not a paragraph: Claude Code's menu choices can be long
YOURS = ('merge', 'discard', 'stop_team')  # you ended this agent: no need to tell you
SAYS = {'stuck': 'seems stuck', 'waiting': 'has work waiting'}
STOPPED = re.compile(r'^\W*stopped:\W*(.*)', re.I | re.S)  # the practice "Know when to stop" asks for this word
OPEN = {'id': 'open', 'label': 'Open'}


def _short(text, limit=MAX_BODY):
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _labels(buttons):
    """Notification buttons. The first is always Open, so a slip on the Options menu can never answer an agent."""
    out, seen = [OPEN], {OPEN['label']}
    for b in buttons:
        label = _short(b['label'], MAX_LABEL)
        if b['kind'] != 'reply' and label not in seen:
            seen.add(label)
            out.append({'id': b['id'], 'label': label})
    return out


def _asks(built):
    """Pauses at a daily limit that can be answered now: at least one of their agents has stopped."""
    listed = {p['role'] for p in built['panes'] if p['state'] in teammap.NEEDS_YOU}  # those already count, with the same button
    return [a for a in (built.get('budget') or {}).get('asks', []) if a.get('fix') and a['role'] not in listed]


def pause_until():
    """When a pause ends, or None. A damaged or finished pause is no pause."""
    at = status.parse_ts(settings.load().get('notify_paused_until'))
    return at if at and at > status.now_utc() else None


class Menubar:
    def __init__(self, brain, reviews, store=None):
        self.brain, self.reviews = brain, reviews
        self.store = store or os.path.join(quiet.state_dir(), 'notified-app.json')
        self.lock, self.polled = threading.Lock(), None

    def _load(self):
        try:
            with open(self.store) as f:
                seen = json.load(f)
        except (OSError, ValueError):
            return {}
        return seen if isinstance(seen, dict) else {}

    def _last_words(self, agent):
        """What the agent said last, from the transcript both CLIs keep, read again only when it changes."""
        try:
            if agent.source == 'codex':
                said = self.reviews._read_once(agent.path, review._codex_said)[1]
            else:
                said = self.reviews._read_once(agent.path, review._claude_said)
        except OSError:
            return ''
        return said[-1] if said else ''

    def _teams(self, now):
        """(name, built, agent by address) for every team the Team tab lists."""
        out = []
        for t in self.brain.teams()['teams']:
            built, rows, agents = self.brain._built(t['name'], now)
            out.append((t['name'], built, panes.match(rows, agents)))
        return out

    def _panel(self, teams, st):
        shown = []
        for name, built, _ in teams:
            items = [{'role': p['role'], 'state': p['state'], 'since_s': p['since_s'],
                      'text': _short((p['fix'] or {}).get('text') or p['detail']) if p['state'] == 'asking' else '',
                      'says': SAYS.get(p['state'], ''), 'fix': (p['fix'] or {}).get('id'),
                      'buttons': [{k: b[k] for k in ('id', 'label', 'kind')} for b in (p['fix'] or {}).get('buttons', [])]}
                     for p in built['panes'] if p['state'] in teammap.NEEDS_YOU]
            items += [{'role': a['role'], 'state': 'asking', 'since_s': a['since_s'], 'text': _short(a['title']), 'says': '',
                       'fix': a['fix'], 'buttons': [{k: b[k] for k in ('id', 'label', 'kind')} for b in a['buttons']]}
                      for a in _asks(built)]
            shown.append({'name': name, 'where': 'This Mac', 'need': len(items), 'items': items})
        shown.sort(key=lambda t: -t['need'])  # stable: what needs you first, otherwise as the Team tab lists them
        tokens = sum((b['spend'] or {}).get('tokens', 0) for _, b, _ in teams)
        ready = self.brain.today.ready
        paused = pause_until()
        return {'count': sum(t['need'] for t in shown), 'teams': shown, 'spend_tokens': tokens if ready else None,
                'auto_fixes': sum(v == 'auto' for v in st['fixes'].values()),
                'paused_until': paused.astimezone().strftime('%H:%M') if paused else None}

    def state(self):
        return self._panel(self._teams(status.now_utc()), settings.load())

    def _needs(self, teams):
        for name, built, _ in teams:
            for p in built['panes']:
                if p['state'] not in teammap.NEEDS_YOU or (p['since_s'] or 0) > live.NOTIFY_MAX_AGE:
                    continue
                f = p['fix'] or {}
                buttons = f.get('buttons', [])
                text = f.get('text') or p['detail'] or 'Waiting for you'
                if p['state'] == 'asking':
                    if not any(b['kind'] != 'reply' for b in buttons) and (p['since_s'] or ANSWER_WAIT_S) < ANSWER_WAIT_S:
                        continue  # its answers may be on the way
                    title = f"{p['role']} is asking you"
                else:
                    title = f"{p['role']} {SAYS[p['state']]}"
                # Only a question's words identify it: a stuck agent's detail changes as the minutes pass.
                yield f"need|{name}|{p['role']}|{p['state']}|{text if p['state'] == 'asking' else ''}", {
                    'kind': 'needs_you', 'team': name, 'role': p['role'], 'view': 'terminals', 'fix': f.get('id'),
                    'title': title, 'body': _short(text), 'buttons': _labels(buttons)}

    def _budgets(self, teams):
        for name, built, _ in teams:
            for a in _asks(built):
                yield f"budget|{name}|{a['scope']}|{a['fix']}", {
                    'kind': 'needs_you', 'team': name, 'role': a['role'], 'view': 'terminals', 'fix': a['fix'],
                    'title': a['title'], 'body': _short(a['detail']), 'buttons': _labels(a['buttons'])}
            for t in (built.get('budget') or {}).get('told', []):
                yield f"budget-told|{name}|{t['scope']}|{t['day']}", {
                    'kind': 'needs_you', 'team': name, 'role': None, 'view': None, 'fix': None,
                    'title': t['title'], 'body': _short(t['body']), 'buttons': []}

    def _stops(self, teams, ready_roles):
        ended_by_you = {(e.get('team'), e.get('agent')) for e in live.recent_actions(None, 200) if e.get('action') in YOURS}
        for name, built, matched in teams:
            for p in built['panes']:
                if p['since_s'] is None or p['since_s'] > live.NOTIFY_MAX_AGE:
                    continue  # no age: an old terminal this app has never seen, not news
                said = STOPPED.match(self._last_words(matched[p['address']])) if p['state'] == 'idle' and p['address'] in matched else None
                if said:
                    key, body = f"stop|{name}|{p['role']}|{said[1][:80]}", said[1].splitlines()[0] if said[1] else ''
                elif p['state'] == 'exited' and (name, p['role']) not in ended_by_you | ready_roles:
                    key, body = f"stop|{name}|{p['role']}|{p['address']}", 'It has stopped running.'
                else:
                    continue
                yield key, {'kind': 'finished', 'team': name, 'role': p['role'], 'view': 'terminals', 'fix': None,
                            'title': f"{p['role']} stopped", 'body': _short(body or 'It stopped and reported back.'),
                            'buttons': [OPEN]}

    def _ready(self, waiting, teams, now, known, showing):
        """Asking Review starts the project's tests and reads its whole diff, so only for a notification still to come."""
        states = {(name, p['role']): p['state'] for name, built, _ in teams for p in built['panes']}
        for w in waiting:
            age = now.timestamp() - w.get('at', 0)
            if states.get((w['team'], w['role'])) in ('working', *teammap.NEEDS_YOU) or age > live.NOTIFY_MAX_AGE:
                continue  # a busy agent's commit is routine progress; a question notifies on its own
            key = f"ready|{w['team']}|{w['role']}|{w.get('tip')}"
            if key in known:
                yield key, {}  # still true: remembered, so it can't pop up again
                continue
            if not showing:
                continue
            try:
                # Starts the tests once per commit, as opening Review would. Review keeps the result.
                tests = next(c for c in self.reviews.get(w['team'], w['role'])['checks'] if c['id'] == 'tests')
            except Exception:  # its folder may be gone: the work is still ready
                tests = {'state': 'none'}
            if tests['state'] == 'running' and age < TESTS_WAIT_S:
                continue
            said = {'ok': ' Tests pass.', 'bad': ' Tests fail.'}.get(tests['state'], '')
            yield key, {
                'kind': 'finished', 'team': w['team'], 'role': w['role'], 'view': 'review', 'fix': None,
                'title': 'Ready to review',
                'body': _short(f"{w['role']} finished “{w.get('subject', '')}”.{said}"), 'buttons': []}

    def _stalls(self, stalls):
        for st in stalls:
            if st['quiet_seconds'] <= live.NOTIFY_MAX_AGE:
                yield 'stall|' + '|'.join(sorted(r['agent'] for r in st.get('roots', []))), {
                    'kind': 'stalls', 'team': None, 'role': None, 'view': None, 'fix': None,
                    'title': 'Team stalled', 'body': _short(st['text']), 'buttons': []}

    def events(self, teams, waiting, stalls, now, st):
        """What should pop up now, once each. Paused: nothing is marked sent, so what's still true when it ends notifies
        then, and what was resolved meanwhile never does."""
        ready_roles = {(w['team'], w['role']) for w in waiting}
        showing = not pause_until() and st['notify'].get('finished', True)
        found = [*self._needs(teams), *self._budgets(teams), *self._stops(teams, ready_roles), *self._ready(waiting, teams, now, self._load(), showing),
                 *self._stalls(stalls)]
        with self.lock:
            seen = self._load()
            before = set(seen)
            out = []
            stamp = now.isoformat()
            forget = (now - dt.timedelta(seconds=FORGET_S)).isoformat()
            paused = pause_until()
            for key, e in found:
                if key in seen:
                    seen[key] = stamp
                elif not paused and st['notify'].get(e['kind'], True):
                    seen[key] = stamp
                    out.append({'key': key, **e})
            seen = {k: v for k, v in seen.items() if v >= forget}
            if set(seen) != before:
                quiet._write_json(self.store, seen)
        return out

    def poll(self):
        """The app's regular call: the count for the icon and what to pop up."""
        now = status.now_utc()
        self.polled = now
        st = settings.load()
        with self.brain.one_view():
            teams = self._teams(now)
            agents = self.brain._collect(now, self.brain.terminals())
        stalls = status.stalls(agents, now)
        # The brain's plain notifications take over if the app quits: they mustn't repeat what's been shown.
        live.announce(status.owner_queue(agents, now), stalls, now, notifier=lambda *_: None, switches=st['notify'])
        panel = self._panel(teams, st)
        return {'count': panel['count'], 'panel': panel, 'events': self.events(teams, self.reviews.waiting(), stalls, now, st)}

    def app_watching(self, now=None):
        return bool(self.polled and ((now or status.now_utc()) - self.polled).total_seconds() < APP_GONE_S)

    def pause(self):
        until = status.now_utc() + dt.timedelta(seconds=PAUSE_S)
        settings.set_value('notify_paused_until', until.strftime('%Y-%m-%dT%H:%M:%SZ'))
        return self.state()

    def resume(self):
        settings.set_value('notify_paused_until', None)
        return self.state()

    def handlers(self):
        return {'menu.state': self.state, 'menu.poll': self.poll,
                'notifications.pause': self.pause, 'notifications.resume': self.resume}
