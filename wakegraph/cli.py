"""wakegraph            live view (the default)
wakegraph status     one snapshot of team health
wakegraph watch      notifications only, no screen
wakegraph report     who woke whom, what it cost, what was refused
"""
import argparse
import datetime as dt
import os
import sys
import time

from . import adapters, live, quiet, report, rules, status


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['shim']:  # the hc stand-in: hc's own flags must reach it untouched
        return quiet.shim_main(argv[1:])
    p = argparse.ArgumentParser(prog='wakegraph', description='Run with no command for the live view: team health, '
                                'what needs you, and one-key fixes. Commands give snapshots and reports.')
    sub = p.add_subparsers(dest='cmd')  # no command: the live view
    r = sub.add_parser('report', help='summarise the last N hours of local agent logs')
    r.add_argument('--hours', type=float, default=24)
    fmt = r.add_mutually_exclusive_group()
    fmt.add_argument('--json', action='store_true')
    fmt.add_argument('--md', action='store_true', help='Markdown (the default)')
    r.add_argument('--claude-dir', default=os.path.expanduser('~/.claude/projects'))
    r.add_argument('--codex-dir', default=os.path.expanduser('~/.codex/sessions'))
    r.add_argument('--generic', action='append', default=[], metavar='FILE', help='normalised-events JSONL (repeatable)')
    r.add_argument('--show-text', action='store_true', help='include wake snippets and command arguments (off: names only)')
    r.add_argument('--rules', action='append', default=[], metavar='FILE',
                   help='a Claude Code settings file whose deny rules explain refusals (repeatable); '
                        "running agents' --settings files are found automatically")
    s = sub.add_parser('status', help='live health of each agent: working, stuck, waiting on you, ...')
    s.add_argument('--hours', type=float, default=12, help='agents with log activity in this window')
    s.add_argument('--json', action='store_true')
    s.add_argument('--watch', type=float, nargs='?', const=5, metavar='SECONDS', help='refresh until Ctrl-C')
    s.add_argument('--no-herdr', action='store_true', help="don't ask herdr-core (`hc ls --json`) for its view")
    s.add_argument('--claude-dir', default=os.path.expanduser('~/.claude/projects'))
    s.add_argument('--codex-dir', default=os.path.expanduser('~/.codex/sessions'))
    s.add_argument('--show-text', action='store_true', help='show command arguments (off: program names only)')
    t = sub.add_parser('team', help='quiet send: put the hc stand-in on (or off) for your agents')
    t.add_argument('switch', choices=['on', 'off', 'status'])
    w = sub.add_parser('watch', help='no screen: a desktop notification when something needs you or a team stalls')
    w.add_argument('--hours', type=float, default=12)
    args = p.parse_args(argv)
    if args.cmd == 'team':
        return _team(args.switch)
    if args.cmd == 'watch':
        return live.watch(os.path.expanduser('~/.claude/projects'), os.path.expanduser('~/.codex/sessions'), args.hours)
    if args.cmd is None:
        return live.run(os.path.expanduser('~/.claude/projects'), os.path.expanduser('~/.codex/sessions'))
    if args.cmd == 'status':
        return _status(args)

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=args.hours)
    sessions = []
    if os.path.isdir(args.claude_dir):
        extra = [r for f in args.rules for r in rules.load_file(f)]
        sessions += adapters.claude_code(args.claude_dir, since, args.show_text,
                                         cwd_rules=rules.running_settings(), extra_rules=extra)
    if os.path.isdir(args.codex_dir):
        sessions += adapters.codex(args.codex_dir, since, args.show_text)
    for path in args.generic:
        sessions += adapters.generic(path, since, args.show_text)

    r = report.aggregate(sessions, dt.datetime.now(dt.timezone.utc), args.hours)
    meta = {'hours': args.hours, 'since': since.strftime('%Y-%m-%dT%H:%M:%SZ')}
    sys.stdout.write(report.to_json(r, meta) + '\n' if args.json else report.to_md(r, meta, args.show_text))
    return 0


def _status(args):
    while True:
        now = status.now_utc()
        agents = status.collect(args.claude_dir, args.codex_dir, now, args.hours, args.show_text,
                                None if args.no_herdr else status.herdr_terminals(), status.running_cwds())
        out = status.to_json(agents, now) + '\n' if args.json else status.to_text(agents, now)
        if not args.watch:
            sys.stdout.write(out)
            return 0
        sys.stdout.write('\033[H\033[2J' + out)  # clear screen, redraw
        sys.stdout.flush()
        try:
            time.sleep(args.watch)
        except KeyboardInterrupt:
            return 0


def _team(switch):
    if switch == 'on':
        d = quiet.install()
        print(f"""Quiet send is installed.

Agents pick it up when their PATH starts with this folder. Add this line to the script that starts your
agents, before it starts them, then restart them:

    export PATH="{d}:$PATH"

Nothing changes until you also mark a pair quiet in the live view (`wakegraph`, then the pair's number).
Undo with `wakegraph team off`.""")
    elif switch == 'off':
        quiet.uninstall()
        print('Quiet send is off: the hc stand-in is removed. Held notes are still delivered while wakegraph runs.')
    else:
        ps, held = quiet.pairs(), quiet.held()
        print(f"stand-in installed: {'yes' if os.path.exists(os.path.join(quiet.shim_dir(), 'hc')) else 'no'}")
        print(f"deliverer running: {'yes' if quiet.deliverer_alive() else 'no (start wakegraph or wakegraph watch)'}")
        print(f"quiet pairs: {', '.join(p['label'] for p in ps) or 'none'}")
        print(f"notes held: {sum(len(v) for v in held.values())}")
    return 0
