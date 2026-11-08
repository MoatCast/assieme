"""F8 process weight: merged items, how long each took from first commit to merge, and which gate or rule cost the most.

Reads git. Pull request data comes through `gh` only when asked (`assieme report --prs`), because it goes over the
network. Only `assieme report` calls this: never the brain's refresh.
"""
import json
import os
import re
import subprocess

from .detect import FIX_SUBJECT, _finding
from .model import parse_ts

MIN_HOURS = 4           # an item open at least this long...
LINES_PER_HOUR = 10     # ...with fewer changed lines per hour than this took far more process than its size
GATE_MIN_HOURS = 1      # the costliest gate is only worth naming from here
MAX_MERGES = 200        # per repository
GH_FIELDS = 'number,createdAt,mergedAt,additions,deletions,mergeCommit,commits,reviews,statusCheckRollup'


def _git(cwd, *args):
    """stdout, or None when git fails. Read-only commands only."""
    try:
        res = subprocess.run(['git', '-C', cwd, *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 else None


def _repos(cwds):
    """Working folders -> one folder per repository: a repository's worktrees share a common git folder."""
    out = {}
    for cwd in cwds:
        if not cwd or not os.path.isdir(cwd):
            continue
        common = _git(cwd, 'rev-parse', '--git-common-dir')
        if common:
            out.setdefault(os.path.realpath(os.path.join(cwd, common.strip())), cwd)
    return list(out.values())


def _base(cwd):
    head = _git(cwd, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
    if head:
        return head.strip()
    return next((b for b in ('main', 'master') if _git(cwd, 'rev-parse', '--verify', '-q', b)), None)


def _merges(cwd, name, since):
    base = _base(cwd)
    out = _git(cwd, 'log', base, '--first-parent', '--merges', f'--since={since.isoformat()}', f'-n{MAX_MERGES}',
               '--format=%H %P %ct') if base else None
    items = []
    for line in (out or '').splitlines():
        parts = line.split()
        if len(parts) != 4:  # an octopus merge has no one branch to measure
            continue
        sha, first, second, merged = parts
        log = _git(cwd, 'log', f'{first}..{second}', '--format=%ct %s')
        rows = [x.split(' ', 1) for x in (log or '').splitlines()]
        if not rows:
            continue
        stat = _git(cwd, 'diff', '--shortstat', f'{first}...{second}') or ''
        items.append({'repo': name, 'id': sha[:8], 'sha': sha, 'commits': len(rows),
                      'fixes': sum(bool(FIX_SUBJECT.match((r + [''])[1])) for r in rows),
                      'hours': round((int(merged) - min(int(r[0]) for r in rows)) / 3600, 2),
                      'lines': sum(int(n) for n in re.findall(r'(\d+) (?:insertion|deletion)', stat)),
                      'reviews': None, 'checks': None})
    return items


def run_gh(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60, check=True).stdout


def _pull_requests(cwd, name, since, gh):
    """-> (items, hours per check name). A failing `gh` adds nothing."""
    try:
        rows = json.loads(gh(['gh', 'pr', 'list', '--state', 'merged', '--limit', '100', '--search',
                              f'merged:>={since.strftime("%Y-%m-%d")}', '--json', GH_FIELDS], cwd))
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return [], {}
    items, gates = [], {}
    for pr in rows:
        created, merged = parse_ts(pr.get('createdAt')), parse_ts(pr.get('mergedAt'))
        if not (created and merged):
            continue
        checks = pr.get('statusCheckRollup') or []
        for c in checks:
            a, b = parse_ts(c.get('startedAt')), parse_ts(c.get('completedAt'))
            if a and b:
                key = c.get('name') or c.get('context') or 'check'
                gates[key] = gates.get(key, 0) + (b - a).total_seconds() / 3600
        items.append({'repo': name, 'id': f"#{pr.get('number')}", 'sha': (pr.get('mergeCommit') or {}).get('oid'),
                      'commits': len(pr.get('commits') or []),
                      'fixes': sum(bool(FIX_SUBJECT.match(c.get('messageHeadline') or '')) for c in pr.get('commits') or []),
                      'hours': round((merged - created).total_seconds() / 3600, 2),
                      'lines': (pr.get('additions') or 0) + (pr.get('deletions') or 0),
                      'reviews': len(pr.get('reviews') or []), 'checks': len(checks)})
    return items, {k: round(v, 2) for k, v in gates.items()}


def _heavy(item):
    return item['hours'] >= MIN_HOURS and item['lines'] < LINES_PER_HOUR * item['hours']


def report(cwds, since, now, gh=None):
    """Merged items in the window across these folders' repositories. gh: run `gh` too (run_gh, or a stand-in)."""
    items, gates = [], {}
    repos = _repos(cwds)
    for cwd in repos:
        name = os.path.basename(cwd.rstrip(os.sep))
        merges = _merges(cwd, name, since)
        prs, checks = _pull_requests(cwd, name, since, gh) if gh else ([], {})
        known = {p['sha'] for p in prs}
        items += prs + [m for m in merges if m['sha'] not in known]
        for k, v in checks.items():
            gates[k] = round(gates.get(k, 0) + v, 2)
    findings = []
    for i in items:
        if _heavy(i):
            done = [f"{i['commits']} commits", f"{i['fixes']} fixes"] + ([f"{i['reviews']} reviews", f"{i['checks']} checks"]
                                                                       if i['reviews'] is not None else [])
            findings.append(_finding('process', i['repo'], f"{i['repo']} {i['id']}: {i['hours']:.0f} h from first commit to merge "
                                     f"for {i['lines']} changed lines", f"Its process took far longer than its size: {', '.join(done)}",
                                     'Check whether its gates fit a change this small', hours=i['hours'], lines=i['lines']))
    longest = max((i['hours'] for i in items), default=0)
    reason = (f"{len(items)} merged items in {len(repos)} repositories, longest {longest:.0f} h; flags at {MIN_HOURS} h and "
              f"under {LINES_PER_HOUR} changed lines an hour" + ('' if gh else '; squash merges need --prs'))
    return {'items': items, 'gates': gates, 'findings': findings,
            'detector': {'id': 'F8 process weight', 'fired': len(findings), 'reason': reason}}


def gate_finding(log_gates, check_gates):
    """The gate or rule that cost the most hours, from the logs and pull request checks, or None."""
    gates = {**log_gates}
    for k, v in check_gates.items():
        gates[k] = gates.get(k, 0) + v
    top = max(gates, key=gates.get, default=None)
    if not top or gates[top] < GATE_MIN_HOURS:
        return None
    return _finding('gate', None, f'{top} cost the most: {gates[top]:.1f} h in this window',
                    f'{100 * gates[top] / sum(gates.values()):.0f}% of the time spent on gates and refusals',
                    'Narrow it, or run it only before a merge' if not top.startswith('refused') else 'Fix the rule or the brief',
                    hours=gates[top])
