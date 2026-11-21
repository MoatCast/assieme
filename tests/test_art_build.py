"""Step 31: the app's musicians come from art/musicians.py; the generated module must match it."""
import importlib.util
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, os.path.join(ROOT, 'art'))
import musicians as art  # noqa: E402  the art file, not assieme.musicians


def build():
    spec = importlib.util.spec_from_file_location('art_build', os.path.join(ROOT, 'art', 'build.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ArtTest(unittest.TestCase):

    def test_generated_module_is_current(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 'm.ts')
            with open(out, 'w') as f:
                f.write(build().module())
            with open(out) as a, open(os.path.join(ROOT, 'app', 'src', 'musicians.gen.ts')) as b:
                self.assertEqual(a.read(), b.read(), 'run: python3 art/build.py')

    def test_every_instrument_has_four_poses(self):
        for name, s in art.SPRITES.items():
            for g in [*s['play'], s['rest'], s['hand']]:
                self.assertEqual((len(g), {len(r) for r in g}), (art.HEIGHT, {art.WIDTH}), name)
                self.assertTrue(set(''.join(g)) <= set(art.LETTERS + '.'), name)
            self.assertNotEqual(s['play'][0], s['play'][1], name)

    def test_coral_only_in_the_hand(self):
        for name, s in art.SPRITES.items():
            self.assertNotIn('R', ''.join(s['play'][0] + s['play'][1] + s['rest']), name)
            self.assertIn('R', ''.join(s['hand']), name)


if __name__ == '__main__':
    unittest.main()
