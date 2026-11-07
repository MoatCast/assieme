"""Prompts and questions an agent shows, recognised from its screen text.

Claude Code's folder-trust prompt defaults to "No, exit", so it is never answered with a bare Enter.
Codex's hook review is never answered by Assieme: the user reviews hooks themselves.
"""

import re

ITEM = re.compile(r'^\s*(?:[❯›>]\s*)?(\d+)\.\s+(\S.*?)\s*$')
META = {'Type something.', 'Chat about this'}  # Claude's own extras: Reply covers them
LABEL_MAX = 40


def trust_keys(screen):
    """The keys that answer 'yes' to a folder-trust prompt on screen, or None when there is none."""
    if 'Yes, I trust this folder' in screen:
        return ['down', 'enter']
    if 'Trust and continue' in screen:
        return ['enter']
    return None


def hook_review(screen):
    return 'Hooks need review' in screen


def menu(screen):
    """The numbered menu at the bottom of the screen as [(n, label)], or [] when there is none.

    Only the last block counts (a numbered list in the agent's own text above it is not a menu); items may have
    description lines between them; 2 to 9 items numbered from 1."""
    found = []
    for line in reversed(screen.splitlines()[-40:]):
        m = ITEM.match(line)
        if not m:
            continue
        n = int(m.group(1))
        if found and n != found[-1][0] - 1:
            break
        found.append((n, m.group(2)))
        if n == 1:
            break
    if not found or found[-1][0] != 1:
        return []
    items = [(n, label if len(label) <= LABEL_MAX else label[:LABEL_MAX - 1] + '…')
             for n, label in reversed(found) if label not in META]
    return items if 2 <= len(found) <= 9 and len(items) >= 2 else []


AGENT_TEXT = re.compile(r'^[⏺•]\s?(.*)$')  # where an agent message starts: Claude Code ⏺, Codex •
BOX = re.compile(r'^\s*([❯›]|─{3,})')


def question(screen):
    """The agent's last message, when its last paragraph is a question, as one line; else None."""
    lines = screen.splitlines()[-40:]
    start = max((i for i, l in enumerate(lines) if AGENT_TEXT.match(l)), default=None)
    if start is None:
        return None
    body = [AGENT_TEXT.match(lines[start]).group(1)]
    for l in lines[start + 1:]:
        if BOX.match(l):
            break
        body.append(l.strip())
    paragraphs = [' '.join(p.split()) for p in '\n'.join(body).split('\n\n') if p.strip()]
    if not paragraphs or not paragraphs[-1].endswith('?'):
        return None
    return paragraphs[-1][:300]
