"""Activity: the action log as rows in plain words, and Undo.

Undo exists only where it really undoes something. A message already delivered, or memory already freed, has none.
Another step adds its own by putting an entry in UNDO; the brain checks the time limit, never the app.
"""

import os

from . import live, newteam, preset, quiet, settings, status, team

UNDO_S = 24 * 3600
MAX_ROWS = 500
NO_WORDS = ('menu', 'answer')  # older rows of these held the agent's question: it stays out of what is shown
NEVER_WORDS = ('ask-changes',)  # older rows of this held a request, or a reviewer's note
TITLES = {'quiet': 'Started holding notes from {agent}',
          'compact': 'Freed up memory for {agent}', 'nudge': 'Asked {agent} to try another way',
          'tell': 'Told {agent} why a command is blocked', 'add_agent': 'Added {agent}',
          'menu': 'Answered {agent}', 'answer': 'Answered {agent}', 'deliver': 'Delivered held notes to {agent}',
          'dropped': "Couldn't deliver held notes to {agent}",
          'send': 'Sent a message to {agent}', 'stop_agent': 'Stopped {agent}'}


FIX_NAMES = {'nudge': 'Ask stuck agents to try another way', 'compact': 'Free up memory between tasks', 'tell': 'Explain blocked commands'}
EVERY_TEAM = ('fix_mode', 'practice', 'undo')  # settings apply to all teams; any other row without a team isn't this team's


def _undone(entries):
    return {e.get('of') for e in entries if e.get('action') in ('undo', 'undo-merge')}


def _entries(path=None):
    return live.recent_actions(path, MAX_ROWS)


def _title(e):
    if e.get('title'):
        return e['title']
    action = e.get('action')
    if action == 'fix_mode':
        return f'Set "{FIX_NAMES.get(e.get("fix"), e.get("fix"))}" to {"Automatic" if e.get("new") == "auto" else "Ask first"}'
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
    info = rt.get(e['address'])
    labels = info.get('labels') or {}
    if info.get('state') != 'live':
        raise ValueError('This agent has already stopped.')
    if labels.get(team.TEAM) != e.get('team') or labels.get(team.ROLE) != e.get('agent'):
        raise ValueError('That terminal is no longer this agent.')  # only remove terminals you started
    rt.kill(e['address'])
    try:
        newteam.forget_role(os.path.join(newteam.teams_dir(), e['team']), e['agent'])
    except (preset.PresetError, OSError):
        pass  # no saved folder to tidy: the agent itself has stopped


def _undo_improve(e, rt):
    from . import improve  # it reads the team through teammap, which imports this module
    improve.undo(e, rt)


# action -> (fn(entry, rt), button label, seconds it stays available or None for as long as it applies)
UNDO = {'quiet': (_undo_quiet, 'Stop', None),
        'fix_mode': (_undo_fix, 'Undo', UNDO_S),
        'practice': (_undo_practice, 'Undo', UNDO_S),
        'improve': (_undo_improve, 'Undo', UNDO_S),
        'add_agent': (_undo_add, 'Stop', UNDO_S)}


def register_merge(review):
    """Step 9's merges: the Review object holds what an Undo needs, and logs its own row."""
    def undo_merge(e, rt):
        if e.get('restore'):  # after a brain restart the record is only in the log
            review.undos.setdefault(e['id'], e['restore'])
        review.undo(e['id'])
    UNDO['merge'] = (undo_merge, 'Undo', UNDO_S)
    APPLIES['merge'] = lambda e: e['id'] in review.undos or bool(e.get('restore'))


# action -> whether it still applies right now: a pair removed in Settings has nothing left to stop.
APPLIES = {'quiet': lambda e: bool(e.get('pair')) and quiet.is_quiet(e['pair'][0], e['pair'][1])}
LOGS_ITS_OWN_UNDO = ('merge',)
CONFIRM = {'add_agent': "This ends {agent} and anything it's doing right now. Its branch and files stay."}  # never kill without asking


def _still_applies(e):
    check = APPLIES.get(e.get('action'))
    try:
        return not check or check(e)
    except (KeyError, IndexError, TypeError, AttributeError):  # an odd old row just has no button
        return False


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
    undone = _undone(entries)
    rows = []
    for e in reversed(entries):
        by = e.get('by', 'you')
        theirs = e.get('team') == team or (e.get('team') is None and e.get('action') in EVERY_TEAM)
        if (filter != 'all' and by != filter) or (team and not theirs):
            continue
        at = status.parse_ts(e.get('at'))
        local = at.astimezone() if at else None
        reason = '' if e.get('action') in NEVER_WORDS or (e.get('action') in NO_WORDS and 'id' not in e) else (e.get('reason') or '')
        if not e.get('ok', True):
            reason = f"It didn't work: {e.get('detail') or 'no reason given'}"
        action = e.get('action')
        can = _available(e, undone, now) is None
        rows.append({'id': e.get('id'), 'at': local.strftime('%H:%M') if local else '', 'day': local.strftime('%Y-%m-%d') if local else '',
                     'title': _title(e), 'reason': reason, 'by': by, 'ok': e.get('ok', True), 'agent': e.get('agent'), 'action': e.get('action'),
                     'undo': ({'label': UNDO[action][1]} | ({'confirm': CONFIRM[action].format(agent=e.get('agent'))} if action in CONFIRM else {})) if can else None})
    return {'rows': rows}


def undo(id, rt, path=None, now=None):
    now = now or status.now_utc()
    entries = _entries(path)
    e = next((x for x in entries if x.get('id') == id), None)
    if not e:
        return {'ok': False, 'detail': "This can't be undone."}
    why = _available(e, _undone(entries), now)
    if why:
        return {'ok': False, 'detail': why}
    try:
        UNDO[e['action']][0](e, rt)
    except Exception as err:  # a failed Undo is a message on the row, and stays available to try again
        return {'ok': False, 'detail': str(err)[:120]}
    if e['action'] not in LOGS_ITS_OWN_UNDO:
        live.record('undo', e.get('agent'), 'You undid this.', team=e.get('team'), log=path, now=now, of=id, undid=_title(e))
    return {'ok': True, 'detail': ''}


def practice_item(key):
    """(title, rules it covers, whether running agents are told) for a group, rule or bigger-team key."""
    p = preset.practices()
    for g in p['groups']:
        if g['id'] == key:
            return g['title'], g['rules'], True
        for r in g['rules']:
            if r['id'] == key:
                return r['title'], [r], True
    b = next((x for x in p['bigger'] if x['id'] == key), None)
    return (b['title'], [], False) if b else (key, [], False)


def _apply_practice(key, on, rt):
    before = settings.load()['practices']
    after = settings.set_value(f'practices.{key}', on)['practices']
    title, rules, tell = practice_item(key)
    # Agents hear only about rules whose real state changes: what new teams get (`preset.rule_on`), so a rule turned on
    # inside a group that is off is saved for later but told to no one. A bigger-team preset shapes the next team only.
    rules = [r for r in rules if preset.rule_on(after if on else before, r['id']) != preset.rule_on(before if on else after, r['id'])]
    if not tell or not rules:
        return
    text = f"assieme: the team practice \"{title}\" is now {'on' if on else 'off'}."
    text += (' From your next task, follow these:\n' + '\n'.join(f"- {r['text']}" for r in rules)) if on else ' You no longer need to follow it.'
    try:
        rows = rt.list({}, all=False)
    except Exception:  # no core running means no agents to tell
        return
    for r in rows:
        if r.get('state') == 'live' and team.TEAM in (r.get('labels') or {}) and r.get('cwd'):
            quiet.hold_note(r['address'], r['cwd'], text, getattr(rt, 'session', None), getattr(rt, 'sock', None))


def change_practice(key, on, rt, log=None, now=None):
    """Saved as New team's default; each running agent is told with its next turn, through quiet send."""
    old = settings.load()['practices'].get(key, key not in {b['id'] for b in preset.practices()['bigger']})
    _apply_practice(key, on, rt)
    return live.record('practice', practice_item(key)[0], 'You changed this in Settings.', log=log, now=now,
                       group=key, old=old, new=on)


def change_fix(fix, mode, log=None, now=None):
    old = settings.load()['fixes'].get(fix, 'ask')
    settings.set_value(f'fixes.{fix}', mode)
    return live.record('fix_mode', fix, 'You changed this in Settings.', log=log, now=now, fix=fix, old=old, new=mode)
