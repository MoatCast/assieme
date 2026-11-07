"""Start, list and stop a team of agents from a preset, through the runtime (an isolated hc core).

Starting one agent: its worktree, the agent with our status hook, the folder-trust prompt (asked once per
project, answered with explicit keys), then its first status report, and only then its brief and task.
"""
import time
from dataclasses import dataclass

from . import launch, prompts, worktree

TEAM, ROLE = 'assieme.team', 'assieme.role'
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
                     'state': x.get('state'), 'status': (x.get('status') or {}).get('state')})
    return rows


def stop(rt, name):
    live = [r for r in status(rt, name) if r['state'] == 'live']
    for r in live:
        rt.kill(r['address'])
    return len(live)


def _start_role(tm, role, task, rt, ask, trusted, parent, clock):
    cwd = worktree.ensure(tm, role)
    t = rt.start(launch.argv(role), cwd, {TEAM: tm.name, ROLE: role.name}, parent=parent)
    deadline = clock.monotonic() + FIRST_STATUS_S
    while True:
        info = rt.get(t)
        if info.get('state') == 'finished':
            return Started(role.name, t, 'exited', _tail(rt, t))
        if info.get('status'):
            rt.send(t, role.brief.strip() + (f'\n\n{task}' if task else ''))
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
            return Started(role.name, t, 'needs-you',
                           f'{role.agent} wants you to review its hooks once: HC_SESSION=assieme hc attach {t}')
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
    live = {r['role']: r['address'] for r in status(rt, tm.name) if r['state'] == 'live'}
    running = [r for r in roles if r.name in live]
    if running:
        return [Started(r.name, live[r.name], 'already-running') for r in running]
    out, trusted, addresses = [], set(), dict(live)
    for role in roles:
        s = _start_role(tm, role, task, rt, ask, trusted, addresses.get(role.parent), clock)
        addresses[role.name] = s.address
        out.append(s)
    return out
