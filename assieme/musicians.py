"""Step 31: the instrument each agent plays. Chosen by role when the team is made, then saved with the role, so it never
changes when other agents come or go. The pictures are drawn by the app (art/musicians.py)."""

INSTRUMENTS = ['piano', 'reviewer', 'violin', 'cello', 'bass', 'timpani', 'flute', 'clarinet', 'trumpet']
BY_KIND = {'planner': 'piano', 'reviewer': 'reviewer', 'tester': 'timpani'}
STRINGS = ['violin', 'cello', 'bass']  # builders, in turn
WINDS = ['flute', 'clarinet', 'trumpet']  # a role of the person's own, in turn


def _get(role, key):
    return role.get(key) if isinstance(role, dict) else getattr(role, key, None)


def assign(roles):
    """Each role's instrument: its saved one when it has a known one, else the rule by its place in the team."""
    out, builders, custom = [], 0, 0
    for role in roles:
        kind = _get(role, 'kind')
        if kind == 'builder':
            rule, builders = STRINGS[builders % 3], builders + 1
        elif kind in BY_KIND:
            rule = BY_KIND[kind]
        else:
            rule, custom = WINDS[custom % 3], custom + 1
        saved = _get(role, 'musician')
        out.append(saved if saved in INSTRUMENTS else rule)
    return out


def palette(roles, i):
    """0, or 1 for the fourth builder on: the strings come round again in the second colours."""
    if _get(roles[i], 'kind') != 'builder':
        return 0
    return 1 if sum(1 for r in roles[:i] if _get(r, 'kind') == 'builder') >= 3 else 0
