"""The Team tab: who wakes whom, what needs you, what could be saved and today's spend.

Nothing is detected here. Cards come from `panes.view` (so the same advice as the command line), counts from
`report.aggregate` run on one team's sessions, and notes from `quiet`. Polling must stay cheap: only the brain's
tick reads whole logs (`Today`); `build` uses the copy it left.
"""
import dataclasses
import datetime as dt
import os
import subprocess
import threading

from . import activity, adapters, answers, live, newteam, panes, quiet, report, runtime, settings, status, team
from .model import HUMAN

GROUPS_ABOVE = 8           # agents; above this the map draws groups
REFRESH_S = 120
NOT_DONE_FOR_YOU = ('menu', 'answer', 'deliver', 'undo')
WORDS = ('No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten')
NEEDS_YOU = ('asking', 'stuck', 'waiting')


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
    return out


class Today:
    """Today's sessions, refreshed by the brain's tick at most once per `every_s`. A failed read keeps the last copy."""

    def __init__(self, claude_dir, codex_dir, every_s=REFRESH_S):
        self.claude_dir, self.codex_dir, self.every_s = claude_dir, codex_dir, every_s
        self.lock, self.got, self.at = threading.Lock(), [], None

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
            self.got = got

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
    need = sum(p['state'] in NEEDS_YOU for p in v['panes'])
    done = [r for r in activity.list_rows(team=team_name, now=now)['rows'] if r['action'] not in NOT_DONE_FOR_YOU][:2]
    return {'team': team_name, 'found': found,
            'stopped': not any(r.get('state') == 'live' for r in rows),
            'title': 'All quiet' if not need else f"{_word(need)} agent{'s' * (need != 1)} need{'s' * (need == 1)} you",
            'subtitle': _subtitle(v['panes'], gs, need),
            'panes': v['panes'], 'strip': v['strip'], 'nodes': nodes, 'edges': edges, 'waits': waits, 'groups': gs,
            'tips': tips, 'done': done,
            'spend': {'tokens': total, 'waste_share': round(rep['empty_tokens'] / total, 4)} if total else None}


def apply_tip(team_name, tip_id, built, now=None):
    t = next((x for x in built['tips'] if x['id'] == tip_id), None)
    if not t:
        return {'ok': False, 'detail': 'This tip has changed. Take another look.'}
    label = f"{t['from']} to {t['to']}"
    quiet.set_pair(t['from_cwd'], t['to_cwd'], label)
    live.record('quiet', label, f"{round(t['share'] * 100)}% of today's tokens went on these wake-ups.", team=team_name, now=now,
                pair=[t['from_cwd'], t['to_cwd'], label])
    return {'ok': True, 'detail': ''}


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


def run_automatic(views, st, rt, log=None, now=None):
    """Carry out the fixes switched to automatic, on teams started in Assieme only. Only buttons that are fixes: a
    question is the person's to answer. One of each per agent per live.ACTED_HIDE."""
    now = now or status.now_utc()
    # A failed try waits too: otherwise it would retry and log a row every tick.
    recent = [e for e in live.recent_actions(log, 200) if status.parse_ts(e.get('at'))
              and (now - status.parse_ts(e['at'])).total_seconds() <= live.ACTED_HIDE]
    done = {(e.get('team'), e.get('agent'), e.get('action')) for e in recent}
    out = []
    for name, built in views.items():
        if built.get('found'):
            continue
        for p in built['panes']:
            for b in (p['fix'] or {}).get('buttons', []):
                action = b.get('action')
                if b['kind'] != 'action' or st['fixes'].get(action) != 'auto' or (name, p['role'], action) in done:
                    continue
                try:
                    rt.send(p['address'], b['send'])
                    ok, detail_ = True, 'sent'
                except Exception as e:  # a failed fix is a row in Activity, and the others still run
                    ok, detail_ = False, str(e)[:120]
                out.append(live.record(action, p['role'], p['fix']['text'], ok, detail_, team=name, by='auto',
                                       address=p['address'], log=log, now=now))
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


class Brain:
    """What the app asks about teams. One object so that `team.view`, `team.map` and the buttons see the same team."""

    def __init__(self, claude_dir, codex_dir, rt, starter, answers, herdr=None):
        self.claude_dir, self.codex_dir, self.rt, self.starter, self.answers = claude_dir, codex_dir, rt, starter, answers
        self.herdr = herdr or HerdrRt()
        self.today = Today(claude_dir, codex_dir)

    def _assieme_rows(self, name=None):
        try:
            rows = self.rt.list({team.TEAM: name} if name else {}, all=True)
        except runtime.RuntimeError_:
            return []
        return [r for r in rows if (r.get('labels') or {}).get(team.TEAM) and (not name or r['labels'][team.TEAM] == name)]

    def _collect(self, now, herdr):
        return status.collect(self.claude_dir, self.codex_dir, now, herdr=herdr, running=status.running_cwds())

    def _found(self, now):
        agents = self._collect(now, status.herdr_terminals())
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

    def map(self, team):
        now = status.now_utc()
        rows, agents, found, rt = self._resolve(team, now)
        return build(team, rows, agents, now, self._read(rt), self.answers, self.today, settings.load(), found,
                     newteam.roster(team))

    def view(self, team):
        built = self.map(team)
        return {'team': team, 'strip': built['strip'],
                'panes': [p for p in built['panes'] if p['reachable']]}

    def act(self, team, role, fix, button):
        _, _, _, rt = self._resolve(team, status.now_utc())
        return panes.act(team, role, fix, button, lambda: self.map(team), rt)

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
            live.record('quiet', label, 'You added this in Settings.', pair=[_real(from_cwd), _real(to_cwd), label])
        return {'ok': True}

    def tick(self, now=None):
        now = now or status.now_utc()
        self.today.refresh(now)
        st = settings.load()
        if 'auto' not in st['fixes'].values():
            return []
        names = {r['labels'][team.TEAM] for r in self._assieme_rows() if r.get('state') == 'live'}
        return run_automatic({n: self.map(n) for n in names}, st, self.rt, now=now)

    def handlers(self):
        return {'team.view': lambda team: self.view(team),
                'team.map': lambda team: self.map(team),
                'pane.act': self.act,
                'agent.detail': lambda team, role: self.detail(team, role),
                'tip.apply': lambda team, tip: self.tip(team, tip),
                'team.add_agent': lambda team, role: self.starter.add_agent(team, role),
                'team.stop': lambda team: self.stop(team),
                'teams.list': self.teams,
                'activity.list': lambda team=None, filter='all': activity.list_rows(team, filter),
                'activity.undo': lambda id: activity.undo(id, self.rt),
                'settings.get': settings.load,
                'settings.set': lambda key, value: self.change_setting(key, value),
                'quiet.pairs': lambda team=None: self.pairs(team),
                'quiet.set': lambda from_cwd, to_cwd, label='', on=True: self.set_pair(from_cwd, to_cwd, label, on),
                'groups.set': lambda groups: settings.set_value('groups', groups)}


def group_of(a, st_groups):
    """The found team an agent belongs to: the person's choice, else its project folder."""
    chosen = st_groups.get(_real(a.cwd))
    if chosen:
        return chosen
    cwd = (a.cwd or '').rstrip('/')
    marker = f'{os.sep}.assieme{os.sep}worktrees{os.sep}'
    if marker in cwd:  # the worktree of a team that's no longer running
        cwd = cwd.split(marker)[0]
    return os.path.basename(cwd) or 'Other folders'


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
