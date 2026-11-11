"""Daily token limits for a team and for single agents. At a limit: pause and ask (the default), or only tell.

A pause never ends an agent. It presses the agent's own stop key if it is working, and messages sent to it through
Assieme wait until the person lets it carry on. Its terminal, branch and memory stay as they were.
Limits and pauses are kept in budgets.json in the state folder, so a restarted brain still knows who is paused.

Tokens are the input tokens each agent's own log records: an exact count, read every couple of minutes, not a bill.
"""
import threading

from . import live, quiet, status

TEAM = '*'  # the scope that means the whole team: no role can be named this
MODES = ('pause', 'tell')
RESUME = ('assieme: you were paused because a daily spend limit was reached. The person has let you carry on. '
          'Continue from where you stopped.')
GONE = 'This has already carried on.'
_lock = threading.Lock()  # the brain serves each call on its own thread


def _path():
    return quiet._path('budgets.json')


def _dict(v):
    return v if isinstance(v, dict) else {}


def _all():
    return _dict(quiet._read_json(_path(), {}))


def _clean(raw):
    """One team's record. A damaged file means no limits and nobody paused, never a pause nobody asked for."""
    raw = _dict(raw)
    return {'limits': {k: v for k, v in _dict(raw.get('limits')).items() if isinstance(v, int) and v > 0},
            'at_limit': raw.get('at_limit') if raw.get('at_limit') in MODES else 'pause',
            'extra': _dict(raw.get('extra')), 'paused': _dict(raw.get('paused')), 'told': _dict(raw.get('told'))}


def get(team):
    return _clean(_all().get(team))


def teams():
    """Teams the tick has to look at: one with a limit, or with someone paused."""
    return {name for name, raw in _all().items() if _clean(raw)['limits'] or _clean(raw)['paused']}


def _save(team, b):
    everything = _all()
    everything[team] = b
    quiet._write_json(_path(), everything)


def words(n):
    """4200000 -> 4.2M, as the app writes it."""
    if n >= 1e6:
        return f'{n / 1e6:.1f}'.removesuffix('.0') + 'M'
    return f'{round(n / 1e3)}K' if n >= 1e3 else str(n)


def _day(now):
    return now.astimezone().strftime('%Y-%m-%d')


def _allowed(b, scope, today):
    """The limit plus what the person added for today, or None when there is no limit."""
    limit = b['limits'].get(scope)
    if not limit:
        return None
    extra = _dict(b['extra'].get('by')).get(scope, 0) if b['extra'].get('day') == today else 0
    return limit + extra


def _used(scope, used, total):
    return total if scope == TEAM else used.get(scope, 0)


def _who(team, scope):
    return team if scope == TEAM else scope


def view(team, used, total, now):
    """What the Team tab shows: limits, today's use, and one question for each pause. `used` is {role: tokens}."""
    b, today = get(team), _day(now)
    asks = []
    for scope, entry in b['paused'].items():
        who, u, allowed = _who(team, scope), _used(scope, used, total), _allowed(b, scope, today)
        over = allowed is not None and u >= allowed
        quarter = max(1, b['limits'].get(scope, 0) // 4)
        its = 'its' if scope != TEAM else 'the team’s'
        at = status.parse_ts(entry.get('at'))
        asks.append({'scope': scope, 'who': who, 'roles': sorted(r for r, x in _dict(entry.get('roles')).items() if x.get('stopped')),
                     'title': f'{who} is paused at today’s limit',
                     'text': 'Carry on past today’s limit?' if over else 'Carry on?',
                     'detail': (f'{who} used {words(u)} of {its} {words(allowed)} tokens. ' if over else
                                f'{who} was paused at {its} limit, which no longer applies. ')
                               + 'Messages to it wait until you decide.',
                     'label': f'Add {words(quarter)} and carry on' if over else 'Carry on',
                     'add': u - allowed + quarter if over else 0,
                     'since_s': max(0, int((now - at).total_seconds())) if at else None})
    told = [{'scope': s, 'day': d, 'title': f'{_who(team, s)} reached today’s limit',
             'body': f'It used {words(_used(s, used, total))} of {words(_allowed(b, s, today) or 0)} tokens. It was not paused.'}
            for s, d in b['told'].items() if d == today and b['limits'].get(s)]
    return {'limit': b['limits'].get(TEAM), 'allowed': _allowed(b, TEAM, today), 'used': total, 'at_limit': b['at_limit'],
            'agents': [{'role': r, 'used': n, 'limit': b['limits'].get(r)} for r, n in used.items()],
            'asks': asks, 'told': told}


def check(team, built, rt, now, announce=None, log=None):
    """The brain's tick: pause or tell at a limit, and stop paused agents that are still working. Returns what it logged."""
    bud = built.get('budget')
    if not bud or built.get('spend_ready') is False:
        return []
    used, total, today = {a['role']: a['used'] for a in bud['agents']}, bud['used'], _day(now)
    here = {p['role']: p for p in built['panes'] if p['state'] != 'exited'}
    out = []

    def say(action, who, title, reason):
        out.append(live.record(action, who, reason, team=team, by='auto', log=log, now=now, title=title))
        if announce:
            announce(title, reason)

    with _lock:
        b = get(team)
        before = repr(b)
        for scope in list(b['paused']):  # an agent that has gone is no longer paused
            roles = {r: x for r, x in _dict(b['paused'][scope].get('roles')).items() if r in here}
            if roles:
                b['paused'][scope]['roles'] = roles
            else:
                del b['paused'][scope]
        for scope in b['limits']:
            u, allowed, who = _used(scope, used, total), _allowed(b, scope, today), _who(team, scope)
            roles = list(here) if scope == TEAM else [scope] if scope in here else []
            if u < allowed or scope in b['paused'] or not roles:
                continue
            spent = f'It used {words(u)} of its {words(allowed)} tokens today.'
            if b['at_limit'] == 'tell':
                if b['told'].get(scope) != today:
                    b['told'][scope] = today
                    say('budget_reached', who, f'{who} reached today’s limit', f'{spent} You chose to be told only.')
                continue
            b['paused'][scope] = {'at': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
                                  'roles': {r: {'address': here[r]['address'], 'stopped': False, 'notes': []} for r in roles}}
            say('budget_pause', who, f'Paused {who} at today’s limit',
                f'{spent} Nothing was closed: it carries on when you say so.')
        for entry in b['paused'].values():
            for role, x in entry['roles'].items():
                p = here[role]
                if x.get('stopped') or p['state'] == 'asking':
                    continue  # a real question from its CLI is the person's to answer first
                if p.get('busy') or p['state'] == 'working':  # an agent that seems stuck is often still mid-turn
                    try:
                        rt.interrupt(p['address'])
                    except Exception:  # tried again at the next tick
                        continue
                x['stopped'] = True
        if repr(b) != before:
            _save(team, b)
    return out


def _paused_roles(b, but=None):
    return {r for s, e in b['paused'].items() if s != but for r in _dict(e.get('roles'))}


def is_paused(team, role):
    return role in _paused_roles(get(team))


def hold(team, role, sender, text):
    """Keep a message for a paused agent. False when it isn't paused, so the caller sends it as usual."""
    with _lock:
        b = get(team)
        for entry in b['paused'].values():
            x = _dict(entry.get('roles')).get(role)
            if x is not None:
                x.setdefault('notes', []).append({'from': sender, 'text': text})
                _save(team, b)
                return True
    return False


def _resume(team, b, scope, add, rt, now, by, why, log):
    """Under the lock. Tells each agent of this pause to carry on, unless another limit still holds it."""
    entry = b['paused'].pop(scope)
    today = _day(now)
    if add > 0:
        if b['extra'].get('day') != today:
            b['extra'] = {'day': today, 'by': {}}
        b['extra']['by'][scope] = b['extra']['by'].get(scope, 0) + add
    still, failed = _paused_roles(b), []
    for role, x in _dict(entry.get('roles')).items():
        notes = x.get('notes') or []
        if role in still:  # its own limit, or the team's, still holds it: its messages wait there
            other = next(e for e in b['paused'].values() if role in e['roles'])
            other['roles'][role].setdefault('notes', []).extend(notes)
            continue
        text = RESUME
        if notes:
            text += '\n\nMessages that arrived while you were paused:\n' + '\n'.join(f"- from {n['from']}: {n['text']}" for n in notes)
        try:
            rt.send(x['address'], text)
        except Exception as e:
            failed.append(f'{role}: {str(e)[:80]}')
    _save(team, b)
    who = _who(team, scope)
    live.record('budget_resume', who, why + (f' Added {words(add)} tokens for today.' if add > 0 else ''), ok=not failed,
                detail='; '.join(failed), team=team, by=by, log=log, now=now, title=f'Let {who} carry on')
    return {'ok': not failed, 'detail': '; '.join(failed)}


def resume(team, scope, add, rt, now=None, by='you', log=None):
    now = now or status.now_utc()
    with _lock:
        b = get(team)
        if scope not in b['paused']:
            return {'ok': False, 'detail': GONE}
        return _resume(team, b, scope, add, rt, now, by, 'You let it carry on.', log)


def set_limits(team, limits, at_limit, rt, used, total, now=None, log=None):
    """Save the limits (tokens a day; none or 0 removes one). A pause the new limits no longer call for carries on."""
    now = now or status.now_utc()
    if at_limit not in MODES:
        raise ValueError('Choose "Pause and ask me" or "Just tell me".')
    clean = {}
    for scope, n in _dict(limits).items():
        if n in (None, 0, ''):
            continue
        if isinstance(n, bool) or not isinstance(n, (int, float)) or n < 0:
            raise ValueError('A limit is a number of tokens above zero.')
        clean[scope] = int(n)
    with _lock:
        b = get(team)
        old = (b['limits'], b['at_limit'])
        b['limits'], b['at_limit'] = clean, at_limit
        b['told'] = {}
        _save(team, b)
        if (clean, at_limit) != old:
            parts = [f"{'Team' if s == TEAM else s}: {words(n)} tokens a day." for s, n in sorted(clean.items())] or ['No limits.']
            if clean:
                parts.append('At a limit: pause and ask.' if at_limit == 'pause' else 'At a limit: just tell me.')
            live.record('budget_set', team, ' '.join(parts), team=team, log=log, now=now, title=f'Changed {team}’s daily limits')
        today = _day(now)
        for scope in list(b['paused']):
            allowed = _allowed(b, scope, today)
            if at_limit == 'tell' or allowed is None or _used(scope, used, total) < allowed:
                _resume(team, b, scope, 0, rt, now, 'you', 'You changed the limit.', log)
    return get(team)
