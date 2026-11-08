"""Activity: the action log as rows in plain words, and Undo.

Undo exists only where it really undoes something. A message already delivered, or memory already freed, has none.
Another step adds its own by putting an entry in UNDO; the brain checks the time limit, never the app.
"""

import os

from . import live, newteam, preset, quiet, settings, status, team

UNDO_S = 24 * 3600
MAX_ROWS = 500
NO_WORDS = ('menu', 'answer')  # older rows of these held the agent's question: it stays out of what is shown
TITLES = {'quiet': 'Started holding notes from {agent}',
          'compact': 'Freed up memory for {agent}', 'nudge': 'Asked {agent} to try another way',
          'tell': 'Told {agent} why a command is blocked', 'add_agent': 'Added {agent}',
          'menu': 'Answered {agent}', 'answer': 'Answered {agent}', 'deliver': 'Delivered held notes to {agent}'}


def _entries(path=None):
    return live.recent_actions(path, MAX_ROWS)


def _title(e):
    if e.get('title'):
        return e['title']
    action = e.get('action')
    if action == 'fix_mode':
        return f'Set "{e.get("fix")}" to {"Automatic" if e.get("new") == "auto" else "Ask first"}'
    if action == 'practice':
        return f'Turned {e.get("agent")} {"on" if e.get("new") else "off"}'
    if action == 'undo':
        return f'Undid: {e.get("undid", "an action")}'
    return TITLES.get(action, f'{action} {{agent}}').format(agent=e.get('agent'))


def _undo_quiet(e, rt):
    a, b, label = e['pair']
    quiet.set_pair(a, b, label, on=False)  # notes already held are still delivered


def _undo_fix(e, rt):
    settings.set_value(f'fixes.{e["fix"]}', e['old'])


def _undo_practice(e, rt):
    _apply_practice(e['group'], e['old'], rt)


def _undo_add(e, rt):
    labels = rt.get(e['address']).get('labels') or {}
    if labels.get(team.TEAM) != e.get('team') or labels.get(team.ROLE) != e.get('agent'):
        raise ValueError('That terminal is no longer this agent.')  # only remove terminals you started
    rt.kill(e['address'])
    try:
        newteam.forget_role(os.path.join(newteam.teams_dir(), e['team']), e['agent'])
    except (preset.PresetError, OSError):
        pass  # no saved folder to tidy: the agent itself has stopped


# action -> (fn(entry, rt), button label, seconds it stays available or None for as long as it applies)
UNDO = {'quiet': (_undo_quiet, 'Stop', None),
        'fix_mode': (_undo_fix, 'Undo', UNDO_S),
        'practice': (_undo_practice, 'Undo', UNDO_S),
        'add_agent': (_undo_add, 'Stop', UNDO_S)}


def _still_applies(e):
    return e.get('action') != 'quiet' or quiet.is_quiet(e['pair'][0], e['pair'][1])


def _available(e, undone, now):
    """Why this entry can't be undone now, or None if it can."""
    u = UNDO.get(e.get('action'))
    if not u or not e.get('id') or not e.get('ok', True):
        return "This can't be undone."
    if e['id'] in undone:
        return 'This was already undone.'
    at = status.parse_ts(e.get('at'))
    if u[2] and (not at or (now - at).total_seconds() > u[2]):
        return 'It has been more than 24 hours, so this can no longer be undone.'
    if not _still_applies(e):
        return 'This has already stopped.'
    return None


def list_rows(team=None, filter='all', path=None, now=None):  # `filter` is the brain method's parameter name
    now = now or status.now_utc()
    entries = _entries(path)
    undone = {e.get('of') for e in entries if e.get('action') == 'undo'}
    rows = []
    for e in reversed(entries):
        by = e.get('by', 'you')
        if (filter != 'all' and by != filter) or (team and e.get('team') not in (None, team)):
            continue
        at = status.parse_ts(e.get('at'))
        local = at.astimezone() if at else None
        reason = '' if e.get('action') in NO_WORDS and 'id' not in e else (e.get('reason') or '')
        if not e.get('ok', True):
            reason = f"It didn't work: {e.get('detail') or 'no reason given'}"
        can = _available(e, undone, now) is None
        rows.append({'id': e.get('id'), 'at': local.strftime('%H:%M') if local else '', 'day': local.strftime('%Y-%m-%d') if local else '',
                     'title': _title(e), 'reason': reason, 'by': by, 'ok': e.get('ok', True), 'agent': e.get('agent'), 'action': e.get('action'),
                     'undo': {'label': UNDO[e['action']][1]} if can else None})
    return {'rows': rows}


def undo(id, rt, path=None, now=None):
    now = now or status.now_utc()
    entries = _entries(path)
    e = next((x for x in entries if x.get('id') == id), None)
    if not e:
        return {'ok': False, 'detail': "This can't be undone."}
    why = _available(e, {x.get('of') for x in entries if x.get('action') == 'undo'}, now)
    if why:
        return {'ok': False, 'detail': why}
    try:
        UNDO[e['action']][0](e, rt)
    except Exception as err:  # a failed Undo is a message on the row, and stays available to try again
        return {'ok': False, 'detail': str(err)[:120]}
    live.record('undo', e.get('agent'), 'You undid this.', team=e.get('team'), log=path, now=now, of=id, undid=_title(e))
    return {'ok': True, 'detail': ''}


def _apply_practice(group, on, rt):
    settings.set_value(f'practices.{group}', on)
    g = next((x for x in preset.practices()['groups'] if x['id'] == group), None)
    if not g:
        return
    text = f"assieme: the team practice \"{g['title']}\" is now {'on' if on else 'off'}."
    text += (' From your next task, follow these:\n' + '\n'.join(f'- {r}' for r in g['rules'])) if on else ' You no longer need to follow it.'
    try:
        rows = rt.list({}, all=False)
    except Exception:  # no core running means no agents to tell
        return
    for r in rows:
        if r.get('state') == 'live' and team.TEAM in (r.get('labels') or {}) and r.get('cwd'):
            quiet.hold_note(r['address'], r['cwd'], text, getattr(rt, 'session', None), getattr(rt, 'sock', None))


def change_practice(group, on, rt, log=None, now=None):
    """Saved as New team's default; each running agent is told with its next turn, through quiet send."""
    old = settings.load()['practices'].get(group, True)
    _apply_practice(group, on, rt)
    g = next((x for x in preset.practices()['groups'] if x['id'] == group), None)
    return live.record('practice', g['title'] if g else group, 'You changed this in Settings.', log=log, now=now,
                       group=group, old=old, new=on)


def change_fix(fix, mode, log=None, now=None):
    old = settings.load()['fixes'].get(fix, 'ask')
    settings.set_value(f'fixes.{fix}', mode)
    return live.record('fix_mode', fix, 'You changed this in Settings.', log=log, now=now, fix=fix, old=old, new=mode)
