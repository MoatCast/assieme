"""Step 18a: the brain's actions, described for assistants (Claude Code, Codex, the Claude app)."""
import re
import unittest

from assieme import actions, cli, newteam


class CatalogueTest(unittest.TestCase):

    def test_every_handler_is_catalogued_or_hidden(self):  # a new app action can't miss the assistants
        names = set(cli.handler_names())
        self.assertGreater(len(names), 100)
        self.assertEqual(names - set(actions.CATALOGUE) - actions.hidden(names), set())
        self.assertEqual(set(actions.CATALOGUE) & actions.hidden(names), set())
        self.assertEqual(set(actions.CATALOGUE) - names, set())  # no description for an action that isn't there

    def test_hidden_never_listed_or_run(self):
        self.assertNotIn('brain.snapshot', [a['name'] for a in actions.listing()])
        self.assertFalse(any(a['name'].startswith('mcp.') for a in actions.listing()))
        with self.assertRaisesRegex(ValueError, 'No action named brain.snapshot'):
            actions.run({'brain.snapshot': lambda: {}}, 'brain.snapshot', {})
        with self.assertRaisesRegex(ValueError, 'No action named nope.nope'):
            actions.run({}, 'nope.nope', {})

    def test_changes_flag(self):
        self.assertTrue(actions.CATALOGUE['review.merge'].changes)
        self.assertFalse(actions.CATALOGUE['review.get'].changes)
        self.assertFalse(actions.CATALOGUE['teams.list'].changes)

    def test_listing_shape_and_filter(self):
        got = actions.listing('review')
        self.assertTrue(got)
        for a in got:
            self.assertEqual(set(a), {'name', 'description', 'params', 'changes'})
            self.assertTrue('review' in a['name'] or 'review' in a['description'].lower())

    def test_copy(self):  # professional copy
        for name, a in actions.CATALOGUE.items():
            self.assertNotRegex(a.description.lower(), r'\b(thing|something|nothing|everything)\b', name)
            self.assertTrue(a.description.endswith('.'), name)

    def test_params_match_the_handlers(self):  # the description says what the handler takes
        for name, a in actions.CATALOGUE.items():
            for p in a.params:
                self.assertRegex(p, r'^[a-z_]+$', name)

    def test_run_passes_params(self):
        got = actions.run({'review.get': lambda team, role: {'team': team, 'role': role}}, 'review.get',
                          {'team': 'shop', 'role': 'builder-1'})
        self.assertEqual(got, {'team': 'shop', 'role': 'builder-1'})

    def test_a_failing_action_returns_its_message(self):  # Review Focus 1
        def bad(team):
            raise newteam.NewTeamError('team', 'No team named x.')
        with self.assertRaisesRegex(ValueError, '^No team named x.$'):
            actions.run({'team.stop': bad}, 'team.stop', {'team': 'x'})

    def test_wrong_params_say_what_it_takes(self):
        with self.assertRaisesRegex(ValueError, re.escape('team.stop takes: team')):
            actions.run({'team.stop': lambda team: None}, 'team.stop', {'teem': 'x'})

    def test_handlers(self):
        table = {'review.get': lambda team, role: 'diff'}
        h = actions.handlers(table)
        self.assertEqual(h['assistant.do'](name='review.get', params={'team': 's', 'role': 'r'}), 'diff')
        self.assertIn('review.get', [a['name'] for a in h['assistant.actions'](filter='review')])
