"""Step 32: the brain methods for one agent's skills, plugins and MCP servers."""

import dataclasses
import os
import threading

from . import live, newteam, preset, runtime, team, tools


def find(kind, project=None, own_copy=True):
    return tools.find(kind, project, own_copy=own_copy)


def suggest(role_kind, kind, project=None, own_copy=True):
    return tools.suggest(role_kind, tools.find(kind, project, own_copy=own_copy)['items'])


def _role(teams, team_name, role_name, practices=False):
    """practices: the team as it runs (practice roles and rules in); without, as its file saves it."""
    folder = os.path.join(teams, team_name)
    tm = preset.load(folder, practices=practices)
    r = next((x for x in tm.roles if x.name == role_name), None)
    if not r:
        raise ValueError(f'No agent named {role_name} in {team_name}.')
    return folder, tm, r


def for_agent(teams, team_name, role_name):
    """What Agent details' picker needs: this agent's kind's items, its saved choice and its role's suggestion."""
    _, tm, r = _role(teams, team_name, role_name, practices=True)
    own = r.checkout == 'branch' and not r.worktree
    found = tools.find(r.agent, tm.project, own_copy=own)
    return {**found, 'value': {'tools_off': r.tools_off, 'tools_new': r.tools_new},
            'suggested': tools.suggest(r.kind, found['items']), 'role_kind': r.kind, 'agent': r.agent}


def set_tools(teams, team_name, role_name, tools_off, tools_new):
    """Save one agent's choice. It applies the next time the agent starts. Logged with an Undo."""
    folder, tm, r = _role(teams, team_name, role_name)
    change = {'tools_off': tools_off, 'tools_new': tools_new}
    newteam.validate([newteam._role_dict(x) | (change if x.name == role_name else {}) for x in tm.roles])  # parents too
    # What was there before stays known, so an item uninstalled meanwhile comes back as it was, not as new.
    known = [] if tools_new else sorted(set(r.tools_known) | {i['id'] for i in tools.find(r.agent, tm.project)['items']})
    old = {'tools_off': r.tools_off, 'tools_new': r.tools_new, 'tools_known': r.tools_known}
    newteam._rewrite_role(folder, role_name, tools_off=list(tools_off), tools_new=bool(tools_new), tools_known=known)
    return live.record('tools', role_name, 'You changed its skills and servers. They apply when it next starts.',
                       team=team_name, title=f'Changed the skills and servers of {role_name}', old=old, folder=folder)


def undo(e):
    newteam._rewrite_role(e.get('folder') or os.path.join(newteam.teams_dir(), e['team']), e['agent'], **e['old'])


def restart(teams, rt, team_name, role_name, thread=threading.Thread):
    """Stop the agent and start it again with its saved choice, picking up its conversation where the kind allows it
    (Claude Code and Codex). The app asks the person first: anything it is doing right now stops."""
    folder, tm, r = _role(teams, team_name, role_name, practices=True)  # its brief keeps the team practices
    try:
        rows = rt.list({team.TEAM: team_name}, all=False)
    except runtime.RuntimeError_:
        rows = []  # no core running: nothing of this team is
    for row in rows:
        labels = row.get('labels') or {}
        if row.get('state') == 'live' and labels.get(team.TEAM) == team_name and labels.get(team.ROLE) == role_name:
            rt.kill(row['address'])
    tm = dataclasses.replace(tm, roles=[dataclasses.replace(x, cont=True) if x.name == role_name else x for x in tm.roles])

    def run():
        try:
            team.start(tm, None, rt, ask=lambda q: True, only=role_name)
        except Exception as err:  # a failed start is a row in Activity, not a crash of the brain
            live.record('restart', role_name, f"It didn't start again: {err}", ok=False, team=team_name)
    live.record('restart', role_name, 'You restarted it with its skills and servers.', team=team_name,
                title=f'Restarted {role_name}')
    thread(target=run, daemon=True).start()
    return {'ok': True, 'detail': ''}
