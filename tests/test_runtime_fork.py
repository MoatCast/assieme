"""Step 16: the licence's terms for the fork, and that the app still runs on hc."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / 'runtime'
CHANGED = 'Changed by Assieme'
# Licence texts stay as their authors wrote them; JSON can't hold a comment and NOTICE covers it.
EXEMPT = re.compile(r'^(LICENSE$|NOTICE$|README\.md$|\.gitignore$|Cargo\.lock$|distribution/notices/|distribution/windows/'
                    r'|crates/assieme-core/THIRD-PARTY-NOTICES$|crates/assieme-core/src/help\.txt$|docs/commands\.md$|schema/.*\.json$)')
# Files an interop string or a credit needs: a program started inside herdr sets this exact value.
MAY_NAME_HERDR = {'README.md', 'crates/assieme-core/src/api/run.rs', 'crates/assieme-core/tests/m1.rs'}


def files():
    out = subprocess.run(['git', 'ls-files', '-co', '--exclude-standard', 'runtime'], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    for line in out.splitlines():
        rel = line.removeprefix('runtime/')
        if not rel.startswith('vendor/') and (RUNTIME / rel).is_file():
            yield rel


def text(rel):
    try:
        return (RUNTIME / rel).read_text()
    except UnicodeDecodeError:
        return ''


def test_licence_and_notice_are_carried():
    assert 'Apache License' in text('LICENSE') and 'Version 2.0, January 2004' in text('LICENSE')
    notice = text('NOTICE')
    assert 'herdr-core' in notice and 'https://github.com/herdrdev/herdr-core' in notice and 'Changed by Assieme' in notice
    assert 'MIT License' in text('distribution/notices/ghostty.txt')


def test_every_changed_file_says_so():
    missing = [f for f in files() if not EXEMPT.match(f) and text(f) and CHANGED not in text(f)[:600]]
    assert missing == []


def test_the_herdr_name_is_only_where_it_must_be():
    def own_text(f):  # the credit line is meant to name them
        return '\n'.join(line for line in text(f).splitlines() if CHANGED not in line)
    named = [f for f in files() if not EXEMPT.match(f) and f not in MAY_NAME_HERDR and re.search('herdr', own_text(f), re.I)]
    assert named == []


def test_nothing_downloads_a_release():
    source = [f for f in files() if f.startswith('crates/assieme-core/src/') and 'releases.herdr' in text(f)]
    assert source == []


def test_the_app_and_bundle_still_run_on_hc():
    assert "['hc', 'server', 'start']" in (ROOT / 'assieme/runtime.py').read_text()
    assert not list((ROOT / 'app/src-tauri').glob('**/assieme-core*'))
