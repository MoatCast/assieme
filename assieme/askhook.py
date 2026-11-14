"""Step 18a: Claude Code's PreToolUse hook in the Assieme plugin. It answers "ask" for every Assieme tool that changes
something, so Claude Code shows its own Allow prompt, which reaches the phone (Assieme's own question doesn't). The
server started by the plugin takes the call as the person's yes (mcpserver.CLIENT_ASKS). Reads pass untouched.
In a mode where Claude Code may approve without the person, a change is refused instead."""
import json
import sys


CHANGE_TOOLS = ('answer', 'send_message', 'nudge', 'compact', 'undo', 'start_team', 'add_agent', 'stop_agent',
                'stop_team', 'start_draft')
# shortcut: unverified whether these modes show an "ask" to the person; refuse, until checked on a real session.
NO_PERSON = ('bypassPermissions', 'dontAsk', 'auto')
DENY = {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                               'permissionDecisionReason': 'Assieme changes need your own yes, and this session’s '
                               'permission mode can approve without asking you. Switch to the default mode to make it.'}}
ASK = {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'ask',
                              'permissionDecisionReason': 'Assieme asks before changing anything.'}}


def _asks(tool, tool_input):
    name = tool.rsplit('__', 1)[-1]
    return name in CHANGE_TOOLS or name == 'app_action'  # app_action runs changes only


def main(stdin=sys.stdin, stdout=sys.stdout):
    try:
        event = json.load(stdin)
        asks = _asks(event.get('tool_name') or '', event.get('tool_input'))
        mode = event.get('permission_mode')
    except (ValueError, AttributeError):
        asks, mode = True, None  # can't tell what it is: ask
    if asks:
        stdout.write(json.dumps(DENY if mode in NO_PERSON else ASK) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
