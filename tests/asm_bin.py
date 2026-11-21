"""Assieme's runtime for tests that drive a real core: `asm` on PATH, else the build in runtime/target."""
import os
import shutil
import tempfile
from pathlib import Path

BUILT = Path(__file__).resolve().parent.parent / 'runtime/target/debug/assieme-core'


def asm_dir():
    """A folder holding an `asm` to put first on PATH, or None when there is no asm to test with."""
    found = shutil.which('asm')
    if found:
        return os.path.dirname(found)
    if not BUILT.exists():
        return None
    d = tempfile.mkdtemp(prefix='asm-bin-')
    os.symlink(BUILT, os.path.join(d, 'asm'))
    return d


ASM_DIR = asm_dir()
