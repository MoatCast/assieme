"""The shared team brief: what is kept, what agents read, and who may add a decision."""
import os
import tempfile
import unittest
from unittest import mock

from assieme import brief


class BriefTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = mock.patch.dict(os.environ, {'XDG_STATE_HOME': d.name})
        p.start()
        self.addCleanup(p.stop)

    def test_empty_until_something_is_written(self):
        self.assertEqual(brief.load('shop'), {'goal': '', 'rules': '', 'decisions': []})
        self.assertEqual(brief.text('shop'), '')

    def test_save_changes_only_what_it_is_given(self):
        brief.save('shop', goal=' Ship retries ', rules='No API changes')
        brief.save('shop', goal='Ship backoff')
        self.assertEqual((brief.load('shop')['goal'], brief.load('shop')['rules']), ('Ship backoff', 'No API changes'))

    def test_agents_read_goal_rules_and_decisions(self):
        brief.save('shop', goal='Ship retries', rules='No API changes')
        brief.add('shop', 'planner', 'Flag or\nmain?', 'Behind a flag')
        t = brief.text('shop')
        for part in ('Goal: Ship retries', 'No API changes', '- Flag or main? -> Behind a flag'):
            self.assertIn(part, t)
        self.assertNotIn('Goal:', brief.text('shop', goal=False))

    def test_the_same_question_again_replaces_its_answer(self):
        brief.add('shop', 'a', 'Which port?', '8080')
        brief.add('shop', 'b', 'which port?', '9090')
        self.assertEqual([(d['a'], d['role']) for d in brief.load('shop')['decisions']], [('9090', 'b')])

    def test_an_agents_record_right_after_ours_is_the_same_answer(self):
        brief.add('shop', 'a', 'Which port?', '8080', now=1000)
        brief.add('shop', 'a', 'What port should I use?', 'Port 8080', by='agent', now=1100)
        brief.add('shop', 'b', 'Tabs or spaces?', 'Spaces', by='agent', now=1100)  # another agent: kept
        brief.add('shop', 'a', 'Which Python?', '3.12', by='agent', now=2000)      # later: a new decision
        self.assertEqual([d['q'] for d in brief.load('shop')['decisions']], ['Which port?', 'Tabs or spaces?', 'Which Python?'])

    def test_limits_and_remove(self):
        for n in range(brief.KEEP + 3):
            brief.add('shop', 'a', f'Q{n}?', 'x' * 900, now=n)
        ds = brief.load('shop')['decisions']
        self.assertEqual((len(ds), ds[0]['q'], len(ds[0]['a'])), (brief.KEEP, 'Q3?', brief.ANSWER_MAX))
        brief.remove('shop', ds[0]['id'])
        self.assertEqual(brief.load('shop')['decisions'][0]['q'], 'Q4?')
        with self.assertRaises(ValueError):
            brief.add('shop', 'a', ' ', 'x')

    def test_a_damaged_file_and_an_odd_team_name(self):
        brief.save('my team/1', goal='x')
        self.assertEqual(os.listdir(os.path.dirname(brief._file('my team/1'))), ['my-team-1.json'])
        with open(brief._file('shop'), 'w') as f:
            f.write('{not json')
        self.assertEqual(brief.load('shop')['decisions'], [])


if __name__ == '__main__':
    unittest.main()
