"""Step 18a: the brain's actions, described for assistants (Claude Code, Codex, the Claude app), so a session can do
what the app does. `app_actions` lists them and `app_action` runs one; the MCP server asks first when one changes
something. A test fails when the brain serves an action that is neither described here nor hidden, so a new app
feature can't silently miss the assistants."""
import inspect
from dataclasses import dataclass, field

# Set in the server entry of the plugin, the Claude app extension and the Codex plugin: the client asks before each
# change tool, so its own prompt is the person's yes (mcpserver._with_yes). Here so the brain needn't load the SDK.
CLIENT_ASKS = 'ASSIEME_CLIENT_ASKS'


@dataclass(frozen=True)
class Action:
    description: str
    params: dict = field(default_factory=dict)  # name -> what to pass ("optional" when it has a default)
    changes: bool = False


T, R = 'team name', 'agent name in the team'

CATALOGUE = {
    # Teams
    'teams.list': Action('Lists the teams running now and the saved ones that have stopped.'),
    'team.view': Action('Shows a team: its agents, their states and what needs the person.', {'team': T}),
    'team.map': Action('Shows who wakes whom in a team, with the wake-ups between agents.', {'team': T}),
    'team.presets': Action('Lists the team presets New team offers.'),
    'project.check': Action('Checks a project folder before starting a team in it.', {'path': 'full path of the folder'}),
    'team.suggest': Action('Suggests a team for a project, with sizes, a reviewer and a budget.',
                           {'project': 'full path', 'size': 'small, medium or large', 'review': 'true or false',
                            'budget': 'daily limit in dollars, or null',
                            'task': 'optional: what the team works on; then size may be empty'}),
    'team.start': Action('Starts a team: its agents, each in its own terminal and branch.',
                         {'project': 'full path', 'task': 'what the team works on', 'roles': 'list of roles as in New team',
                          'practices': 'team practices, {} for the defaults', 'name': 'optional team name'}, True),
    'team.stop': Action('Stops every agent of a team. Their branches and files stay.', {'team': T}, True),
    'team.add_agent': Action('Adds an agent to a running team.', {'team': T, 'role': 'the role, as in New team'}, True),
    'team.allow_script': Action('Allows one setup or test script a team brought over may run.',
                                {'team': T, 'which': 'setup or test', 'line': 'the exact command line'}, True),
    'team.run_found': Action('Lets Assieme apply automatic fixes on a team found in herdr, or takes that back.',
                             {'team': T, 'on': 'true or false'}, True),
    'team.band': Action('Shows the instrument each agent plays.', {'team': T}),
    'team.improve': Action('Checks a running team and lists improvements to its setup.', {'team': T}),
    'team.improve_apply': Action('Applies chosen improvements to a team.', {'team': T, 'ids': 'list of improvement ids'}, True),
    'team.improve_brief': Action('Drafts a better brief for one role.', {'project': 'full path', 'role': 'the role'}),
    'team.rule_draft': Action('Turns plain words into a permission rule for a team.', {'project': 'full path', 'words': 'the rule in plain words'}),
    'groups.set': Action('Saves how a large team groups its agents on the map.', {'groups': 'groups by team'}, True),
    # Agents
    'agent.detail': Action('Shows one agent: what is wrong, how full its memory is and who wakes it.', {'team': T, 'role': R}),
    'agent.restart': Action('Restarts an agent with its saved choice. Whatever it is doing stops.', {'team': T, 'role': R}, True),
    'agent.tools': Action('Lists the skills and MCP servers an agent has on and off.', {'team': T, 'role': R}),
    'agent.set_tools': Action('Turns an agent’s skills and MCP servers on or off.',
                              {'team': T, 'role': R, 'tools_off': 'list of ids turned off', 'tools_new': 'true to add newly installed ones'}, True),
    'agent.set_musician': Action('Changes the instrument an agent plays. Only its look changes.',
                                 {'team': T, 'role': R, 'musician': 'one of the nine instruments'}, True),
    'tools.find': Action('Lists the skills and MCP servers installed for a kind of agent.',
                         {'kind': 'agent kind', 'project': 'optional full path', 'own_copy': 'optional, true or false'}),
    'tools.suggest': Action('Suggests which skills and MCP servers a role needs.',
                            {'role_kind': 'planner, builder, reviewer or tester', 'kind': 'agent kind',
                             'project': 'optional full path', 'own_copy': 'optional, true or false'}),
    # Review and merge
    'review.waiting': Action('Lists the agents whose work is ready to review.'),
    'review.get': Action('Shows an agent’s changes and its checks: tests, conflicts with main, files touched.',
                         {'team': T, 'role': R, 'retest': 'optional, true to run the tests again'}),
    'review.merge': Action('Merges an agent’s work into main.',
                           {'team': T, 'role': R, 'tip': 'the commit shown by review.get', 'force': 'optional, true to merge despite failing checks'}, True),
    'review.ask': Action('Sends an agent a request for changes to its work.', {'team': T, 'role': R, 'text': 'what to change'}, True),
    'review.discard': Action('Throws away an agent’s work on its branch.', {'team': T, 'role': R, 'tip': 'the commit shown by review.get'}, True),
    'review.note': Action('Acts on a reviewer’s note: asks the builder to fix it, or ignores it.',
                          {'team': T, 'role': R, 'note': 'note id', 'action': 'fix or ignore'}, True),
    'review.pull_request': Action('Opens a pull request for an agent’s work.', {'team': T, 'role': R}, True),
    'review.undo': Action('Undoes a merge made in the last day.', {'id': 'the merge id'}, True),
    # Task board
    'board.get': Action('Shows a team’s task board: to do, doing, review and done.', {'team': T}),
    'board.add': Action('Adds a card to a team’s board.', {'team': T, 'title': 'the task', 'role': 'optional agent to give it to'}, True),
    'board.move': Action('Moves a card to another column.', {'team': T, 'id': 'card id', 'status': 'todo, doing, review or done'}, True),
    'board.remove': Action('Removes a card from a team’s board.', {'team': T, 'id': 'card id'}, True),
    # Brief
    'brief.get': Action('Shows a team’s shared brief: goal, decisions and rules.', {'team': T}),
    'brief.save': Action('Changes a team’s shared brief.', {'team': T, 'goal': 'optional new goal', 'rules': 'optional list of rules'}, True),
    'brief.remove': Action('Removes one decision from a team’s shared brief.', {'team': T, 'id': 'decision id'}, True),
    # Budgets, fixes, notes between agents
    'budget.set': Action('Sets a team’s daily spend limits.',
                         {'team': T, 'limits': 'limits per team and agent', 'at_limit': 'optional, pause or warn'}, True),
    'tip.apply': Action('Applies a suggested fix to a team.', {'team': T, 'tip': 'tip id'}, True),
    'quiet.pairs': Action('Lists the pairs of agents whose routine notes wait until the receiver is free.', {'team': 'optional team name'}),
    'quiet.set': Action('Holds or stops holding routine notes between two agents.',
                        {'from_cwd': 'sender folder', 'to_cwd': 'receiver folder', 'label': 'optional', 'on': 'optional, true or false'}, True),
    # Activity and settings
    'activity.list': Action('Lists what Assieme did, with its reason, newest first.', {'team': 'optional team name'}),
    'activity.undo': Action('Undoes an action from Activity, where it can be undone.', {'id': 'activity id'}, True),
    'settings.get': Action('Shows Assieme’s settings.'),
    'settings.set': Action('Changes one Assieme setting.', {'key': 'setting name, for example notify.needs_you', 'value': 'its new value'}, True),
    'notifications.pause': Action('Pauses Assieme’s Mac notifications for one hour.', {}, True),
    'notifications.resume': Action('Turns Assieme’s Mac notifications back on.', {}, True),
    # Scheduled runs
    'schedule.list': Action('Lists scheduled runs.', {'teams': 'optional, true to include their teams'}),
    'schedule.save': Action('Creates or changes a scheduled run.',
                            {'name': 'run name', 'project': 'full path', 'preset': 'preset name', 'task': 'what to do',
                             'every': 'when it runs', 'id': 'optional, to change an existing run'}, True),
    'schedule.pause': Action('Pauses or resumes a scheduled run.', {'id': 'run id', 'paused': 'optional, true or false'}, True),
    'schedule.run_now': Action('Starts a scheduled run now.', {'id': 'run id'}, True),
    'schedule.stop': Action('Stops a scheduled run that is going.', {'id': 'run id'}, True),
    'schedule.remove': Action('Deletes a scheduled run.', {'id': 'run id'}, True),
    # Live preview
    'preview.list': Action('Lists the live previews running.'),
    'preview.start': Action('Starts an agent’s dev server, to see its changes.',
                            {'team': T, 'role': R, 'command': 'optional command', 'install': 'optional, true to install first',
                             'confirm': 'optional'}, True),
    'preview.stop': Action('Stops an agent’s live preview.', {'team': T, 'role': R}, True),
    # Bring a team over
    'bringover.find': Action('Finds teams from other agent tools in a folder.', {'folder': 'optional full path'}),
    'bringover.workspaces': Action('Lists the workspaces of a team found in another tool.', {'id': 'id from bringover.find'}),
    'bringover.import': Action('Turns a team from another tool into a New team draft.', {'id': 'id from bringover.find', 'ticked': 'optional list'}, True),
    'herdr.find': Action('Finds herdr teams and launch scripts on this Mac.'),
    'herdr.import': Action('Turns a herdr team into a New team draft.', {'source': 'what herdr.find returned'}, True),
    # Remote machines
    'remote.machines': Action('Lists the machines Assieme is set up on.'),
    'remote.hosts': Action('Lists the SSH hosts this Mac knows.'),
    'remote.check': Action('Checks that a machine is reachable and ready.', {'machine': 'machine name'}),
    'remote.test': Action('Tests an SSH connection and adds the machine to Assieme.', {'target': 'SSH host or user@address'}, True),
    'remote.connect': Action('Connects to a machine Assieme is set up on.', {'machine': 'machine name'}, True),
    'remote.setup': Action('Sets Assieme up on a machine over SSH.', {'target': 'SSH host or user@address'}, True),
    'remote.forget': Action('Removes a machine from Assieme. Its files stay.', {'machine': 'machine name'}, True),
    'remote.repos': Action('Lists the git projects in the home folder.'),
    # Agents and containers on this Mac
    'setup.status': Action('Shows which agents are installed and signed in on this Mac.'),
    'setup.commands': Action('Lists the install and sign-in commands for each kind of agent.'),
    'setup.folder_preview': Action('Shows what turning a folder into a project would create.', {'project': 'full path'}),
    'setup.folder_save': Action('Turns a folder into a git project for a team.', {'project': 'full path'}, True),
    'setup.folder_create': Action('Creates a new project folder.', {'path': 'full path'}, True),
    'container.status': Action('Shows whether an agent kind’s container is built.', {'agent': 'agent kind'}),
    'container.build': Action('Builds the container an agent kind runs in.', {'agent': 'agent kind'}, True),
}

# Internal, app-only, or opening windows and terminals on the Mac (no use from another device).
HIDDEN = frozenset({'brain.snapshot', 'menu.poll', 'menu.state', 'draft.take', 'pane.act', 'remote.terminal',
                    'remote.job', 'remote.version', 'review.open_editor', 'container.open', 'setup.terminal_state',
                    'setup.install_agent', 'setup.install_stop', 'setup.install_tools', 'setup.signin_agent',
                    'setup.connect_antigravity', 'assistant.actions', 'assistant.do'})
HIDDEN_PREFIXES = ('mcp.', 'assistants.')  # the assistants' own tools; installing assistants from an assistant


def hidden(names):
    """The names among `names` an assistant never sees."""
    return {n for n in names if n in HIDDEN or n.startswith(HIDDEN_PREFIXES)}


def listing(filter=''):
    """The actions an assistant may use, optionally only those whose name or description holds `filter`."""
    f = (filter or '').lower()
    return [{'name': n, 'description': a.description, 'params': dict(a.params), 'changes': a.changes}
            for n, a in sorted(CATALOGUE.items()) if f in n or f in a.description.lower()]


def run(table, name, params):
    """Carry out one catalogued action through the brain's own handler. Any failure comes back as its plain message."""
    if name not in CATALOGUE or name not in table:
        raise ValueError(f'No action named {name}. app_actions lists them.')
    fn = table[name]
    params = params or {}
    try:
        inspect.signature(fn).bind(**params)
    except TypeError:
        raise ValueError(f"{name} takes: {', '.join(CATALOGUE[name].params) or 'nothing'}.") from None
    except ValueError:
        pass  # no signature to check (a builtin): let the call say
    try:
        return fn(**params)
    except Exception as e:  # the handler's own message, never a traceback
        raise ValueError(getattr(e, 'message', None) or str(e)) from None


def handlers(table):
    return {'assistant.actions': lambda filter='': listing(filter),
            'assistant.do': lambda name, params=None: run(table, name, params or {})}
