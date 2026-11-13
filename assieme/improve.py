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
    """(the step 21 import source of this team, its herdr terminals): every agent terminal of the herdr team that holds
    one of these addresses, so agents sharing a folder (one log, one row) are each seen."""
    teams, _ = herdr._running()
    for key, ts in teams.items():
        if any(t.get('address') in addresses for t in ts):
            return f'running:{key}', ts
    return None, []


def _members(rows, terminals):
    """{name: {address, cwd, row}}: each herdr terminal by its herdr name, then each log-only agent by its role."""
    out, by_address = {}, {r['address']: r for r in rows}
    for t in terminals:
        r = by_address.get(t.get('address'))
        name = t.get('name') or (r['labels'][team.ROLE] if r else t.get('address'))
        out[name] = {'address': t.get('address'), 'cwd': t.get('cwd'), 'row': r, 'terminal': t}
    seen = {m['address'] for m in out.values()}
    for r in rows:
        if r['address'] not in seen:
            out[r['labels'][team.ROLE]] = {'address': r['address'], 'cwd': r.get('cwd'), 'row': r, 'terminal': None}
    return out


def _command_line(name, t):
    """(checkout, [wide permissions]) from the command line herdr shows, or None for another program."""
    prog, args, env = herdr._split(t.get('argv') or [])
    if prog not in ('claude', 'codex'):
        prog = t.get('agent') or prog
    if prog not in ('claude', 'codex'):
        return None
    got, notes = herdr.agent_role(name, prog, args, env)
    return got['checkout'], [d for kind, _, d in notes if kind == 'wide']


def _finding(id_, title, detail, *, kind='apply', group=None, to=(), restart=False):
    return {'id': id_, 'kind': kind, 'title': title, 'detail': detail, 'group': group, 'to': list(to),
            'restart': restart, 'copy': None, 'draft': None}


def _team(brain, team_name, now, terminals):
    """(rows, agents, members, import source) of a found team, or None for a team started in Assieme."""
    rows, agents, found, _ = brain._resolve(team_name, now)
    rows = [r for r in rows if r.get('state') == 'live']
    if not found or not rows:
        return None
    source, terms = (None, terminals) if terminals is not None else _herdr_terminals({r['address'] for r in rows})
    return rows, agents, _members(rows, terms), source


def check(brain, team_name, now=None, log=None, terminals=None):
    """{team, found, findings, in_place}. `terminals`: herdr's terminals for this team, read when None."""
    now = now or status.now_utc()
    got = _team(brain, team_name, now, terminals)
    out = {'team': team_name, 'found': got is not None, 'findings': [], 'in_place': []}
    if not got:
        return out
    rows, agents, members, source = got
    reach = {n for n, m in members.items() if not m['address'].startswith('log:')}
    # A log row stands for every terminal in its folder: what its log shows is about all of them.
    in_folder = {}
    for n, m in members.items():
        in_folder.setdefault(_real(m['cwd']), []).append(n)
    names_of = {r['labels'][team.ROLE]: in_folder.get(_real(r.get('cwd')), [r['labels'][team.ROLE]]) for r in rows}
    findings, in_place = out['findings'], out['in_place']

    # Each agent its own folder (rule 2): needs a restart.
    shared = [ns for cwd, ns in in_folder.items() if cwd and len(ns) > 1]
    for ns in shared:
        findings.append(_finding(f'folder:{ns[0]}', 'Agents share one folder',
                                 f'{_names(ns)} work in the same folder, so they can edit the same files. '
                                 'Give each its own branch.', kind='review', restart=True))
    if len(in_folder) > 1 and not shared:
        in_place.append('each agent has its own folder')

    # Safe permissions (16) and read-only reviewers (14), from the command lines herdr shows: need a restart.
    lines = {n: line for n, m in members.items() if m['terminal'] and (line := _command_line(n, m['terminal']))}
    for n, (checkout, wide) in lines.items():
        if wide:
            findings.append(_finding(f'permissions:{n}', 'An agent runs with wide permissions',
                                     f'{n} runs with {", ".join(wide)}. Start it again with safe permissions: its '
                                     'own copy, and no pushes.', kind='review', restart=True))
        if herdr._kind_of(n) in ('reviewer', 'planner') and checkout != 'read-only':
            findings.append(_finding(f'reviewer:{n}', 'A reviewer can edit files',
                                     f'{n} reviews or plans but can edit files. Start it again read-only.',
                                     kind='review', restart=True))
    if lines and not any(w for _, w in lines.values()):
        in_place.append('safe permissions')
    readers = [c for n, (c, _) in lines.items() if herdr._kind_of(n) in ('reviewer', 'planner')]
    if readers and all(c == 'read-only' for c in readers):
        in_place.append('read-only reviewers')

    # What today's sessions show: wake-ups for nothing, loops, review rounds, heavy runs, refusals.
    rep = teammap._team_report(rows, brain.today, now)
    roles = set(names_of)
    talk = [e for e in rep['edges'] if e['from'] in roles and e['to'] in roles and e['from'] != e['to']]
    wasted = [x for x in rep['send_less'] if x['from'] in roles and x['to'] in roles]
    for x in wasted:
        findings.append(_finding(f"messages:{x['from']}", 'Agents wake each other for nothing',
                                 f"{x['from']} woke {x['to']} {x['wakes']} times today and {x['empty']} of those did no "
                                 f"work ({round(x['share'] * 100)}% of today’s tokens). Give {x['from']} the rules for "
                                 'fewer, cheaper messages.', group='messages', to=names_of[x['from']]))
    if talk and not wasted:
        in_place.append('no wasted wake-ups')
    seen = set()
    for f in rep['findings'] + detect.live_findings([a for a in agents if a.name in roles], now):
        group = {'loop': 'stopping', 'review': 'stopping', 'repeats': 'machine', 'overlap': 'machine'}.get(f['kind'])
        who = f.get('agent')
        if not group or who not in roles or (group, who) in seen:
            continue
        seen.add((group, who))
        title, fix = (('No stop rules', 'Add the two-attempt limit and a notification when it stops.') if group == 'stopping'
                      else ('Heavy test runs', 'Give it the rules that keep your Mac usable.'))
        findings.append(_finding(f'{group}:{who}', title, f"{f['what']}. {fix}", group=group, to=names_of[who]))
    for x in rep['refusals']:
        if x['agent'] in roles and x['count'] >= report.REPEATED_REFUSALS:
            findings.append(_finding(f"refused:{x['agent']}:{x['command']}", 'A command keeps being refused',
                                     f"{x['agent']} tried `{x['command']}` {x['count']} times and {x['why']} refused it "
                                     'each time. Check whether it should be allowed, or change the brief so it stops trying.',
                                     kind='review'))  # never a one-click allow: widening a rule is the person's call

    # The practices themselves: a team started outside Assieme was given none, unless Improve gave them.
    on = _group_rules(settings.load()['practices'])
    given = delivered(team_name, log)
    lacking = [n for n in members if any((n, g) not in given for g in on)]
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
            f['to'] = [n for n in f['to'] if any((n, g) not in given for g in groups)]
            f['groups'] = groups
            if set(f['to']) & reach:
                f['to'] = [n for n in f['to'] if n in reach]
            elif f['to']:  # outside herdr: nothing reaches it, so the person pastes it
                f['kind'], f['copy'] = 'review', note_text([on[g] for g in groups])
                one = len(f['to']) == 1
                f['detail'] += (f" Assieme can’t send {'it' if one else 'them'} messages, because {'it runs' if one else 'they run'} "
                                "in a terminal Assieme doesn’t control, so paste these rules into the next message.")
        elif f['restart']:
            f['draft'] = source
    out['findings'] = [f for f in findings if f['kind'] == 'review' or f['to']]
    return out


def apply(brain, team_name, ids, now=None, log=None, terminals=None):
    """Give each chosen finding's rules to its agents with their next turn. One action log row per finding."""
    now = now or status.now_utc()
    got = check(brain, team_name, now, log, terminals)
    members = (_team(brain, team_name, now, terminals) or (None, None, {}, None))[2]
    on = _group_rules(settings.load()['practices'])
    given = delivered(team_name, log)
    done = []
    for f in got['findings']:
        if f['id'] not in ids or f['kind'] != 'apply':
            continue
        sent, notes = [], []
        for role in f['to']:
            m = members.get(role)
            if not m or m['address'].startswith('log:') or not m.get('cwd'):
                continue
            mine = [g for g in f['groups'] if (role, g) not in given]
            text = note_text([on[g] for g in mine])
            quiet.hold_note(m['address'], m['cwd'], text, None, None)
            notes.append([m['address'], _real(m['cwd']), text])
            sent += [[role, m['address'], _real(m['cwd']), g] for g in mine]
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
