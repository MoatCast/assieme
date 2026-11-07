"""wakegraph report --hours 24 [--json|--md]"""
import argparse
import datetime as dt
import os
import sys

from . import adapters, report


def main(argv=None):
    p = argparse.ArgumentParser(prog='wakegraph', description='Who woke whom in multi-agent coding teams, and what it cost.')
    sub = p.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('report', help='summarise the last N hours of local agent logs')
    r.add_argument('--hours', type=float, default=24)
    fmt = r.add_mutually_exclusive_group()
    fmt.add_argument('--json', action='store_true')
    fmt.add_argument('--md', action='store_true', help='Markdown (the default)')
    r.add_argument('--claude-dir', default=os.path.expanduser('~/.claude/projects'))
    r.add_argument('--codex-dir', default=os.path.expanduser('~/.codex/sessions'))
    r.add_argument('--generic', action='append', default=[], metavar='FILE', help='normalised-events JSONL (repeatable)')
    r.add_argument('--show-text', action='store_true', help='include wake snippets and command arguments (off: names only)')
    args = p.parse_args(argv)

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=args.hours)
    sessions = []
    if os.path.isdir(args.claude_dir):
        sessions += adapters.claude_code(args.claude_dir, since, args.show_text)
    if os.path.isdir(args.codex_dir):
        sessions += adapters.codex(args.codex_dir, since, args.show_text)
    for path in args.generic:
        sessions += adapters.generic(path, since, args.show_text)

    r = report.aggregate(sessions)
    meta = {'hours': args.hours, 'since': since.strftime('%Y-%m-%dT%H:%M:%SZ')}
    sys.stdout.write(report.to_json(r, meta) + '\n' if args.json else report.to_md(r, meta, args.show_text))
    return 0
