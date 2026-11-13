"""Step 30: a brought-over agent picks up its conversation on its first start only."""
import os
import unittest
from unittest import mock

from assieme import launch, newteam, preset, team
from assieme.preset import Role
from tests.test_newteam import StarterTest, role


def argv(r, cwd='/w'):
    return launch.argv(r, block_push=False, which=lambda n: None, cwd=cwd)


class ResumeArgv(unittest.TestCase):
    def test_argv_claude_resume(self):
        a = argv(Role('b', 'claude', '', 'b', resume='abc-123'))
        self.assertEqual(a[a.index('--resume') + 1], 'abc-123')

    def test_argv_codex_resume(self):
        a = argv(Role('b', 'codex', '', 'b', resume='019a-xyz'))
        self.assertEqual(a[:3], ['codex', 'resume', '019a-xyz'])
        self.assertIn('check_for_update_on_startup=false', a)

    def test_argv_without_resume(self):
        self.assertNotIn('--resume', argv(Role('b', 'claude', '', 'b')))
        self.assertNotIn('resume', argv(Role('b', 'codex', '', 'b')))

    def test_argv_gemini_ignores_resume(self):
        a = argv(Role('b', 'gemini', '', 'b', resume='abc'), cwd=self._tmp())
        self.assertNotIn('abc', ' '.join(a))

    def _tmp(self):
        import tempfile, shutil
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        return d


class ResumeStart(StarterTest):
    def kept_roles(self):
        return [role('builder') | {'resume': 'abc-123'}, role('reviewer', checkout='read-only', parent='builder')]

    def test_start_clears_resume_after_start(self):
        s = self.starter()
        s.start(self.repo, 'Fix login', self.kept_roles(), {})
        self.manual.run_all()
        tm = preset.load(os.path.join(self.teams, 'repo'), practices=False)
        self.assertEqual([r.resume for r in tm.roles], [None, None])

    def test_start_failure_keeps_resume(self):
        self.result = lambda tm: [team.Started(r.name, None, 'exited', 'no') for r in tm.roles]
        s = self.starter()
        s.start(self.repo, 'Fix login', self.kept_roles(), {})
        self.manual.run_all()
        tm = preset.load(os.path.join(self.teams, 'repo'), practices=False)
        self.assertEqual({r.name: r.resume for r in tm.roles}['builder'], 'abc-123')


if __name__ == '__main__':
    unittest.main()
