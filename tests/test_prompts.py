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


class TallScreenTest(unittest.TestCase):
    """Claude Code draws at the top of a tall screen (120 rows here) and leaves the rest blank."""

    def test_menu_and_question_on_a_tall_screen(self):
        tall = fixture('claude_question.txt') + '\n' * 85 + '──\n'
        self.assertEqual(prompts.menu(tall), [(1, 'Tabs'), (2, 'Spaces')])
        q = '⏺ Should the file be named hello.txt or greeting.txt?\n\n───\n❯ \n───\n' + '\n' * 90 + '──\n'
        self.assertEqual(prompts.question(q), 'Should the file be named hello.txt or greeting.txt?')


class StatusLinesTest(unittest.TestCase):
    def test_question_followed_by_claude_status_lines(self):
        screen = ('  - Leave 2 CPU cores free.\n\n⏺ Should the file be named hello.txt or greeting.txt?\n\n'
                  '✻ Brewed for 3s · done 8:31 PM\n\n● full  $0.25  last $0.25  4s\n\n' + '─' * 40 +
                  '\n❯\xa0\n' + '─' * 40 + '\n  ⏵⏵ auto mode on (shift+tab to cycle)\n')
        self.assertEqual(prompts.question(screen), 'Should the file be named hello.txt or greeting.txt?')


# Claude Code's first run, as read from its terminal (v2.1.323; the theme screen captured in a fresh home folder).
THEME = """Welcome to Claude Code v2.1.323
Let's get started.
Choose the text style that looks best with your terminal
To change this later, run /theme
 Auto (match terminal)
 ❯ ✔ Dark mode
   Light mode"""
SECURITY = """Security notes:
 1. Claude can make mistakes
 2. Due to prompt injection risks, only use it with code you trust
Press Enter to continue…"""
LOGIN = """Select login method:
 ❯ 1. Claude account with subscription · Individual, Team, or Enterprise
   2. Anthropic Console account · API usage billing"""


AGY_THEME = """Welcome to Antigravity CLI!

Choose your color scheme:        ╭──────────────╮
  > terminal                     │ > you: add a │
    light                        │              │
    [Next]
  ↑/↓ Navigate · enter Confirm"""


class FirstRunTest(unittest.TestCase):
    def test_antigravitys_color_scheme_is_passed_with_enter(self):
        self.assertEqual(prompts.first_run_keys(AGY_THEME), ['enter'])

    def test_the_theme_and_security_screens_are_passed_with_enter(self):
        self.assertEqual(prompts.first_run_keys(THEME), ['enter'])
        self.assertEqual(prompts.first_run_keys(SECURITY), ['enter'])

    def test_spaces_lost_in_a_screen_read_still_match(self):
        self.assertEqual(prompts.first_run_keys(THEME.replace(' ', '')), ['enter'])

    def test_sign_in_and_anything_else_are_left_to_the_person(self):
        for screen in (LOGIN, CLAUDE_TRUST, CLAUDE_IDLE, 'Press Enter to continue'):
            self.assertIsNone(prompts.first_run_keys(screen), screen)
