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


def fixture(name):
    import os
    with open(os.path.join(os.path.dirname(__file__), 'fixtures', 'screens', name)) as f:
        return f.read()


LONG = 'Yes, and switch to accept edits (auto-approve file edits and common file commands) for this session (shift+tab)'


class MenuTest(unittest.TestCase):
    def test_menu_from_real_screens(self):
        self.assertEqual(prompts.menu(fixture('claude_permission.txt')),
                         [(1, 'Yes'), (2, LONG[:39] + '…'), (3, 'No')])
        self.assertEqual(prompts.menu(fixture('claude_question.txt')), [(1, 'Tabs'), (2, 'Spaces')])
        self.assertEqual(prompts.menu(CODEX_TRUST), [(1, 'Trust and continue'), (2, 'Quit')])
        self.assertEqual([n for n, _ in prompts.menu(CODEX_HOOKS)], [1, 2, 3])

    def test_menu_takes_last_block(self):
        screen = 'Plan:\n1. backoff\n2. tests\n3. review\n\nAllow?\n❯ 1. Yes\n  2. No, and tell Claude what to do\n'
        self.assertEqual(prompts.menu(screen), [(1, 'Yes'), (2, 'No, and tell Claude what to do')])

    def test_menu_rules(self):
        self.assertEqual(prompts.menu('❯ 1. Only one\n'), [])
        self.assertEqual(prompts.menu('\n'.join(f'{i}. item {i}' for i in range(1, 11))), [])
        self.assertEqual(prompts.menu('1. a\n3. b\n'), [])
        self.assertEqual(prompts.menu('no menu here'), [])
        self.assertEqual(len(prompts.menu('❯ 1. ' + 'x' * 80 + '\n  2. b\n')[0][1]), 40)


class QuestionTest(unittest.TestCase):
    def test_question(self):
        screen = '⏺ Read 3 files\n\n⏺ Split into parts.\n\n  Before part 4: behind a flag,\n  or straight to main?\n\n───\n❯ \n───\n'
        self.assertEqual(prompts.question(screen), 'Before part 4: behind a flag, or straight to main?')
        self.assertEqual(prompts.question('• Should I add a test?\n\n› Ask Codex to do anything\n'),
                         'Should I add a test?')

    def test_no_question(self):
        self.assertIsNone(prompts.question('⏺ Done. All tests pass.\n\n❯ \n'))
        self.assertIsNone(prompts.question('⏺ Should I go on?\n\n  I went on and finished.\n\n❯ \n'))
        self.assertIsNone(prompts.question('nothing from the agent'))


class MenuPointerTest(unittest.TestCase):
    def test_agent_list_without_pointer_is_not_a_menu(self):
        screen = '⏺ Two ways:\n\n  1. Refactor the parser\n  2. Patch the caller\n\n  Which do you prefer?\n\n───\n❯ \n───\n'
        self.assertEqual(prompts.menu(screen), [])

    def test_menu_context_is_the_text_above(self):
        a = fixture('claude_permission.txt')
        b = a.replace('note.txt', 'other.txt')
        self.assertEqual(prompts.menu(a), prompts.menu(b))
        self.assertNotEqual(prompts.menu_context(a), prompts.menu_context(b))
        self.assertIn('Do you want to create note.txt?', prompts.menu_context(a))
