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


class PresetTest(unittest.TestCase):
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
        self.assertEqual(t.roles[0].brief.strip(), 'You are the coordinator.')

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
