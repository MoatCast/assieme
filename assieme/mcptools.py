"""What the MCP server asks the brain. `assieme mcp` is a thin process over these `mcp.*` methods; the rules on who
may do what without a question live with it (a question needs the client), and the actions themselves live here."""
import json
import os
import subprocess
import uuid

from . import brief, live, newteam, panes, preset, quiet, reports, settings, status, team as team_, teammap

READ_MAX_LINES = 2000
READ_MAX_CHARS = 50_000


def _pane(p):
    fix = p.get('fix') or {}
    out = {'agent': p['role'], 'cli': p['agent'], 'state': p['state'], 'since_seconds': p['since_s'], 'detail': p['detail']}
    if fix:
        out['about'] = fix['text']
        out['can_answer_with'] = [b['label'] for b in fix['buttons'] if b['kind'] in ('menu', 'answer')]
        out['fixes'] = [b['action'] for b in fix['buttons'] if b['kind'] == 'action']
    return out


class Tools:
    def __init__(self, brain, starter, teams=None, opener=None, notify=live.notify):
        self.brain, self.starter, self.notify = brain, starter, notify
        self.teams = teams or newteam.teams_dir()
        self.opener = opener or (lambda: subprocess.run(['open', '-b', 'dev.assieme.app'], capture_output=True, timeout=10).returncode == 0)

    # --- agents inside Assieme ---------------------------------------------------------------------------

    def _me(self, address):
        info = self.brain.rt.get(address)
        labels = info.get('labels') or {}
        if not labels.get(team_.TEAM):
            raise ValueError('This terminal is not an agent of an Assieme team.')
        return labels[team_.TEAM], labels.get(team_.ROLE) or address, info

    def _live(self, team):
        return [r for r in self.brain._assieme_rows(team) if r.get('state') == 'live']

    def teammates(self, address):
        name, me, _ = self._me(address)
        roster = newteam.roster(name, self.teams)
        out = []
        for r in self._live(name):
            role = r['labels'].get(team_.ROLE)
            if role != me:
                out.append({'name': role, 'job': (roster.get(role) or {}).get('job', ''),
                            'reports_to': (roster.get(role) or {}).get('parent'),
                            'status': (r.get('status') or {}).get('state')})
        return {'team': name, 'you': me, 'teammates': out}

    def message(self, address, to, text):
        name, me, info = self._me(address)
        rows = self._live(name)
        target = next((r for r in rows if r['labels'].get(team_.ROLE) == to), None)
        if not target:
            names = ', '.join(r['labels'].get(team_.ROLE) for r in rows if r['labels'].get(team_.ROLE) != me)
            raise ValueError(f'No running agent named {to}. Your teammates: {names or "none"}.')
        if target['labels'].get(team_.BRIEFED) != '1':  # a message to a booting agent is dropped
            raise ValueError(f'{to} is still starting. Send this when it has started.')
        rt = self.brain.rt
        if (info.get('cwd') and target.get('cwd') and quiet.is_quiet(info['cwd'], target['cwd'])
                and not quiet.urgent(text) and quiet.deliverer_alive()):
            quiet.hold_note(target['address'], target['cwd'], text, rt.session, rt.sock, sender=me, from_cwd=quiet._real(info['cwd']))
            return {'sent': 'held', 'detail': f'{to} gets this with its next turn. It was not woken.'}
        reports.noted(name, to)
        # The marker the logs already read, so the map still shows who woke whom.
        rt.send(target['address'], f'<hc from="{address} {me}">{text}</hc>')
        return {'sent': 'now', 'detail': f'Sent to {to}.'}

    def report(self, address, state, note=''):
        name, me, info = self._me(address)
        reports.save(name, me, state, note, turn=reports.running_turn(info))
        if state == 'need_decision' and settings.load()['notify']['needs_you']:
            self.notify(f'{me} needs a decision', note[:200])
        return {'ok': True, 'detail': 'Reported. End your turn now if you cannot go on.' if state in reports.SHOWN else 'Reported.'}

    def team_brief(self, address):
        name, _, _ = self._me(address)
        return {'team': name, 'brief': brief.text(name) or 'Nothing in the team brief yet.'}

    def record_decision(self, address, question, answer):
        name, me, _ = self._me(address)
        brief.add(name, me, question, answer, by='agent')
        return {'ok': True, 'detail': 'Saved in the team brief.'}

    # --- reading -----------------------------------------------------------------------------------------

    def _saved(self):
        try:
            return sorted(d for d in os.listdir(self.teams) if os.path.isfile(os.path.join(self.teams, d, 'team.toml')))
        except OSError:
            return []

    def list_teams(self):
        running = self.brain.teams()['teams']
        here = {t['name'] for t in running}
        return {'running': running, 'saved_and_stopped': [n for n in self._saved() if n not in here]}

    def status(self, team):
        built = self.brain.map(team)
        if not built['panes']:
            raise ValueError(f'No team named {team} is running.')
        return {'team': team, 'title': built['title'], 'summary': built['subtitle'], 'stopped': built['stopped'],
                'started_outside_assieme': built['found'], 'agents': [_pane(p) for p in built['panes']],
                'tokens_today': (built['spend'] or {}).get('tokens')}

    def needs_you(self):
        out = []
        for t in self.brain.teams()['teams']:
            out += [{'team': t['name']} | _pane(p) for p in self.brain.map(t['name'])['panes'] if p['state'] in teammap.NEEDS_YOU]
        return {'needs_you': out}

    def _pane_of(self, team, role):
        p = next((x for x in self.brain.map(team)['panes'] if x['role'] == role), None)
        if not p:
            raise ValueError(f'No agent named {role} in {team}.')
        return p

    def _reach(self, team, role):
        p = self._pane_of(team, role)
        if not p.get('reachable', True):
            raise ValueError(f"{role} wasn't started in a terminal Assieme can reach, so it can only be watched.")
        return p, self.brain._resolve(team, status.now_utc())[3]

    def read(self, team, role, lines=200):
        p, rt = self._reach(team, role)
        n = max(1, min(int(lines), READ_MAX_LINES))
        text = '\n'.join((rt.read(p['address'], lines=n) or '').splitlines()[-n:])
        return {'text': text[-READ_MAX_CHARS:]}

    # --- acting (the MCP process has already got the person's yes, or the setting says Automatic) --------

    def send(self, team, role, text):
        p, rt = self._reach(team, role)
        rt.send(p['address'], text)
        live.record('send', role, 'You approved this from your assistant.', team=team, address=p['address'])
        return {'ok': True, 'detail': f'Sent to {role}.'}

    def answer(self, team, role, answer):
        p, rt = self._reach(team, role)
        fix = p.get('fix')
        if p['state'] != 'asking' or not fix:
            return {'ok': False, 'detail': f"{role} isn't asking anything right now."}
        choices = [b for b in fix['buttons'] if b['kind'] in ('menu', 'answer')]
        wanted = answer.strip().lower()
        b = next((x for x in choices if wanted in (x['label'].lower(), (x['send'] or '').lower())), None)
        if b:
            return self.brain.act(team, role, fix['id'], b['id'])
        if any(x['kind'] == 'menu' for x in choices):
            return {'ok': False, 'detail': 'Choose one of: ' + ', '.join(x['label'] for x in choices)}
        rt.send(p['address'], answer)
        live.record('answer', role, panes.ANSWERED, team=team, address=p['address'])
        if fix.get('question'):
            brief.add(team, role, fix['question'], answer)
        return {'ok': True, 'detail': 'sent'}

    def _fix_button(self, p, action):
        return next((b for b in (p.get('fix') or {}).get('buttons', []) if b.get('action') == action), None)

    def fix_check(self, team, role, action):
        """Whether this fix is on offer right now, and whether its setting lets it run without asking."""
        built = self.brain.map(team)
        p = next((x for x in built['panes'] if x['role'] == role), None)
        if not p or not self._fix_button(p, action):
            return {'available': False, 'why': f"{role} doesn't need this right now."}
        # The same limits as teammap.run_automatic.
        auto = (settings.load()['fixes'].get(action) == 'auto' and not built['found']
                and (action != 'compact' or p.get('memory_full')))
        return {'available': True, 'auto': bool(auto), 'text': p['fix']['text']}

    def fix(self, team, role, action, by='you'):
        p = self._pane_of(team, role)
        b = self._fix_button(p, action)
        if not b:
            return {'ok': False, 'detail': f"{role} doesn't need this right now."}
        return self.brain.act(team, role, p['fix']['id'], b['id'], by=by)

    def stop_agent(self, team, role):
        row = next((r for r in self._live(team) if r['labels'].get(team_.ROLE) == role), None)
        if not row:
            raise ValueError(f'No running agent named {role} in {team}.')
        self.brain.rt.kill(row['address'])
        try:
            newteam.forget_role(os.path.join(self.teams, team), role)
        except (preset.PresetError, OSError):
            pass  # no saved folder to tidy: the agent itself has stopped
        live.record('stop_agent', role, 'You approved this from your assistant. Its branch and files stay.', team=team, address=row['address'])
        return {'ok': True, 'detail': f'Stopped {role}. Its branch and files stay.'}

    def start_saved(self, team, task=''):
        """Start a stopped team again. A new team only ever starts from New team, with the person's click."""
        try:
            tm = preset.load(os.path.join(self.teams, team), practices=False)
        except (preset.PresetError, OSError):
            raise ValueError(f'No saved team named {team}.') from None
        return self.starter.start(tm.project, task, [newteam._role_dict(r) for r in tm.roles], tm.practices)

    # --- drafts ------------------------------------------------------------------------------------------

    def _drafts(self):
        return os.path.join(quiet.state_dir(), 'drafts')

    def draft_save(self, project, roles, task='', practices=None, why=''):
        """Checked like New team's form. It starts nothing: the app opens it in New team for the person to Start."""
        check = newteam.project_check(project)
        if not check['ok']:
            raise newteam.NewTeamError('project', check['reason'])
        filled = []
        for r in roles:
            r = dict(r)
            kind = r.pop('kind', None)
            if kind in newteam.KINDS:
                r.setdefault('checkout', newteam.KINDS[kind][1])
                if not (r.get('brief') or '').strip():
                    r['brief'] = newteam.kind_brief(kind)
            r.setdefault('checkout', 'branch')
            r.setdefault('job', '')
            r.setdefault('parent', None)
            filled.append(r)
        newteam.validate(filled)
        chosen = settings.load()['practices'] | (practices or {})
        unknown = set(chosen) - {g['id'] for g in preset.practices()['groups']}
        if unknown:
            raise ValueError(f'Unknown practice: {", ".join(sorted(unknown))}.')
        id_ = uuid.uuid4().hex[:12]
        quiet._write_json(os.path.join(self._drafts(), f'{id_}.json'),
                          {'id': id_, 'project': os.path.abspath(project), 'task': task, 'roles': filled,
                           'practices': chosen, 'why': why})
        try:
            opened = self.opener()
        except (OSError, subprocess.SubprocessError):
            opened = False
        return {'id': id_, 'opened': opened}

    def draft_next(self):
        """The oldest draft waiting to open, or None. The app asks often, so this only lists a folder."""
        try:
            names = [os.path.join(self._drafts(), n) for n in os.listdir(self._drafts()) if n.endswith('.json')]
        except OSError:
            return None
        ages = {}
        for path in names:
            try:
                ages[path] = os.path.getmtime(path)
            except OSError:  # the app took it just now
                pass
        return os.path.basename(min(ages, key=ages.get))[:-5] if ages else None

    def draft_take(self, id):
        path = os.path.join(self._drafts(), f'{os.path.basename(id)}.json')
        try:
            with open(path) as f:
                draft = json.load(f)
        except OSError:
            return None
        except ValueError:
            draft = None
        if not isinstance(draft, dict):  # kept to look at, but no longer in the way of the next draft
            os.replace(path, path + '.damaged')
            return None
        os.remove(path)
        return draft

    def handlers(self):
        return {'mcp.teammates': self.teammates, 'mcp.message': self.message, 'mcp.report': self.report,
                'mcp.list_teams': self.list_teams, 'mcp.status': self.status, 'mcp.needs_you': self.needs_you,
                'mcp.read': self.read, 'mcp.send': self.send, 'mcp.answer': self.answer,
                'mcp.fix_check': self.fix_check, 'mcp.fix': self.fix, 'mcp.stop_agent': self.stop_agent,
                'mcp.start_saved': self.start_saved, 'mcp.draft_save': self.draft_save, 'draft.take': self.draft_take,
                'mcp.brief': self.team_brief, 'mcp.record_decision': self.record_decision,
                'brief.get': brief.load, 'brief.save': brief.save, 'brief.remove': brief.remove}
