"""assieme            live view (the default)
assieme status     one snapshot of team health
assieme watch      notifications only, no screen
assieme report     who woke whom, what it cost, what was refused
assieme team       start, list and stop a team from a preset
assieme quiet      quiet send: the hc stand-in on or off
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import traceback

from . import activity, adapters, answers, connect, detect, live, machine, mcptools, menubar, newteam, preset, process, quiet, report, review, rules, runtime, serve, service, status, team, teammap, worktree


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['shim']:  # the hc stand-in: hc's own flags must reach it untouched
        return quiet.shim_main(argv[1:])
    p = argparse.ArgumentParser(prog='assieme', description='Run with no command for the live view: team health, '
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
    r.add_argument('--metrics', metavar='FILE', help='paid-compute readings, JSONL (default: boxes.jsonl in the state folder)')
    r.add_argument('--prs', action='store_true', help='also ask `gh` about merged pull requests (goes over the network)')
    s = sub.add_parser('status', help='live health of each agent: working, stuck, waiting on you, ...')
    s.add_argument('--hours', type=float, default=12, help='agents with log activity in this window')
    s.add_argument('--json', action='store_true')
    s.add_argument('--watch', type=float, nargs='?', const=5, metavar='SECONDS', help='refresh until Ctrl-C')
    s.add_argument('--no-herdr', action='store_true', help="don't ask herdr-core (`hc ls --json`) for its view")
    s.add_argument('--claude-dir', default=os.path.expanduser('~/.claude/projects'))
    s.add_argument('--codex-dir', default=os.path.expanduser('~/.codex/sessions'))
    s.add_argument('--show-text', action='store_true', help='show command arguments (off: program names only)')
    q = sub.add_parser('quiet', help='quiet send: put the hc stand-in on (or off) for your agents')
    q.add_argument('switch', choices=['on', 'off', 'status'])
    t = sub.add_parser('team', help='start, list and stop a team of agents from a preset')
    tsub = t.add_subparsers(dest='team_cmd', required=True)
    ts = tsub.add_parser('start', help='start every role in a preset folder (team.toml plus briefs)')
    ts.add_argument('preset')
    ts.add_argument('--task', help='sent to every agent after its brief')
    ts.add_argument('--only', metavar='ROLE', help='start just this role (for example to retry it)')
    tl = tsub.add_parser('status', help='the agents of one team, or of every team')
    tl.add_argument('name', nargs='?')
    tl.add_argument('--json', action='store_true')
    tk = tsub.add_parser('stop', help='stop every running agent of a team')
    tk.add_argument('name')
    sv = sub.add_parser('service', help='run the brain in the background at login (launchd)')
    sv.add_argument('switch', choices=['install', 'uninstall', 'status'])
    sub.add_parser('mcp', help='the MCP server for assistants and for agents in a team (stdio)')
    sub.add_parser('serve', help='the brain as a background service (the app talks to it); runs `watch` too')
    w = sub.add_parser('watch', help='no screen: a desktop notification when something needs you or a team stalls')
    w.add_argument('--hours', type=float, default=12)
    args = p.parse_args(argv)
    if args.cmd == 'service':
        return _service(args.switch)
    if args.cmd == 'serve':
        return _serve()
    if args.cmd == 'mcp':
        from . import mcpserver  # only this process loads the MCP SDK: the brain stays standard library
        return mcpserver.main()
    if args.cmd == 'quiet':
        return _quiet(args.switch)
    if args.cmd == 'team':
        return _team(args)
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

    now = dt.datetime.now(dt.timezone.utc)
    r = report.aggregate(sessions, now, args.hours, adapters.box_metrics(args.metrics or adapters.box_path(), since),
                         machine.read(since))
    weight = process.report({s.cwd for s in sessions}, since, now, process.run_gh if args.prs else None)
    r['process'] = {'items': [{k: v for k, v in i.items() if k != 'sha'} for i in weight['items']],
                    'gates': {**detect.gate_hours(sessions), **weight['gates']}}
    gate = process.gate_finding(detect.gate_hours(sessions), weight['gates'])
    r['findings'] += weight['findings'] + ([gate] if gate else [])
    r['detectors'].append(weight['detector'])
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


def _service(switch):
    if switch == 'install':
        print(f'The brain runs in the background now and at every login ({service.install()}).')
    elif switch == 'uninstall':
        service.uninstall()
        print('The background brain is stopped and removed.')
    else:
        up = service.running()
        print('running' if up else 'not running')
        return 0 if up else 1
    return 0


def _serve():
    claude, codex = os.path.expanduser('~/.claude/projects'), os.path.expanduser('~/.codex/sessions')
    rt = runtime.Runtime()
    starter = newteam.Starter(rt, newteam.teams_dir())
    suggested = answers.Answers()

    brain = teammap.Brain(claude, codex, rt, starter, suggested)
    reviews = review.Review(rt, newteam.teams_dir())
    activity.register_merge(reviews)
    tools = mcptools.Tools(brain, starter)
    menu = menubar.Menubar(brain, reviews)

    def tick():
        for step in (lambda: live.watch_once(claude, codex, announcing=not menu.app_watching()), brain.tick,
                     lambda: machine.tick(status.now_utc())):
            try:
                step()
            except Exception:  # one failing step must not stop notifications, delivery or the others
                traceback.print_exc()
    serve.serve(serve.brain_socket_path(),
                {'brain.snapshot': lambda: serve.snapshot(claude, codex, status.now_utc(), rt,
                                                          starting=starter.records()) | {'draft': tools.draft_next()},
                 'project.check': newteam.project_check,
                 'team.presets': lambda: newteam.presets(),
                 'team.start': starter.start,
                 'team.suggest': newteam.suggest,
                 **brain.handlers(),
                 **reviews.handlers(),
                 **tools.handlers(),
                 **connect.handlers(),
                 **menu.handlers()},
                tick=tick)
    return 0


def _ask(question):
    try:
        return input(f'{question} [y/N] ').strip().lower() in ('y', 'yes')
    except EOFError:
        return False


def _team(args):
    rt = runtime.Runtime()
    try:
        if args.team_cmd == 'stop':
            print(f'stopped {team.stop(rt, args.name)} agents')
        elif args.team_cmd == 'status':
            rows = team.status(rt, args.name)
            if args.json:
                print(json.dumps(rows, indent=2))
            elif not rows:
                print('no Assieme teams running' + (f' named {args.name}' if args.name else ''))
            for r in rows if not args.json else []:
                print(f"{r['team']:<20} {r['role'] or '?':<16} {r['address']:<6} {r['state']:<9} {r['status'] or '-'}")
        else:
            tm = preset.load(args.preset)
            for s in team.start(tm, args.task, rt, _ask, only=args.only):
                print(f'{s.role:<16} {s.state}' + (f'  {s.address}' if s.address else ''))
                if s.note:
                    print('\n'.join('    ' + line for line in s.note.splitlines()))
                if s.state == 'needs-you':
                    print(f'    then: assieme team start {args.preset}')
                if s.state in ('exited', 'not-reporting'):
                    print(f'    retry: assieme team start {args.preset} --only {s.role}')
    except (preset.PresetError, worktree.WorktreeError, team.TeamError, runtime.RuntimeError_) as e:
        hint = getattr(e, 'hint', '')
        print(f'assieme: {e}' + (f' ({hint})' if hint else ''), file=sys.stderr)
        return 1
    return 0


def _quiet(switch):
    if switch == 'on':
        d = quiet.install()
        print(f"""Quiet send is installed.

Agents pick it up when their PATH starts with this folder. Add this line to the script that starts your
agents, before it starts them, then restart them:

    export PATH="{d}:$PATH"

Nothing changes until you also mark a pair quiet in the live view (`assieme`, then the pair's number).
Undo with `assieme quiet off`.""")
    elif switch == 'off':
        quiet.uninstall()
        print('Quiet send is off: the hc stand-in is removed. Held notes are still delivered while assieme runs.')
    else:
        ps, held = quiet.pairs(), quiet.held()
        print(f"stand-in installed: {'yes' if os.path.exists(os.path.join(quiet.shim_dir(), 'hc')) else 'no'}")
        print(f"deliverer running: {'yes' if quiet.deliverer_alive() else 'no (start assieme or assieme watch)'}")
        print(f"quiet pairs: {', '.join(p['label'] for p in ps) or 'none'}")
        print(f"notes held: {sum(len(v) for v in held.values())}")
    return 0
