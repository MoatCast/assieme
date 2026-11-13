"""Prompts and questions an agent shows, recognised from its screen text.

Claude Code's folder-trust prompt defaults to "No, exit", so it is never answered with a bare Enter.
Codex's hook review is never answered by Assieme: the user reviews hooks themselves.
"""

import re

ITEM = re.compile(r'^\s*([❯›>]\s*)?(\d+)\.\s+(\S.*?)\s*$')
META = {'Type something.', 'Chat about this'}  # Claude's own extras: Reply covers them
LABEL_MAX = 40


def trust_keys(screen):
    """The keys that answer 'yes' to a folder-trust prompt on screen, or None when there is none."""
    if 'Yes, I trust this folder' in screen:
        return ['down', 'enter']
    if 'Trust and continue' in screen:
        return ['enter']
    return None


# Claude Code's and Antigravity's first-run screens that only choose a look or say what to be careful about: never a permission, and
# never the sign-in, which stays the person's. Compared without spaces, since a screen read can lose them.
FIRST_RUN = ('Choosethetextstylethatlooksbestwithyourterminal', 'Securitynotes:', 'Chooseyourcolorscheme:')


def first_run_keys(screen):
    """['enter'] (keep the default) on one of FIRST_RUN's screens, else None."""
    flat = ''.join(screen.split())
    if FIRST_RUN[0] in flat or FIRST_RUN[2] in flat or (FIRST_RUN[1] in flat and 'PressEntertocontinue' in flat):
        return ['enter']
    return None


def hook_review(screen):
    return 'Hooks need review' in screen


def _tail(screen, n=40):
    """The last n lines that matter: runs of blank lines squeezed to one (a tall screen is mostly blank)."""
    out = []
    for line in screen.splitlines():
        if line.strip() or (out and out[-1].strip()):
            out.append(line)
    return out[-n:]


def _block(screen):
    """(items, index of item 1, lines) for the last numbered block on screen, or ([], None, lines)."""
    lines = _tail(screen)
    found, pointer, first = [], False, None
    for i in range(len(lines) - 1, -1, -1):
        m = ITEM.match(lines[i])
        if not m:
            continue
        n = int(m.group(2))
        if found and n != found[-1][0] - 1:
            break
        found.append((n, m.group(3)))
        pointer = pointer or bool(m.group(1))
        if n == 1:
            first = i
            break
    # A real menu draws its selection pointer; an agent's own numbered list in its reply has none.
    if first is None or not pointer or not 2 <= len(found) <= 9:
        return [], None, lines
    return list(reversed(found)), first, lines


def menu(screen):
    """The numbered menu at the bottom of the screen as [(n, label)], or [] when there is none.

    Only the last block counts, it must show the selection pointer (❯ or ›), items may have description lines
    between them, and there are 2 to 9 items numbered from 1. Claude's "Type something." and "Chat about this"
    are left out (Reply covers them)."""
    found, _, _ = _block(screen)
    items = [(n, label if len(label) <= LABEL_MAX else label[:LABEL_MAX - 1] + '…')
             for n, label in found if label not in META]
    return items if len(items) >= 2 else []


ALWAYS = re.compile(r"^Yes, (and )?(don.t ask again|allow all|always)", re.I)


ALWAYS_LABEL = 'Always allow for this agent'


def permission_labels(items):
    """{n: label} for an agent's permission menu, in Assieme's words: Allow once, Always allow for this agent, Block.
    The person's click still presses the agent's own choice, which that agent alone keeps (Claude Code in its worktree's
    local settings), so the label says so. Anything else on the menu keeps its own label. {} for other menus."""
    if not items or not items[0][1].startswith('Yes') or not any(l.startswith('No') for _, l in items[1:]):
        return {}
    out = {items[0][0]: 'Allow once'}
    for n, label in items[1:]:
        if ALWAYS.match(label) and ALWAYS_LABEL not in out.values():
            out[n] = ALWAYS_LABEL
        elif label.startswith('No') and 'Block' not in out.values():
            out[n] = 'Block'
    return out


def menu_context(screen, lines_above=8):
    """The text just above the menu ("Do you want to create note.txt?", the tool line): what the menu is about."""
    found, first, lines = _block(screen)
    if not found:
        return ''
    above = [l.strip() for l in lines[:first] if l.strip() and not set(l.strip()) <= set('─╌-')]
    return '\n'.join(above[-lines_above:])


AGENT_TEXT = re.compile(r'^[⏺•]\s?(.*)$')  # where an agent message starts: Claude Code ⏺, Codex •
BOX = re.compile(r'^\s*([❯›]|─{3,})')


def question(screen):
    """The agent's last message, when its last paragraph is a question, as one line; else None."""
    lines = _tail(screen)
    start = max((i for i, l in enumerate(lines) if AGENT_TEXT.match(l)), default=None)
    if start is None:
        return None
    body = [AGENT_TEXT.match(lines[start]).group(1)]
    for l in lines[start + 1:]:
        # The message's own lines are indented; the CLI's status lines ("✻ Brewed for 3s") and the input box aren't.
        if BOX.match(l) or (l.strip() and not l[:1].isspace()):
            break
        body.append(l.strip())
    paragraphs = [' '.join(p.split()) for p in '\n'.join(body).split('\n\n') if p.strip()]
    if not paragraphs or not paragraphs[-1].endswith('?'):
        return None
    return paragraphs[-1][:300]
