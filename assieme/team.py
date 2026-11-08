"""Start, list and stop a team of agents from a preset, through the runtime (an isolated hc core).

Starting one agent: its worktree, the agent with our status hook, the folder-trust prompt (asked once per
project, answered with explicit keys), then its first status report, and only then its brief and task.
"""
import time
from dataclasses import dataclass

from . import launch, prompts, worktree

TEAM, ROLE, BRIEFED = 'assieme.team', 'assieme.role', 'assieme.briefed'
FIRST_STATUS_S = 60   # after start, and again after answering a trust prompt
TAIL_LINES = 8


class TeamError(RuntimeError):
    pass


@dataclass
class Started:
    role: str
    address: str | None
    state: str  # running, needs-you, not-reporting, exited, already-running
    note: str = ''


def _tail(rt, t):
    try:
        return '\n'.join(rt.read(t).rstrip().splitlines()[-TAIL_LINES:])
    except Exception:
        return ''


def status(rt, name=None):
    rows = []
    for x in rt.list({TEAM: name} if name else {}, all=True):
        labels = x.get('labels') or {}
        if TEAM not in labels or (name and labels[TEAM] != name):
            continue
        rows.append({'team': labels[TEAM], 'role': labels.get(ROLE), 'address': x['address'],
                     'state': x.get('state'), 'status': (x.get('status') or {}).get('state'),
                     'briefed': labels.get(BRIEFED) == '1', 'cwd': x.get('cwd')})
    return rows


def stop(rt, name):
    live = [r for r in status(rt, name) if r['state'] == 'live']
    for r in live:
        rt.kill(r['address'])
    return len(live)


def _brief(rt, t, role, task):
    rt.send(t, role.brief.strip() + (f'\n\n{task}' if task else ''))
    rt.label(t, {BRIEFED: '1'})


def _review_hint(rt, role, t):
    return (f'{role.agent} wants you to review its hooks once: '
            f'HC_SESSION={getattr(rt, "session", "assieme")} HC_SOCKET={rt.sock} hc attach {t}')


def _start_role(tm, role, task, rt, ask, trusted, parent, cwd, clock):
    t = rt.start(launch.argv(role), cwd, {TEAM: tm.name, ROLE: role.name}, parent=parent)
    deadline = clock.monotonic() + FIRST_STATUS_S
    while True:
        info = rt.get(t)
        if info.get('state') == 'finished':
            return Started(role.name, t, 'exited', _tail(rt, t))
        if info.get('status'):
            _brief(rt, t, role, task)
            return Started(role.name, t, 'running')
        screen = rt.read(t)
        keys = prompts.trust_keys(screen)
        if keys:
            if tm.project not in trusted:
                if not ask(f'Trust {tm.project} so {role.agent} agents can read, edit and run files there?'):
                    rt.kill(t)
                    return Started(role.name, t, 'exited', 'folder not trusted')
                trusted.add(tm.project)
            rt.keys(t, keys)
            deadline = clock.monotonic() + FIRST_STATUS_S
        elif prompts.hook_review(screen):
            return Started(role.name, t, 'needs-you', _review_hint(rt, role, t))
        if clock.monotonic() >= deadline:
            return Started(role.name, t, 'not-reporting', _tail(rt, t))
        clock.sleep(1)


def start(tm, task, rt, ask, only=None, clock=time):
    roles = tm.roles
    if only:
        roles = [r for r in tm.roles if r.name == only]
        if not roles:
            raise TeamError(f'no role named {only!r} in team {tm.name!r}')
    missing = launch.missing_clis(roles)
    if missing:
        raise TeamError(f'not installed: {", ".join(missing)}. Install it, then start the team again.')
    worktree.check_repo(tm.project)
    live = {r['role']: r for r in status(rt, tm.name) if r['state'] == 'live'}
    # Every worktree before the first agent, so a branch or worktree problem refuses before anything runs.
    cwds = {r.name: worktree.ensure(tm, r) for r in roles if r.name not in live}
    out, trusted, addresses = [], set(), {name: r['address'] for name, r in live.items()}
    for role in roles:
        row = live.get(role.name)
        if row is None:
            s = _start_role(tm, role, task, rt, ask, trusted, addresses.get(role.parent), cwds[role.name], clock)
        elif row['briefed']:
            s = Started(role.name, row['address'], 'already-running')
        elif row['status']:  # live but never briefed, e.g. after the user answered Codex's hook review
            _brief(rt, row['address'], role, task)
            s = Started(role.name, row['address'], 'running')
        else:
            s = Started(role.name, row['address'], 'needs-you', _review_hint(rt, role, row['address']))
        addresses[role.name] = s.address
        out.append(s)
    return out
