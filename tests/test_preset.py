"""Team presets: team.toml plus one Markdown brief per role."""
import os
import shutil
import tempfile
import unittest

from assieme import preset

TEAM = '''name = "demo"
project = "{project}"

[[role]]
name = "builder-1"
agent = "codex"
brief = "builder.md"
parent = "coordinator"

[[role]]
name = "coordinator"
agent = "claude"
model = "opus"
brief = "coordinator.md"
checkout = "read-only"
'''

BRIEF = '---\ndescription: x\n---\n{body}\n'


class _Folder(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def write(self, team, briefs=('builder.md', 'coordinator.md')):
        with open(os.path.join(self.d, 'team.toml'), 'w') as f:
            f.write(team)
        for b in briefs:
            with open(os.path.join(self.d, b), 'w') as f:
                f.write(BRIEF.format(body=f'You are the {b[:-3]}.'))


class PresetTest(_Folder):
    def test_loads_example_team(self):
        self.write(TEAM.format(project=self.d))
        t = preset.load(self.d)
        self.assertEqual(t.name, 'demo')
        self.assertEqual([r.name for r in t.roles], ['coordinator', 'builder-1'])
        self.assertEqual(t.roles[1].agent, 'codex')
        self.assertEqual(t.roles[1].checkout, 'branch')
        self.assertEqual(t.roles[1].parent, 'coordinator')
        self.assertEqual(t.roles[0].checkout, 'read-only')
        self.assertEqual(t.roles[0].model, 'opus')
        self.assertIsNone(t.roles[1].model)
        self.assertTrue(t.roles[0].brief.startswith('You are the coordinator.'))

    def test_project_tilde_and_relative(self):
        self.write(TEAM.format(project='~/x'))
        self.assertEqual(preset.load(self.d).project, os.path.expanduser('~/x'))
        self.write(TEAM.format(project='../repo'))
        self.assertEqual(preset.load(self.d).project, os.path.normpath(os.path.join(self.d, '../repo')))
        self.write(TEAM.replace('project = "{project}"\n', ''))
        self.assertEqual(preset.load(self.d).project, os.path.abspath(self.d))

    def test_parent_ordered_first(self):
        self.write(TEAM.format(project=self.d))
        names = [r.name for r in preset.load(self.d).roles]
        self.assertLess(names.index('coordinator'), names.index('builder-1'))

    def test_practices_default_empty(self):
        self.write(TEAM.format(project=self.d))
        self.assertEqual(preset.load(self.d).practices, {})

    def test_rejects(self):
        base = TEAM.format(project=self.d)
        cases = [
            (base.replace('agent = "codex"', 'agent = "gemini"'), 'agent'),
            (base.replace('name = "builder-1"', 'name = "coordinator"').replace('parent = "coordinator"\n', ''), 'duplicate'),
            (base.replace('parent = "coordinator"', 'parent = "nobody"'), 'parent'),
            (base.replace('brief = "builder.md"', 'brief = "missing.md"'), 'brief'),
            (base.replace('checkout = "read-only"', 'checkout = "sometimes"'), 'checkout'),
        ]
        for text, msg in cases:
            self.write(text)
            with self.subTest(msg=msg), self.assertRaisesRegex(preset.PresetError, msg):
                preset.load(self.d)

    def test_missing_team_toml(self):
        with self.assertRaisesRegex(preset.PresetError, 'team.toml'):
            preset.load(self.d)


class PracticesTest(_Folder):
    def test_job_read(self):
        self.write(TEAM.format(project=self.d).replace('agent = "codex"', 'agent = "codex"\njob = "Writes the code"'))
        roles = {r.name: r for r in preset.load(self.d).roles}
        self.assertEqual(roles['builder-1'].job, 'Writes the code')
        self.assertEqual(roles['coordinator'].job, '')

    def test_practices_on_by_default(self):
        self.write(TEAM.format(project=self.d))
        tm = preset.load(self.d)
        for g in preset.practices()['groups']:
            self.assertIn(g['rules'][0]['text'], tm.roles[0].brief)
        self.assertIn('Team practices', tm.roles[0].brief)

    def test_practice_off(self):
        self.write(TEAM.format(project=self.d) + '\n[practices]\nreviews = false\n')
        tm = preset.load(self.d)
        groups = {g['id']: g for g in preset.practices()['groups']}
        for r in tm.roles:
            for rule in groups['reviews']['rules']:
                self.assertNotIn(rule['text'], r.brief)
            self.assertIn(groups['safety']['rules'][0]['text'], r.brief)

    def test_load_without_practices(self):
        self.write(TEAM.format(project=self.d))
        self.assertNotIn('Team practices', preset.load(self.d, practices=False).roles[0].brief)

    def test_practices_list_shape(self):
        p = preset.practices()
        self.assertEqual([g['id'] for g in p['groups']], ['messages', 'safety', 'reviews', 'stopping', 'machine'])
        self.assertTrue(all(g['title'] and g['summary'] and g['detail'] and g['rules'] for g in p['groups']))
        rules = [r for g in p['groups'] for r in g['rules']]
        self.assertTrue(all(r['id'] and r['title'] and r['detail'] and r['text'] for r in rules))
        ids = [r['id'] for r in rules] + [g['id'] for g in p['groups']] + [b['id'] for b in p['bigger']]
        self.assertEqual(len(ids), len(set(ids)))  # one flat key per switch
        self.assertEqual(len(p['basics']), 10)
        self.assertEqual([b['id'] for b in p['bigger']], ['steward', 'research-pairs'])

    def test_one_rule_off_keeps_the_rest_of_its_group(self):
        self.write(TEAM.format(project=self.d) + '\n[practices]\nreport-line = false\n')
        brief = preset.load(self.d).roles[0].brief
        texts = {r['id']: r['text'] for g in preset.practices()['groups'] for r in g['rules']}
        self.assertNotIn(texts['report-line'], brief)
        self.assertIn(texts['brief-file'], brief)

    def test_a_rule_on_in_a_group_that_is_off_stays_off(self):
        self.assertFalse(preset.rule_on({'safety': False, 'no-push': True}, 'no-push'))
        self.assertTrue(preset.rule_on({}, 'no-push'))
        self.assertFalse(preset.rule_on({'no-push': False}, 'no-push'))

    def test_unknown_practice_id_rejected(self):
        self.write(TEAM.format(project=self.d) + '\n[practices]\ntypo = false\n')
        with self.assertRaisesRegex(preset.PresetError, 'typo'):
            preset.load(self.d)


RESEARCH = '''name = "demo"
project = "{project}"

[[role]]
name = "lead"
agent = "claude"
brief = "coordinator.md"
checkout = "read-only"

[[role]]
name = "researcher-1"
agent = "codex"
brief = "builder.md"
parent = "lead"
'''


class BiggerTeamsTest(_Folder):
    def roles(self, practices=''):
        self.write(RESEARCH.format(project=self.d) + practices)
        return {r.name: r for r in preset.load(self.d).roles}

    def test_off_by_default(self):
        self.assertEqual(set(self.roles()), {'lead', 'researcher-1'})

    def test_steward_waits_on_the_top_agent_and_everyone_sends_it_stops(self):
        roles = self.roles('\n[practices]\nsteward = true\n')
        s = roles['steward']
        self.assertEqual((s.agent, s.checkout, s.parent), ('claude', 'read-only', 'lead'))
        self.assertIn('steward', s.brief.lower())
        self.assertIn('Team practices', s.brief)
        for name in ('lead', 'researcher-1'):
            self.assertIn(preset.STEWARD_LINE, roles[name].brief)

    def test_research_pairs_add_a_critic_per_researcher(self):
        roles = self.roles('\n[practices]\nresearch-pairs = true\n')
        c = roles['researcher-1-critic']
        self.assertEqual((c.agent, c.checkout, c.parent), ('codex', 'read-only', 'researcher-1'))
        self.assertIn('researcher-1-critic', roles['researcher-1'].brief)
        self.assertNotIn('lead-critic', roles)

    def test_both_together_and_parents_come_first(self):
        self.write(RESEARCH.format(project=self.d) + '\n[practices]\nsteward = true\nresearch-pairs = true\n')
        order = [r.name for r in preset.load(self.d).roles]
        self.assertEqual(set(order), {'lead', 'researcher-1', 'researcher-1-critic', 'steward'})
        self.assertLess(order.index('researcher-1'), order.index('researcher-1-critic'))
        self.assertIn(preset.STEWARD_LINE, {r.name: r for r in preset.load(self.d).roles}['researcher-1-critic'].brief)

    def test_a_team_without_researchers_is_unchanged(self):
        self.write(TEAM.format(project=self.d) + '\n[practices]\nresearch-pairs = true\n')
        self.assertEqual({r.name for r in preset.load(self.d).roles}, {'builder-1', 'coordinator'})

    def test_never_while_editing_the_saved_team(self):
        self.write(RESEARCH.format(project=self.d) + '\n[practices]\nsteward = true\n')
        self.assertEqual({r.name for r in preset.load(self.d, practices=False).roles}, {'lead', 'researcher-1'})


class ModelEffortPresetTest(_Folder):
    def test_effort_read(self):
        self.write(TEAM.format(project=self.d).replace('model = "opus"', 'model = "opus"\neffort = "high"'))
        roles = {r.name: r for r in preset.load(self.d).roles}
        self.assertEqual((roles['coordinator'].model, roles['coordinator'].effort), ('opus', 'high'))
        self.assertIsNone(roles['builder-1'].effort)

    def test_bad_effort_rejected(self):
        self.write(TEAM.format(project=self.d).replace('model = "opus"', 'model = "opus"\neffort = "huge"'))
        with self.assertRaisesRegex(preset.PresetError, 'coordinator.*huge'):
            preset.load(self.d)
