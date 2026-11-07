"""Prompts an agent shows at start-up, recognised from its screen text.

Claude Code's folder-trust prompt defaults to "No, exit", so it is never answered with a bare Enter.
Codex's hook review is never answered by Assieme: the user reviews hooks themselves.
"""


def trust_keys(screen):
    """The keys that answer 'yes' to a folder-trust prompt on screen, or None when there is none."""
    if 'Yes, I trust this folder' in screen:
        return ['down', 'enter']
    if 'Trust and continue' in screen:
        return ['enter']
    return None


def hook_review(screen):
    return 'Hooks need review' in screen
