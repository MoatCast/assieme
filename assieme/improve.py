"""Improve this team: a team started outside Assieme, compared with the recommended setup.

Nothing new is detected here. Findings come from what already runs: the found agents' folders, step 21's reading of
herdr command lines (`herdr.agent_role`), and `report.aggregate` / `detect` on the team's sessions today.

Apply gives practice rules to running agents with their next turn, as a held note through the person's own herdr
core: no agent is typed into mid-work or restarted. Anything that needs a restart (folders, permissions, read-only
reviewers) is Review only; its fix is a New team draft of the same agents, started by the person's click.
Each Apply is one `improve` row in the action log; its Undo is in `activity`.
"""
import os

from . import activity, detect, herdr, live, preset, quiet, report, settings, status, team, teammap


def _real(path):
    return os.path.realpath(path) if path else None


def _names(roles):
    return roles[0] if len(roles) == 1 else ', '.join(roles[:-1]) + f' and {roles[-1]}'


def _group_rules(chosen):
    """{group id: (title, [rule text])} for the rules that are on, in the list's order."""
    on = {r['id'] for r in preset.rules_on(chosen)}
    out = {}
    for g in preset.practices()['groups']:
        texts = [r['text'] for r in g['rules'] if r['id'] in on]
        if texts:
            out[g['id']] = (g['title'], texts)
    return out


def note_text(groups):
    """What an agent is sent: the rules of these groups, as one note."""
    lines = [f'- {t}' for _, texts in groups for t in texts]
    return 'assieme: your owner added these team practices. From your next task, follow them:\n' + '\n'.join(lines)


def delivered(team_name, log=None):
    """{(role, group id)} given to this team by an Apply that wasn't undone."""
    entries = live.recent_actions(log, activity.MAX_ROWS)
    undone = activity._undone(entries)
    return {(role, group) for e in entries if e.get('action') == 'improve' and e.get('team') == team_name
            and e.get('ok', True) and e.get('id') not in undone for role, _, _, group in e.get('sent') or []}


def _herdr_terminals(addresses):
    """(the step 21 import source of this team, {address: terminal}) for its agents running in herdr."""
    teams, _ = herdr._running()
    for key, ts in teams.items():
        mine = {t['address']: t for t in ts if t.get('address') in addresses}
        if mine:
            return f'running:{key}', mine
    return None, {}


def _command_lines(rows, terminals):
    """{role: (checkout, [wide permissions])} for agents whose command line herdr shows."""
    out = {}
    for r in rows:
        t = terminals.get(r['address'])
        if not t:
            continue
        prog, args, env = herdr._split(t.get('argv') or [])
        if prog not in ('claude', 'codex'):
            prog = t.get('agent') or prog
        if prog not in ('claude', 'codex'):
            continue
        role = r['labels'][team.ROLE]
        got, notes = herdr.agent_role(role, prog, args, env)
        out[role] = (got['checkout'], [d for kind, _, d in notes if kind == 'wide'])
    return out


def _finding(id_, title, detail, *, kind='apply', group=None, to=(), restart=False):
    return {'id': id_, 'kind': kind, 'title': title, 'detail': detail, 'group': group, 'to': list(to),
            'restart': restart, 'copy': None, 'draft': None}


def check(brain, team_name, now=None, log=None, terminals=None):
    """{team, found, findings, in_place}. `terminals`: herdr's terminals for this team, read when None."""
    now = now or status.now_utc()
    rows, agents, found, _ = brain._resolve(team_name, now)
    out = {'team': team_name, 'found': found, 'findings': [], 'in_place': []}
    rows = [r for r in rows if r.get('state') == 'live']
    if not found or not rows:
        return out
    by_role = {r['labels'][team.ROLE]: r for r in rows}
    roles = list(by_role)
    reach = {role for role, r in by_role.items() if not r['address'].startswith('log:')}
    source, terms = (None, terminals) if terminals is not None else _herdr_terminals({r['address'] for r in rows})
    findings, in_place = out['findings'], out['in_place']

    # Each agent its own folder (rule 2): needs a restart.
    by_cwd = {}
    for role, r in by_role.items():
        if r.get('cwd'):
            by_cwd.setdefault(_real(r['cwd']), []).append(role)
    shared = [rs for rs in by_cwd.values() if len(rs) > 1]
    for rs in shared:
        findings.append(_finding(f'folder:{rs[0]}', 'Agents share one folder',
                                 f'{_names(rs)} work in the same folder, so they can edit the same files. '
                                 'Give each its own branch.', kind='review', restart=True))
    if len(by_cwd) > 1 and not shared:
        in_place.append('each agent has its own folder')

    # Safe permissions (16) and read-only reviewers (14), from the command lines herdr shows: need a restart.
    lines = _command_lines(rows, terms)
    for role, (checkout, wide) in lines.items():
        if wide:
            findings.append(_finding(f'permissions:{role}', 'An agent runs with wide permissions',
                                     f'{role} runs with {", ".join(wide)}. Start it again with safe permissions: its '
                                     'own copy, and no pushes.', kind='review', restart=True))
        if herdr._kind_of(role) in ('reviewer', 'planner') and checkout != 'read-only':
            findings.append(_finding(f'reviewer:{role}', 'A reviewer can edit files',
                                     f'{role} reviews or plans but can edit files. Start it again read-only.',
                                     kind='review', restart=True))
    if lines and not any(w for _, w in lines.values()):
        in_place.append('safe permissions')
    readers = [c for role, (c, _) in lines.items() if herdr._kind_of(role) in ('reviewer', 'planner')]
    if readers and all(c == 'read-only' for c in readers):
        in_place.append('read-only reviewers')

    # What today's sessions show: wake-ups for nothing, loops, review rounds, heavy runs, refusals.
    rep = teammap._team_report(rows, brain.today, now)
    names = set(roles)
    talk = [e for e in rep['edges'] if e['from'] in names and e['to'] in names and e['from'] != e['to']]
    wasted = [x for x in rep['send_less'] if x['from'] in names and x['to'] in names]
    for x in wasted:
        findings.append(_finding(f"messages:{x['from']}", 'Agents wake each other for nothing',
                                 f"{x['from']} woke {x['to']} {x['wakes']} times today and {x['empty']} of those did no "
                                 f"work ({round(x['share'] * 100)}% of today’s tokens). Give {x['from']} the rules for "
                                 'fewer, cheaper messages.', group='messages', to=[x['from']]))
    if talk and not wasted:
        in_place.append('no wasted wake-ups')
    seen = set()
    for f in rep['findings'] + detect.live_findings([a for a in agents if a.name in names], now):
        group = {'loop': 'stopping', 'review': 'stopping', 'repeats': 'machine', 'overlap': 'machine'}.get(f['kind'])
        who = f.get('agent')
        if not group or who not in names or (group, who) in seen:
            continue
        seen.add((group, who))
        title, fix = (('No stop rules', 'Add the two-attempt limit and a notification when it stops.') if group == 'stopping'
                      else ('Heavy test runs', 'Give it the rules that keep your Mac usable.'))
        findings.append(_finding(f'{group}:{who}', title, f"{f['what']}. {fix}", group=group, to=[who]))
    for x in rep['refusals']:
        if x['agent'] in names and x['count'] >= report.REPEATED_REFUSALS:
            advice = x['advice'][:1].upper() + x['advice'][1:] + '.' if x['advice'] else 'Change the rule or the brief.'
            findings.append(_finding(f"refused:{x['agent']}:{x['command']}", 'A command keeps being refused',
                                     f"{x['agent']} tried `{x['command']}` {x['count']} times and {x['why']} refused it "
                                     f'each time. {advice}', kind='review'))

    # The practices themselves: a team started outside Assieme was given none, unless Improve gave them.
    on = _group_rules(settings.load()['practices'])
    given = delivered(team_name, log)
    lacking = [role for role in roles if any((role, g) not in given for g in on)]
    if on and lacking:
        findings.append(_finding('practices', 'Agents don’t have the team practices',
                                 f'{_names(lacking)} started outside Assieme, so {"it doesn’t" if len(lacking) == 1 else "they don’t"} '
                                 f'follow the {len(on)} practice group{"s" * (len(on) != 1)} you have on.',
                                 group='*', to=lacking))
    elif on:
        in_place.append('team practices')

    for f in findings:
        if f['kind'] == 'apply':
            groups = list(on) if f['group'] == '*' else [f['group']] if f['group'] in on else []
            f['to'] = [r for r in f['to'] if any((r, g) not in given for g in groups)]
            f['groups'] = groups
            if set(f['to']) & reach:
                f['to'] = [r for r in f['to'] if r in reach]
            elif f['to']:  # outside herdr: nothing reaches it, so the person pastes it
                f['kind'], f['copy'] = 'review', note_text([on[g] for g in groups])
                f['detail'] += f" Assieme can’t reach {_names(f['to'])}: it isn’t running in herdr, so paste these rules into its next message."
        elif f['restart']:
            f['draft'] = source
    out['findings'] = [f for f in findings if f['kind'] == 'review' or f['to']]
    return out


def apply(brain, team_name, ids, now=None, log=None, terminals=None):
    """Give each chosen finding's rules to its agents with their next turn. One action log row per finding."""
    got = check(brain, team_name, now, log, terminals)
    rows = {r['labels'][team.ROLE]: r for r in brain._resolve(team_name, now or status.now_utc())[0] if r.get('state') == 'live'}
    on = _group_rules(settings.load()['practices'])
    given = delivered(team_name, log)
    done = []
    for f in got['findings']:
        if f['id'] not in ids or f['kind'] != 'apply':
            continue
        sent, notes = [], []
        for role in f['to']:
            r = rows.get(role)
            if not r or r['address'].startswith('log:') or not r.get('cwd'):
                continue
            mine = [g for g in f['groups'] if (role, g) not in given]
            text = note_text([on[g] for g in mine])
            quiet.hold_note(r['address'], r['cwd'], text, None, None)
            notes.append([r['address'], _real(r['cwd']), text])
            sent += [[role, r['address'], _real(r['cwd']), g] for g in mine]
            given |= {(role, g) for g in mine}
        if not sent:
            continue
        who = sorted({s[0] for s in sent})
        what = 'the team practices' if f['group'] == '*' else f'“{on[f["group"]][0]}”'
        e = live.record('improve', _names(who), f['detail'], team=team_name, log=log, now=now,
                        title=f'Gave {_names(who)} {what}', sent=sent, notes=notes)
        done.append({'id': f['id'], 'entry': e['id']})
    return {'ok': True, 'applied': done}


def undo(e, rt):
    """A note still held is taken back; one already delivered is followed by a note that it no longer applies."""
    titles = {g['id']: g['title'] for g in preset.practices()['groups']}
    held = [n for ns in quiet.held().values() for n in ns]
    for address, cwd, text in e.get('notes') or []:
        mine = [n for n in held if (n['to'], n['to_cwd'], n['text']) == (address, cwd, text)]
        if mine:
            quiet._drop(mine)
            continue
        groups = [titles.get(g, g) for _, a, c, g in e.get('sent') or [] if (a, c) == (address, cwd)]
        quiet.hold_note(address, cwd, 'assieme: your owner took back these team practices, so you no longer need to '
                        'follow them: ' + ', '.join(f'“{t}”' for t in groups) + '.', None, None)
