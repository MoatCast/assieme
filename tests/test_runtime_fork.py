"""Step 16: the licence's terms for the fork, and that the app runs on it."""
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


def test_the_brain_runs_asm():
    assert "['asm', 'server', 'start']" in (ROOT / 'assieme/runtime.py').read_text()


# The only places `hc` may stay once Assieme runs on asm: each is (file, text on the line). The person's own herdr
# keeps its `hc`, and code reading what agents typed or received accepts both.
HC_ALLOWED = [
    ('assieme/herdr.py', ''),  # the person's own herdr, HC_VALUED included
    ('assieme/status.py', 'hc_path'),
    ('assieme/status.py', "herdr install's own hc"),
    ('assieme/status.py', "os.path.join(os.path.dirname(herdr), 'hc')"),
    ('assieme/status.py', "setup.agent_path('hc')"),
    ('assieme/status.py', "any `hc` command starts a core of its own"),
    ('assieme/status.py', "os.environ.get('HC_SESSION')"),
    ('assieme/status.py', "os.environ.get('HC_SOCKET')"),
    ('assieme/status.py', 'None when hc is missing'),
    ('assieme/status.py', "herdr: optional `hc ls --json` terminals"),
    ('assieme/status.py', "'hc send builder --if idle"),  # reads both
    ('assieme/status.py', "in ('asm', 'hc')"),
    ('assieme/status.py', r'(?:asm|hc)\s+wait'),
    ('assieme/teammap.py', "through the `hc` command"),  # HerdrRt
    ('assieme/teammap.py', "['hc', *args]"),
    ('assieme/teammap.py', "or 'hc failed'"),
    ('assieme/teammap.py', "in the shape of `hc`'s rows"),
    ('assieme/serve.py', "'hc': 'hc'"),
    ('assieme/cli.py', "herdr-core (`hc ls --json`)"),
    ('assieme/live.py', 'a nudge through `hc send`'),  # the live view nudges herdr terminals
    ('assieme/live.py', 'f"hc attach'),
    ('assieme/live.py', 'with `hc send`'),
    ('assieme/live.py', "['hc', 'send'"),
    ('assieme/adapters.py', 'herdr `hc`'),  # reads both
    ('assieme/adapters.py', '<hc from='),
    ('assieme/detect.py', "'asm', 'hc',"),
    ('assieme/model.py', "hc (the person's herdr)"),
    ('assieme/runtime.py', "their `hc` keeps its default"),
    ('assieme/runtime.py', "startswith('HC_')"),
    ('assieme/quiet.py', "Never an `hc`"),
    ('assieme/quiet.py', "os.path.join(d, 'hc')"),  # removes the stand-in from before the switch
    ('assieme/quiet.py', "('asm', 'hc', BLOCKER)"),
    ('app/src-tauri/src/bridge.rs', 'herdr-core/{session}'),
    ('app/src-tauri/src/bridge.rs', "person's own herdr-core"),
    ('app/src/FirstRun.test.tsx', 'hc attach t2'),  # herdr text
    ('app/src/FirstRun.test.tsx', 'hc: '),
    ('app/src/Shell.test.tsx', 'hc: true'),
    ('app/src/bridge.ts', 'hc: boolean'),  # serve's CLIS['hc']: the person's herdr
]


def test_no_bundled_hc():
    out = subprocess.run(['git', 'grep', '-nP', r'\bhc\b|HC_|hc\.sock|hc-bin|<hc ', '--', 'assieme', 'app/src',
                          'app/src-tauri', 'app/scripts'], cwd=ROOT, capture_output=True, text=True).stdout
    left = [line for line in out.splitlines()
            if not any(line.startswith(f + ':') and text in line.split(':', 2)[2] for f, text in HC_ALLOWED)]
    assert left == []
    conf = (ROOT / 'app/src-tauri/tauri.conf.json').read_text()
    assert '"resources/hc' not in conf and '"hc/' not in conf and '"hc-bin/' not in conf
