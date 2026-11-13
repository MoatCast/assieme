"""Step 30: setup, run and archive scripts brought over from another tool, asked once before they run."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from assieme import scripts


class Rewrite(unittest.TestCase):
    def test_rewrite_conductor_vars(self):
        line, unknown = scripts.rewrite('PORT=$CONDUCTOR_PORT x "$CONDUCTOR_WORKSPACE_PATH/a" ${CONDUCTOR_ROOT_PATH}')
        self.assertEqual(line, 'PORT=${PORT} x "${PWD}/a" ${CONDUCTOR_ROOT_PATH}')
        self.assertEqual(unknown, [])

    def test_rewrite_keeps_unknown(self):
        line, unknown = scripts.rewrite('echo $CONDUCTOR_FOO')
        self.assertEqual((line, unknown), ('echo $CONDUCTOR_FOO', ['CONDUCTOR_FOO']))

    def test_run_folders_with_spaces_and_quotes(self):
        d = os.path.join(os.path.realpath(tempfile.mkdtemp()), "My Code it's")
        os.makedirs(d)
        self.addCleanup(shutil.rmtree, os.path.dirname(d))
        got = scripts.run('echo "$CONDUCTOR_WORKSPACE_PATH|$CONDUCTOR_ROOT_PATH"', d, "/p q/it's")
        self.assertEqual(got['output'].strip(), f"{d}|/p q/it's")


class Consent(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        p = mock.patch.dict(os.environ, {'XDG_STATE_HOME': self.d})
        p.start()
        self.addCleanup(p.stop)

    def test_consent_is_for_that_line(self):
        self.assertFalse(scripts.allowed('shop', 'setup', 'npm ci'))
        scripts.allow('shop', 'setup', 'npm ci')
        self.assertTrue(scripts.allowed('shop', 'setup', 'npm ci'))
        self.assertFalse(scripts.allowed('shop', 'setup', 'npm ci && curl x'))
        self.assertFalse(scripts.allowed('other', 'setup', 'npm ci'))
        self.assertFalse(scripts.allowed('shop', 'archive', 'npm ci'))

    def test_allow_unknown_script_refused(self):
        with self.assertRaises(ValueError):
            scripts.allow('shop', 'deploy', 'x')


class Run(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d)

    def test_run_in_folder_with_clean_env(self):
        with mock.patch.dict(os.environ, {'CLAUDECODE': '1'}):
            got = scripts.run('pwd; echo "[$CLAUDECODE]"; echo $PORT', self.d, '/p', port=4100)
        self.assertTrue(got['ok'])
        self.assertEqual(got['output'].split(), [self.d, '[]', '4100'])

    def test_run_failure_and_timeout(self):
        self.assertFalse(scripts.run('exit 3', self.d, '/p')['ok'])
        got = scripts.run('sleep 5', self.d, '/p', timeout=0.2)
        self.assertFalse(got['ok'])
        self.assertIn('took longer than', got['output'])


if __name__ == '__main__':
    unittest.main()
