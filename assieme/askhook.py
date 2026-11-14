"""Step 18a: Claude Code's PreToolUse hook in the Assieme plugin. It answers "ask" for every Assieme tool that changes
something, so Claude Code shows its own Allow prompt, which reaches the phone (Assieme's own question doesn't). The
server started by the plugin takes the call as the person's yes (mcpserver.CLIENT_ASKS). Reads pass untouched."""
import json
import sys

from . import actions

CHANGE_TOOLS = ('answer', 'send_message', 'nudge', 'compact', 'undo', 'start_team', 'add_agent', 'stop_agent',
                'stop_team', 'start_draft')
ASK = {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'ask',
                              'permissionDecisionReason': 'Assieme asks before changing anything.'}}


def _asks(tool, tool_input):
    name = tool.rsplit('__', 1)[-1]
    if name == 'app_action':
        a = actions.CATALOGUE.get((tool_input or {}).get('name'))
        return a is None or a.changes  # an unknown action: the safe side
    return name in CHANGE_TOOLS


def main(stdin=sys.stdin, stdout=sys.stdout):
    try:
        event = json.load(stdin)
        asks = _asks(event.get('tool_name') or '', event.get('tool_input'))
    except (ValueError, AttributeError):
        asks = True  # can't tell what it is: ask
    if asks:
        stdout.write(json.dumps(ASK) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
