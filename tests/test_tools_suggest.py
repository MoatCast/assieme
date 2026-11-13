"""Step 32: suggestions by role, and what is off when an agent starts."""
import unittest

from assieme import tools
from assieme.preset import Role


def it(id_, description='', locked=False):
    type_, name = id_.split(':', 1)
    return {'id': id_, 'type': type_, 'name': name, 'description': description, 'locked': locked,
            'why': 'x' if locked else ''}


ITEMS = [it('server:browser-mcp'), it('server:vercel', 'Deploy to production'), it('skill:pdf', 'Read PDFs'),
         it('skill:literature-review'), it('plugin:shots', 'Drives Chrome to take screenshots'),
         it('server:netlify', locked=True)]


def role(**kw):
    return Role('b', 'claude', '', 'b', **kw)


class SuggestTest(unittest.TestCase):
    def test_reviewer_loses_browser_and_deploy(self):
        self.assertEqual(tools.suggest('reviewer', ITEMS), ['server:browser-mcp', 'server:vercel', 'plugin:shots'])
        self.assertEqual(tools.suggest('planner', ITEMS), ['server:browser-mcp', 'server:vercel', 'plugin:shots'])

    def test_builder_keeps_browser(self):
        self.assertEqual(tools.suggest('builder', ITEMS), ['server:vercel'])
        self.assertEqual(tools.suggest('tester', ITEMS), ['server:vercel'])

    def test_unplaced_stays_on(self):
        for kind in ('planner', 'builder', 'reviewer', 'tester'):
            self.assertNotIn('skill:literature-review', tools.suggest(kind, ITEMS))
            self.assertNotIn('skill:pdf', tools.suggest(kind, ITEMS))

    def test_whole_words_only(self):
        items = [it('skill:diagrams', 'Draws diagrams and releases nothing'), it('server:aws-tools'),
                 it('skill:pre-release-notes', 'Writes release notes')]
        self.assertEqual(tools.suggest('builder', items), ['server:aws-tools', 'skill:pre-release-notes'])

    def test_custom_role_nothing_off(self):
        self.assertEqual(tools.suggest(None, ITEMS), [])

    def test_locked_never_suggested(self):
        self.assertNotIn('server:netlify', tools.suggest('reviewer', ITEMS))


class OffTest(unittest.TestCase):
    def test_new_item_on_when_switch_on(self):
        self.assertEqual(tools.off(role(tools_off=['skill:pdf']), ITEMS), {'skill:pdf'})

    def test_new_item_off_when_switch_off(self):
        r = role(tools_off=[], tools_new=False, tools_known=['skill:pdf', 'server:vercel'])
        self.assertEqual(tools.off(r, ITEMS), {'server:browser-mcp', 'skill:literature-review', 'plugin:shots'})

    def test_reinstalled_item_keeps_saved_state(self):  # Review Focus 2
        r = role(tools_off=['server:vercel'], tools_new=False,
                 tools_known=[i['id'] for i in ITEMS])
        self.assertEqual(tools.off(r, ITEMS), {'server:vercel'})

    def test_uninstalled_id_ignored(self):
        self.assertEqual(tools.off(role(tools_off=['server:gone', 'skill:pdf']), ITEMS), {'skill:pdf'})

    def test_locked_never_off(self):
        self.assertEqual(tools.off(role(tools_off=['server:netlify']), ITEMS), set())

    def test_defaults_nothing_off(self):
        self.assertEqual(tools.off(role(), ITEMS), set())


if __name__ == '__main__':
    unittest.main()
