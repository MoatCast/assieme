"""Folder-trust and hook-review prompts, with screen text saved from the step 7 spike."""
import unittest

from assieme import prompts

CLAUDE_TRUST = """ Accessing workspace:
 /tmp/spike/wt-builder
 Quick safety check: Is this a project you created or one you trust? (Like your own code, a well-known open source project, or work from your team). If not, take a moment to review what's in this
 folder first.
 Claude Code'll be able to read, edit, and execute files here.
 Security guide
 ❯ No, exit
   Yes, I trust this folder
 Enter to confirm · Esc to cancel"""

CODEX_TRUST = """  Folder access
  /tmp/spike/wt-codex
  Trust this folder? Codex can read, edit, and run files here, subject to your permission settings.
› 1. Trust and continue
  2. Quit
  enter continue · esc quit"""

CODEX_HOOKS = """  Hooks need review
  4 hooks are new or changed.
  Hooks can run outside the sandbox after you trust them.
› 1. Review hooks
  2. Trust all and continue
  3. Continue without trusting (hooks won't run)
  enter confirm · esc skip"""

CLAUDE_IDLE = """⏺ done
✻ Cooked for 3s · done 5:50 PM
❯
  ⏵⏵ accept edits on (shift+tab to cycle)"""


class PromptsTest(unittest.TestCase):
    def test_claude_trust_never_bare_enter(self):
        self.assertEqual(prompts.trust_keys(CLAUDE_TRUST), ['down', 'enter'])

    def test_codex_trust(self):
        self.assertEqual(prompts.trust_keys(CODEX_TRUST), ['enter'])

    def test_no_prompt(self):
        self.assertIsNone(prompts.trust_keys(CLAUDE_IDLE))
        self.assertIsNone(prompts.trust_keys(CODEX_HOOKS))

    def test_hook_review(self):
        self.assertTrue(prompts.hook_review(CODEX_HOOKS))
        self.assertFalse(prompts.hook_review(CODEX_TRUST))
