"""The Team tab: who wakes whom, what needs you, what could be saved and today's spend.

Nothing is detected here. Cards come from `panes.view` (so the same advice as the command line), counts from
`report.aggregate` run on one team's sessions, and notes from `quiet`. Polling must stay cheap: only the brain's
tick reads whole logs (`Today`); `build` uses the copy it left.
"""
import contextlib
import dataclasses
import datetime as dt
import functools
import json
import os
import subprocess
import threading

from . import activity, adapters, answers, budget, fixes, live, newteam, panes, preset, quiet, report, runtime, settings, status, tasks, team
from .model import HUMAN

GROUPS_ABOVE = 8           # agents; above this the map draws groups
REFRESH_S = 120
DONE_FOR_YOU = ('nudge', 'compact', 'tell', 'quiet')  # fixes; what the person did is in Activity
WORDS = ('No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten')
NEEDS_YOU = ('asking', 'stuck', 'waiting')
DONE_ROWS = 50            # fix rows sent with the map: Team overview shows 3, an agent's panel shows its own


def needs_you(p):
    """A pane waiting for the person: its state, or a pause after a fix (which usually reads idle)."""
    return p['state'] in NEEDS_YOU or bool(p.get('paused_by_fix'))


def _real(path):
    return os.path.realpath(path) if path else None


def _word(n):
    return WORDS[n] if n < len(WORDS) else str(n)


def _read_sessions(claude_dir, codex_dir, since):
    out = []
    if os.path.isdir(claude_dir):
        out += adapters.claude_code(claude_dir, since)
    if os.path.isdir(codex_dir):
        out += adapters.codex(codex_dir, since)
    gemini, opencode = os.path.expanduser('~/.gemini'), os.path.expanduser('~/.local/share/opencode/opencode.db')
    if os.path.isdir(os.path.join(gemini, 'tmp')):
        out += adapters.gemini(gemini, since)
    if os.path.isfile(opencode):
        out += adapters.opencode(opencode, since)
    return out


class Today:
    """Today's sessions, refreshed by the brain's tick at most once per `every_s`. A failed read keeps the last copy."""

    def __init__(self, claude_dir, codex_dir, every_s=REFRESH_S):
        self.claude_dir, self.codex_dir, self.every_s = claude_dir, codex_dir, every_s
        self.lock, self.got, self.at, self.ready = threading.Lock(), [], None, False

    def refresh(self, now):
        if self.at is not None and (now - self.at).total_seconds() < self.every_s:
            return
        self.at = now
        midnight = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            got = _read_sessions(self.claude_dir, self.codex_dir, midnight)
        except Exception:
            return
        with self.lock:
            self.got, self.ready = got, True

    def sessions(self):
        with self.lock:
            return self.got


def _aliases(rows):
    """Every name a sender is tagged with: its terminal's address, its terminal's name, its role."""
    out = {}
    for r in rows:
        role = (r.get('labels') or {}).get(team.ROLE) or r['address']
        for k in (r['address'], r.get('name'), role):
            if k:
                out[k] = role
    return out


def _team_report(rows, today, now):
    """`report.aggregate` on this team's sessions, with agents and senders named by role. A folder belongs to one role."""
    by_cwd, alias = {}, _aliases(rows)
    for r in rows:
        if r.get('cwd'):
            by_cwd.setdefault(_real(r['cwd']), alias[r['address']])
    mine = []
    for s in today.sessions():
        role = by_cwd.get(_real(s.cwd))
        if role:
            turns = [dataclasses.replace(t, sender=alias.get(t.sender, t.sender)) for t in s.turns]
            mine.append(dataclasses.replace(s, agent=role, turns=turns))
    midnight = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return report.aggregate(mine, now, max(1, (now - midnight).total_seconds() / 3600))


def _edges(rep, roles):
    out = []
    for e in rep['edges']:
        if e['from'] in roles and e['to'] in roles and e['from'] != e['to']:
            noisy = e['last_hour'] >= report.NOISY_LAST_HOUR and e['empty'] * 2 > e['wakes']
            out.append({'from': e['from'], 'to': e['to'], 'wakes': e['wakes'], 'empty': e['empty'],
                        'noisy': {'per_hour': e['last_hour']} if noisy else None})
    return out


def groups(nodes, edges):
    """Groups for a big team, or None. A role's own `group` wins, then a lead and those reporting to it, then the first
    word of the name."""
    if len(nodes) <= GROUPS_ABOVE:
        return None
    parent = {n['role']: n['parent'] for n in nodes}
    has_children = {p for p in parent.values() if p}
    roots = [r for r, p in parent.items() if not p or p not in parent]
    leads = {r for r in roots if r in has_children}
    if len(leads) == 1:  # one coordinator over several leads: each of them is a group, and it stands alone
        boss = next(iter(leads))
        under = {r for r, p in parent.items() if p == boss and r in has_children}
        if under:
            leads = under | {boss}

    def key(n):
        if n['group']:
            return n['group']
        r = n['role']
        seen = set()
        while r and r not in seen:
            if r in leads:
                return r
            seen.add(r)
            r = parent.get(r)
        return n['role'].split('-')[0]

    out, where = {}, {}
    for n in nodes:
        k = key(n)
        where[n['role']] = k
        g = out.setdefault(k, {'name': k, 'roles': [], 'states': {}, 'need_you': 0, 'edges': []})
        g['roles'].append(n['role'])
        g['states'][n['role']] = n['state']
        g['need_you'] += n['state'] in NEEDS_YOU
    summed = {}
    for e in edges:
        a, b = where.get(e['from']), where.get(e['to'])
        if a and b and a != b:
            s = summed.setdefault((a, b), {'to': b, 'wakes': 0, 'noisy': None})
            s['wakes'] += e['wakes']
            if e['noisy'] and (not s['noisy'] or e['noisy']['per_hour'] > s['noisy']['per_hour']):
                s['noisy'] = e['noisy']
    for (a, _), s in summed.items():
        out[a]['edges'].append(s)
    return list(out.values())


def _subtitle(panes_, gs, need):
    total = len(panes_)
    rest = total - need
    if not need:
        working = sum(p['state'] == 'working' for p in panes_)
        rest = total - working
        if working and rest:
            head = f'{_word(working)} working, {_word(rest).lower()} idle or done.'
        else:
            head = f'{_word(working)} working.' if working else f'{_word(rest)} idle or done.' if rest else ''
        return f"{head} You'll get a notification if one needs you.".strip()
    if gs:
        return f'{total} agents in {len(gs)} groups. The other {rest} are working or done.'
    if not rest:
        return ''
    return 'The other one is working or done.' if rest == 1 else f'The other {_word(rest).lower()} are working or done.'


def build(team_name, rows, agents, now, read, answers, today, st, found=False, roster=None):
    v = panes.view(team_name, rows, agents, now, read, answers, show_commands=st['show_commands'])
    roster = roster or {}
    alias = _aliases(rows)
    by_address = {r['address']: r for r in rows}
    log_of = panes.match(rows, agents)
    log_name = {a.name: alias[t] for t, a in log_of.items()}
    nodes = []
    for p in v['panes']:
        r = by_address.get(p['address'], {})
        p['reachable'] = not p['address'].startswith('log:')
        p['session'] = 'default' if found else 'assieme'
        if not p['reachable'] and p['fix']:
            p['fix']['buttons'] = []
        nodes.append({'role': p['role'], 'state': p['state'], 'since_s': p['since_s'], 'agent': p['agent'],
                      'parent': alias.get(r.get('parent')), 'group': (roster.get(p['role']) or {}).get('group')})
    roles = {n['role'] for n in nodes}
    rep = _team_report(rows, today, now)
    edges = _edges(rep, roles)
    waits = [{'from': log_name[a.name], 'to': log_name[w]} for a in log_of.values() for w in a.waits_on
             if a.name in log_name and w in log_name]
    gs = groups(nodes, edges)
    total = sum(x['tokens'] for x in rep['agents'].values())
    cwd = {alias[r['address']]: r.get('cwd') for r in rows}
    tips = []
    if not found:
        for x in rep['send_less']:
            a, b = x['from'], x['to']
            if a in roles and b in roles and cwd.get(a) and cwd.get(b) and not quiet.is_quiet(cwd[a], cwd[b]):
                tips.append({'id': f'{a}>{b}', 'from': a, 'to': b, 'share': x['share'],
                             'from_cwd': cwd[a], 'to_cwd': cwd[b]})
    bud = None
    if not found:  # agents found on this Mac get no limit: their messages can't be held
        bud = budget.view(team_name, {n['role']: (rep['agents'].get(n['role']) or {}).get('tokens', 0) for n in nodes}, total, now)
        for ask in bud['asks']:
            for p, n in zip(v['panes'], nodes):
                if p['role'] in ask['roles'] and p['state'] not in ('asking', 'exited'):
                    b = dict(panes._button(p['role'], 'budget', ask['label'], ask['scope']), add=ask['add'])
                    p['fix'] = dict(panes._fix(p['role'], [b], ask['text']), budget=ask['scope'], who=ask['who'], detail=ask['detail'])
                    p['detail'], n['paused'] = 'Paused at today’s limit', True
                    ask.setdefault('fix', p['fix']['id'])
                    ask.setdefault('role', p['role'])
                    ask.setdefault('buttons', p['fix']['buttons'])
    for p, n in zip(v['panes'], nodes):  # paused after a fix that didn't work; a budget pause wins
        card = None if n.get('paused') or p['state'] in ('asking', 'exited') else fixes.pane_fix(team_name, p['role'], now)
        p['paused_by_fix'] = bool(card)
        if card:  # after Esc it usually reads idle, yet it is waiting for the person
            p['fix'], p['detail'], n['paused'] = card, 'Paused: still stuck after a fix', True
            v['strip'] = [s for s in v['strip'] if s['role'] != p['role']] + [
                {'role': p['role'], 'state': 'stuck', 'text': f"{p['role']} is paused: still stuck after a fix"}]
    v['strip'].sort(key=lambda x: panes.URGENT.index(x['state']))
    need = sum(needs_you(p) for p in v['panes'])
    title = 'All quiet' if not need else f"{_word(need)} agent{'s' * (need != 1)} need{'s' * (need == 1)} you"
    subtitle = _subtitle(v['panes'], gs, need)
    if bud and bud['asks'] and not need:
        whole = any(a['scope'] == budget.TEAM for a in bud['asks'])
        title = 'Paused at today’s limit'
        subtitle = ('The agents stopped where they were. Nothing is lost, and nothing was closed.' if whole else
                    f"{' and '.join(a['who'] for a in bud['asks'])} stopped where {'it was' if len(bud['asks']) == 1 else 'they were'}."
                    + (' The others carry on.' if len(nodes) > len({r for a in bud['asks'] for r in a['roles']}) else ''))
    today_ = now.astimezone().strftime('%Y-%m-%d')
    every = activity.list_rows(team=team_name, now=now)['rows']
    done = [r for r in every if r['action'] in DONE_FOR_YOU][:DONE_ROWS]
    mine = [r for r in done if r['day'] == today_ and r['by'] == 'auto']
    fixes_today = {'fixed': sum(r['outcome'] == 'fixed' or r['action'] == 'quiet' for r in mine),
                   'checking': sum(r['outcome'] == 'checking' for r in mine)}
    stopped = not any(r.get('state') == 'live' for r in rows)
    return {'team': team_name, 'found': found, **({'run_by_assieme': bool(st['run_found'].get(team_name))} if found else {}),
            'stopped': stopped, 'fixes_today': fixes_today, 'last_at': every[0]['at'] if every else None,
            'open_tasks': sum(c.get('status') != 'done' for c in tasks.board(team_name)['cards']),
            'run': _run(rows, fixes_today['fixed'], team_name) if stopped and rows else None,
            'title': title, 'subtitle': subtitle, 'budget': bud,
            'panes': v['panes'], 'strip': v['strip'], 'nodes': nodes, 'edges': edges, 'waits': waits, 'groups': gs,
            'tips': tips, 'done': done,
            'spend': {'tokens': total, 'waste_share': round(rep['empty_tokens'] / total, 4)} if total else None,
            'spend_ready': getattr(today, 'ready', True)}


def _run(rows, fixed, team_name):
    """A stopped team's last run: from its first agent's start to its last agent's stop."""
    run = panes._current_run(rows)
    start = min(r.get('created_ms') or 0 for r in run)
    end = max(r.get('status_ms') or r.get('created_ms') or 0 for r in run)
    done = sum(c.get('status') == 'done' for c in tasks.board(team_name)['cards'])
    return {'minutes': max(0, (end - start) // 60_000), 'fixed': fixed, 'tasks_done': done,
            'stopped_at': dt.datetime.fromtimestamp(end / 1000).strftime('%H:%M')}


def _hold_pair(team_name, t, now=None, **extra):
    label = f"{t['from']} to {t['to']}"
    quiet.set_pair(t['from_cwd'], t['to_cwd'], label)
    return live.record('quiet', label, f"{round(t['share'] * 100)}% of today's tokens went on these wake-ups.", team=team_name,
                       now=now, pair=[t['from_cwd'], t['to_cwd'], label], **extra)


def apply_tip(team_name, tip_id, built, now=None):
    t = next((x for x in built['tips'] if x['id'] == tip_id), None)
    if not t:
        return {'ok': False, 'detail': 'This tip has changed. Take another look.'}
    _hold_pair(team_name, t, now)
    return {'ok': True, 'detail': ''}


def hold_automatically(views, now=None):
    """The first tip of each team, held at once: it works from the next note, so it is judged fixed straight away."""
    out = []
    for name, built in views.items():  # a pair the person un-held is never held again by itself (quiet.opted_out)
        t = next((x for x in built.get('tips', []) if not quiet.is_quiet(x['from_cwd'], x['to_cwd'])
                  and not quiet.opted_out(x['from_cwd'], x['to_cwd'])), None)
        if not t:
            continue
        edge = next((e for e in built.get('edges', []) if (e['from'], e['to']) == (t['from'], t['to'])), None) or {}
        n = (edge.get('noisy') or {}).get('per_hour')
        what = f'{n} wake-ups an hour with no work' if n is not None else 'Repeated wake-ups with no work'
        e = _hold_pair(name, t, now, by='auto', title=f"Held {t['from']}'s routine notes until {t['to']} was free",
                       measured={'what': what, 'minutes': None, 'tokens': None})
        out += [e, live.record('outcome', e['agent'], '', team=name, by='auto', now=now, of=e['id'], result='fixed')]
    return out


def _wakers(a, alias, now):
    counts = {}
    for ts, sender in a.wakes:
        if (now - ts).total_seconds() <= 3600 and sender != HUMAN:
            name = alias.get(sender, sender)
            counts[name] = counts.get(name, 0) + 1
    return [{'role': r, 'count': n} for r, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]


def _named(text, alias):
    head, sep, who = text.rpartition('Took a note from ')
    return f'{head}{sep}{alias.get(who, who)}' if sep else text


def detail(team_name, role, built, agents, roster, now, rows=None, show_commands=False):
    p = next((x for x in built['panes'] if x['role'] == role), None)
    if not p:
        return None
    rows = rows if rows is not None else [{'address': x['address'], 'cwd': None, 'labels': {team.ROLE: x['role']}} for x in built['panes']]
    alias = _aliases(rows)
    here = _real(next((r.get('cwd') for r in rows if r['address'] == p['address']), None))
    mine = [a for a in agents if a.cwd and (_real(a.cwd) == here or (not here and a.name == role))]
    a = max(mine, key=lambda x: x.last.timestamp() if x.last else 0) if mine else None
    memory = None
    if a and a.context:
        limit = status.memory_limit(a)
        if limit:
            pct = min(100, round(100 * a.context / limit))
            memory = {'pct': pct, 'amber': pct > 75}
    recent = status.recent_actions(a.path, a.source, show_commands) if a else []
    edge_in = [e for e in built['edges'] if e['to'] == role]
    return {'role': role, 'cli': p['agent'], 'job': (roster.get(role) or {}).get('job', ''), 'state': p['state'],
            'since_s': p['since_s'], 'detail': p['detail'], 'fix': p['fix'], 'address': p['address'],
            'reachable': p.get('reachable', True), 'session': p.get('session', 'assieme'),
            'command': p.get('command'), 'memory': memory,
            'recent': [{'at': ts.astimezone().strftime('%H:%M'), 'text': _named(text, alias)} for ts, text in recent],
            'wakers': _wakers(a, alias, now) if a else [], 'empty_wakes': sum(e['empty'] for e in edge_in)}


def run_automatic(views, st, rt, log=None, now=None, tokens=None, herdr=None):
    """Carry out the fixes switched to automatic, on teams started in Assieme and on found teams the person let Assieme
    run (st['run_found']), the latter through `herdr` and only for agents in a herdr terminal. Only buttons that are fixes: a
    question is the person's to answer. One attempt per problem (fixes.problem_key), measured as it starts.
    `tokens` is role -> (tokens now, tokens when the problem began)."""
    now = now or status.now_utc()
    out = []
    with fixes.LOCK:  # a failed try counts as the attempt too: otherwise it would retry and log a row every tick
        entries = live.recent_actions(log, 500)
        for name, built in views.items():
            found = built.get('found')
            if found and not st['run_found'].get(name):
                continue
            for p in built['panes']:
                if found and not p['reachable']:
                    continue  # a plain terminal agent can only be watched
                for b in (p['fix'] or {}).get('buttons', []):
                    action = b.get('action')
                    if b['kind'] != 'action' or st['fixes'].get(action) != 'auto':
                        continue
                    if action == 'compact' and not p.get('memory_full'):
                        continue  # asking can come sooner; doing it without asking waits until memory really is full
                    started = now - dt.timedelta(seconds=p['since_s'])
                    key = fixes.problem_key(name, p['role'], action, started)
                    # since_s jitters by seconds between ticks, so a start near a minute edge may key a minute off
                    if any(fixes.attempted(fixes.problem_key(name, p['role'], action, started + dt.timedelta(minutes=k)), entries)
                           for k in (-1, 0, 1)):
                        continue
                    try:
                        if found:
                            herdr = herdr or HerdrRt()
                        (herdr if found else rt).send(p['address'], b['send'])
                        ok, detail_ = True, 'sent'
                    except Exception as e:  # a failed fix is a row in Activity, and the others still run
                        ok, detail_ = False, str(e)[:120]
                    now_t, start_t = (tokens or {}).get(p['role'], (None, None))
                    entry = live.record(action, p['role'], p['fix']['text'] + (' (team found in herdr)' if found else ''), ok, detail_, team=name, by='auto',
                                        address=p['address'], log=log, now=now, problem=key, refused=fixes.refused_command(b['send']),
                                        measured=fixes.measure(p, now_t, start_t), check_until=fixes.check_until(now),
                                        # a nudge's words are the same for every agent; a tell's can name a refused command
                                        **({'sent': b['send']} if action == 'nudge' else {}))
                    entries.append(entry)
                    out.append(entry)
    return out


class HerdrRt:
    """The person's own herdr-core, for agents started outside Assieme: the same calls, through the `hc` command."""

    def _hc(self, *args):
        r = subprocess.run(['hc', *args], capture_output=True, text=True, timeout=60)
        if r.returncode:
            raise RuntimeError((r.stderr or r.stdout).strip()[:120] or 'hc failed')
        return r.stdout

    def send(self, t, text):
        self._hc('send', t, text)

    def keys(self, t, keys):
        self._hc('keys', t, *keys)

    def read(self, t, lines=0):
        return self._hc('read', t)

    def interrupt(self, t, timeout_s=3):
        self.keys(t, ['esc'])


class Brain:
    """What the app asks about teams. One object so that `team.view`, `team.map` and the buttons see the same team."""

    def __init__(self, claude_dir, codex_dir, rt, starter, answers, herdr=None):
        self.claude_dir, self.codex_dir, self.rt, self.starter, self.answers = claude_dir, codex_dir, rt, starter, answers
        self.herdr = herdr or HerdrRt()
        self.today = Today(claude_dir, codex_dir)
        self._view = threading.local()

    @contextlib.contextmanager
    def one_view(self):
        """Within this, on this thread, logs and terminals are read once: a poll asks about every team, and each would
        read them all again. Outside it, every call is fresh, so a press always acts on what is true now."""
        self._view.seen = {}
        try:
            yield
        finally:
            self._view.seen = None

    def _once(self, key, read):
        seen = getattr(self._view, 'seen', None)
        if seen is None:
            return read()
        if key not in seen:
            seen[key] = read()
        return seen[key]

    def terminals(self):
        return self._once('herdr', status.herdr_terminals)

    def _assieme_rows(self, name=None):
        try:
            rows = self._once(('rows', name), lambda: self.rt.list({team.TEAM: name} if name else {}, all=True))
        except runtime.RuntimeError_:
            return []
        return [r for r in rows if (r.get('labels') or {}).get(team.TEAM) and (not name or r['labels'][team.TEAM] == name)]

    def _collect(self, now, herdr):
        return self._once(('agents', json.dumps(herdr, sort_keys=True, default=str)),
                          lambda: status.collect(self.claude_dir, self.codex_dir, now, herdr=herdr,
                                                 running=self._once('running', status.running_cwds)))

    def _found(self, now):
        agents = self._collect(now, self.terminals())
        taken = {r.get('cwd') for r in self._assieme_rows() if r.get('state') == 'live'}
        return agents, found_groups(agents, settings.load()['groups'], taken)

    def _resolve(self, name, now):
        """(rows, agents, found, the runtime that reaches them)."""
        rows = self._assieme_rows(name)
        if any(r.get('state') == 'live' for r in rows):
            live_rows = [r for r in self._assieme_rows() if r.get('state') == 'live']
            return rows, self._collect(now, live_rows), False, self.rt
        # A stopped team and the person's own agents in that folder usually share a name: the running ones win.
        agents, groups_ = self._found(now)
        if rows and not groups_.get(name):
            return rows, agents, False, self.rt
        return found_rows(name, groups_.get(name, [])), agents, True, self.herdr

    def _read(self, rt):
        def read(address):
            if address.startswith('log:'):
                return ''
            try:
                return rt.read(address, lines=40)
            except Exception:  # a terminal that just went away reads as empty, never as a failed poll
                return ''
        return read

    def _built(self, team, now):
        rows, agents, found, rt = self._resolve(team, now)
        built = build(team, rows, agents, now, self._read(rt), self.answers, self.today, settings.load(), found,
                      newteam.roster(team))
        return built, rows, agents

    def _map(self, team):
        return self._built(team, status.now_utc())[0]

    def map(self, team):
        """What leaves the brain: refused-command labels can hold arguments, so only the tick reads them."""
        built = self._map(team)
        for p in built['panes']:
            p.pop('refused', None)
            p.pop('turn_at', None)
        return built

    def view(self, team):
        built = self.map(team)
        return {'team': team, 'strip': built['strip'],
                'panes': [p for p in built['panes'] if p['reachable']]}

    def act(self, team, role, fix, button, by='you'):
        _, _, _, rt = self._resolve(team, status.now_utc())
        if fix == f'fixpause:{team}/{role}' and button == 'resume':  # it logs its own row; a second press does nothing
            return fixes.resume(team, role, rt, by=by)
        return panes.act(team, role, fix, button, lambda: self.map(team), rt, by=by)

    def detail(self, team, role):
        now = status.now_utc()
        rows, agents, found, rt = self._resolve(team, now)
        st = settings.load()
        roster = newteam.roster(team)
        built = build(team, rows, agents, now, self._read(rt), self.answers, self.today, st, found, roster)
        return detail(team, role, built, agents, roster, now, rows, st['show_commands'])

    def tip(self, team, tip):
        return apply_tip(team, tip, self.map(team))

    def stop(self, team_name):
        n = team.stop(self.rt, team_name)
        live.record('stop_team', team_name, f'You stopped its {n} agent(s).', team=team_name, title=f'Stopped {team_name}')
        return {'stopped': n}

    def teams(self):
        now = status.now_utc()
        _, groups_ = self._found(now)
        out, rows = {}, self._assieme_rows()
        for r in rows:
            t = out.setdefault(r['labels'][team.TEAM], {'name': r['labels'][team.TEAM], 'kind': 'assieme', 'agents': 0})
            t['agents'] += r.get('state') == 'live'
        for name, agents in groups_.items():
            if not out.get(name, {}).get('agents'):
                out[name] = {'name': name, 'kind': 'found', 'agents': len(agents)}
        found_agents = [{'name': a.name, 'cwd': _real(a.cwd), 'group': g} for g, xs in groups_.items() for a in xs]
        return {'teams': [t for t in out.values() if t['agents']], 'found_agents': found_agents}

    def change_setting(self, key, value):
        top, _, sub = key.partition('.')
        if top == 'fixes':
            settings._check(key, value)
            activity.change_fix(sub, value)
        elif top == 'practices':
            settings._check(key, value)
            activity.change_practice(sub, value, self.rt)
        else:
            settings.set_value(key, value)
        return settings.load()

    def run_found(self, team_name, on):
        old = bool(settings.load()['run_found'].get(team_name))
        settings.set_value(f'run_found.{team_name}', bool(on))
        live.record('run_found', team_name, 'You let Assieme run this team.', team=team_name, old=old, new=bool(on))
        return {'ok': True}

    def pairs(self, team_name=None):
        now = status.now_utc()
        rows = self._assieme_rows()
        entries = live.recent_actions(None, activity.MAX_ROWS)
        midnight = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        today_only = [e for e in entries if (status.parse_ts(e.get('at')) or midnight) >= midnight]
        out = []
        for p in quiet.pairs():
            names = {r['labels'][team.TEAM] for r in rows if _real(r.get('cwd')) == p['to_cwd']}
            saved = None
            for name in names:
                mine = [r for r in rows if r['labels'][team.TEAM] == name]
                by_cwd = {_real(r.get('cwd')): _aliases([r])[r['address']] for r in mine}
                rep = _team_report(mine, self.today, now)
                edge = next((e for e in rep['edges'] if e['from'] == by_cwd.get(p['from_cwd']) and e['to'] == by_cwd.get(p['to_cwd'])), None)
                saved = quiet.savings(p, today_only, edge, sum(a['tokens'] for a in rep['agents'].values()))
            out.append({**p, 'saved': saved})
        choices = [{'role': r['labels'].get(team.ROLE), 'cwd': _real(r.get('cwd'))} for r in rows
                   if r.get('state') == 'live' and (not team_name or r['labels'][team.TEAM] == team_name)]
        return {'pairs': out, 'agents': choices, 'held': sum(len(v) for v in quiet.held().values())}

    def set_pair(self, from_cwd, to_cwd, label, on):
        quiet.set_pair(from_cwd, to_cwd, label, on)
        if on:
            owner = next((r['labels'][team.TEAM] for r in self._assieme_rows() if _real(r.get('cwd')) == _real(to_cwd)), None)
            live.record('quiet', label, 'You added this in Settings.', team=owner, pair=[_real(from_cwd), _real(to_cwd), label])
        return {'ok': True}

    def set_budget(self, team, limits, at_limit='pause'):
        bud = self.map(team)['budget']
        if bud is None:
            raise ValueError('Limits are for teams started in Assieme.')
        budget.set_limits(team, limits, at_limit, self.rt, {a['role']: a['used'] for a in bud['agents']}, bud['used'])
        return self.map(team)

    def tick(self, now=None, announce=None):
        now = now or status.now_utc()
        self.today.refresh(now)
        st = settings.load()
        limited, auto = budget.teams(), 'auto' in st['fixes'].values()
        if not limited and not auto:
            return []
        names = {r['labels'][team.TEAM] for r in self._assieme_rows() if r.get('state') == 'live'}
        views = {n: self._map(n) for n in names if auto or n in limited}
        if auto:  # found teams the person let Assieme run
            views |= {n: self._map(n) for n in self._found(now)[1] if st['run_found'].get(n)}
        for n in names & limited:
            budget.check(n, views[n], self.rt, now, announce)
        if not auto:
            return []
        done = run_automatic(views, st, self.rt, now=now, tokens=self._problem_tokens(views, now), herdr=self.herdr)
        if st['fixes'].get('quiet') == 'auto':
            done += hold_automatically({n: v for n, v in views.items() if not v.get('found')}, now)
        mine = {n: v for n, v in views.items() if not v.get('found')}  # each team's outcomes through the runtime that reaches it
        return done + fixes.check_outcomes(mine, self.rt, now=now) + fixes.check_outcomes(
            {n: v for n, v in views.items() if v.get('found')}, self.herdr, now=now)

    def _problem_tokens(self, views, now):
        """role -> (tokens now, tokens when its problem began), for the panes that have a problem."""
        out = {}
        for n, built in views.items():
            if built.get('found'):
                continue
            agents = _team_report(self._assieme_rows(n), self.today, now)['agents']
            for p in built['panes']:
                if p['fix']:
                    t = (agents.get(p['role']) or {}).get('tokens')
                    began = now - dt.timedelta(seconds=p['since_s'])
                    out[p['role']] = (t, fixes.start_tokens(n, p['role'], began, t, now) if t is not None else None)
        return out

    def handlers(self):
        from . import agenttools, improve  # they read teams through this module
        return {'team.improve': lambda team: improve.check(self, team),
                'team.improve_apply': lambda team, ids: improve.apply(self, team, ids),
                'team.view': lambda team: self.view(team),
                'team.map': lambda team: self.map(team),
                'pane.act': self.act,
                'agent.detail': lambda team, role: self.detail(team, role),
                'tools.find': lambda kind, project=None, own_copy=True: agenttools.find(kind, project, own_copy),
                'tools.suggest': lambda role_kind, kind, project=None, own_copy=True:
                    agenttools.suggest(role_kind, kind, project, own_copy),
                'agent.set_tools': lambda team, role, tools_off, tools_new:
                    {'ok': True, 'id': agenttools.set_tools(self.starter.teams, team, role, tools_off, tools_new)['id']},
                'agent.restart': lambda team, role: agenttools.restart(self.starter.teams, self.rt, team, role),
                'agent.remove': lambda team, role: remove_agent(self.starter.teams, self.rt, team, role),
                'agent.tools': lambda team, role: agenttools.for_agent(self.starter.teams, team, role),
                'team.band': lambda team: newteam.band(team, self.starter.teams),
                'agent.set_musician': lambda team, role, musician: newteam.set_musician(team, role, musician, self.starter.teams),
                'tip.apply': lambda team, tip: self.tip(team, tip),
                'team.add_agent': lambda team, role: self.starter.add_agent(team, role),
                'team.stop': lambda team: self.stop(team),
                'budget.set': self.set_budget,
                'teams.list': self.teams,
                'activity.list': lambda team=None, filter='all': activity.list_rows(team, filter),
                'activity.undo': lambda id: activity.undo(id, self.rt),
                'settings.get': settings.load,
                'settings.set': lambda key, value: self.change_setting(key, value),
                'quiet.pairs': lambda team=None: self.pairs(team),
                'quiet.set': lambda from_cwd, to_cwd, label='', on=True: self.set_pair(from_cwd, to_cwd, label, on),
                'team.run_found': lambda team, on: self.run_found(team, on),
                'groups.set': lambda groups: settings.set_value('groups', groups)}


def remove_agent(teams, rt, team_name, role):
    """Remove Agent on a start that failed: stop its terminal if one runs and take it out of the saved team."""
    try:
        rows = rt.list({team.TEAM: team_name}, all=False)
    except runtime.RuntimeError_:
        rows = []
    for r in rows:
        if r.get('state') == 'live' and (r.get('labels') or {}).get(team.ROLE) == role:
            rt.kill(r['address'])
    try:
        newteam.forget_role(os.path.join(teams, team_name), role)
    except (preset.PresetError, OSError):  # no saved folder to tidy (a team from the command line): the agent itself has stopped
        pass
    live.record('stop_agent', role, "You removed it after it couldn't start. Its branch and files stay.", team=team_name)
    return {'ok': True, 'detail': ''}


def group_of(a, st_groups):
    """The found team an agent belongs to: the person's choice, else its herdr workspace, else its repository (a
    worktree counts as the project it was made from), else its folder."""
    chosen = st_groups.get(_real(a.cwd))
    if chosen:
        return chosen
    if getattr(a, 'herdr_space', None):
        return a.herdr_space
    cwd = (a.cwd or '').rstrip('/')
    marker = f'{os.sep}.assieme{os.sep}worktrees{os.sep}'
    if marker in cwd:  # the worktree of a team that's no longer running
        cwd = cwd.split(marker)[0]
    return os.path.basename(_repository(cwd)) or 'Other folders'


@functools.lru_cache(maxsize=256)
def _repository(cwd):
    """The main checkout of the repository a folder is in; the folder itself outside git. Asked once per folder: the
    team view polls every few seconds. shortcut: a folder that later moves into another repository keeps its first
    answer until the brain restarts."""
    if not cwd or not os.path.isdir(cwd):
        return cwd
    try:
        out = subprocess.run(['git', '-C', cwd, 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return cwd
    common = out.stdout.strip()
    if out.returncode != 0 or not common:
        return cwd
    return os.path.dirname(common) if os.path.basename(common) == '.git' else common


def is_own_session(a):
    """Suggested answers run the person's own agent in a folder of ours: that session isn't one of their agents."""
    return _real(a.cwd) == _real(answers.default_workdir())


def found_groups(agents, st_groups, taken_cwds):
    """Teams of agents started outside Assieme. Closed sessions don't make a team."""
    taken = {_real(c) for c in taken_cwds}
    out = {}
    for a in agents:
        if a.state != 'exited' and _real(a.cwd) not in taken and not is_own_session(a):
            out.setdefault(group_of(a, st_groups), []).append(a)
    return out


def found_rows(name, agents):
    """Agents started outside Assieme, in the shape of `hc`'s rows. Without a herdr terminal they can only be watched."""
    return [{'address': a.herdr_address or f'log:{a.name}', 'state': 'finished' if a.state == 'exited' else 'live',
             'status': {'state': a.herdr} if a.herdr else None, 'cwd': a.cwd, 'created_ms': 0,
             'argv': ['codex' if a.source == 'codex' else 'claude'],
             'labels': {team.TEAM: name, team.ROLE: a.name}} for a in agents]
