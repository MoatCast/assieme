"""assieme status     one snapshot of team health
assieme team       start, list and stop a team from a preset
assieme quiet      quiet send: the asm stand-in on or off
"""
import argparse
import json
import os
import sys
import time
import traceback

from . import scripts as scripts_mod
from . import actions, activity, answers, bridge, bringover, connect, live, machine, mcptools, menubar, newteam, preset, preview, quiet, remote, review, runtime, schedule, serve, settings, setup, status, tasks, team, teammap, worktree


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['shim']:  # the asm stand-in: asm's own flags must reach it untouched
        return quiet.shim_main(argv[1:])
    if argv[:1] == ['call'] and len(argv) == 2:  # the Mac's brain asking this machine's, over the core's line
        return remote.call_main(argv[1])
    p = argparse.ArgumentParser(prog='assieme', description='The brain behind the Assieme app. Commands give snapshots '
                                'and start the background service.')
    sub = p.add_subparsers(dest='cmd')
    s = sub.add_parser('status', help='live health of each agent: working, stuck, waiting on you, ...')
    s.add_argument('--hours', type=float, default=12, help='agents with log activity in this window')
    s.add_argument('--json', action='store_true')
    s.add_argument('--watch', type=float, nargs='?', const=5, metavar='SECONDS', help='refresh until Ctrl-C')
    s.add_argument('--claude-dir', default=os.path.expanduser('~/.claude/projects'))
    s.add_argument('--codex-dir', default=os.path.expanduser('~/.codex/sessions'))
    s.add_argument('--show-text', action='store_true', help='show command arguments (off: program names only)')
    q = sub.add_parser('quiet', help='quiet send: put the asm stand-in on (or off) for your agents')
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
    sub.add_parser('mcp', help='the MCP server for assistants and for agents in a team (stdio)')
    sub.add_parser('serve', help='the brain as a background service (the app talks to it)')
    args = p.parse_args(argv)
    if args.cmd == 'serve':
        return _serve()
    if args.cmd == 'mcp':
        from . import mcpserver  # only this process loads the MCP SDK: the brain stays standard library
        return mcpserver.main()
    if args.cmd == 'quiet':
        return _quiet(args.switch)
    if args.cmd == 'team':
        return _team(args)
    if args.cmd is None:
        p.print_help()
        return 0
    return _status(args)


def _status(args):
    while True:
        now = status.now_utc()
        agents = status.collect(args.claude_dir, args.codex_dir, now, args.hours, args.show_text, status.running_cwds())
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


def _notify_and_deliver(claude, codex, announcing, hours=12):
    """One round of the serve tick: notifications (plain ones only when the app isn't showing its own) and held quiet notes."""
    now = status.now_utc()
    agents = status.collect(claude, codex, now, hours, running=status.running_cwds())
    st = settings.load()
    paused = status.parse_ts(st['notify_paused_until'])
    if announcing and not (paused and paused > now):
        live.announce(status.owner_queue(agents, now), status.stalls(agents, now), now, switches=st['notify'])
    quiet.deliver(agents, on_batch=lambda notes, why: live.record_delivery(notes, why, now),
                  on_give_up=lambda notes: live.record_given_up(notes, now))


def _prune_tickets(rt):
    try:
        bridge.prune({r['address'] for r in team.status(rt) if r['state'] == 'live'})
    except runtime.RuntimeError_:
        pass  # no core: nothing is running, and nothing is pruned until one answers


def _serve():
    claude, codex = os.path.expanduser('~/.claude/projects'), os.path.expanduser('~/.codex/sessions')
    rt = runtime.Runtime()
    starter = newteam.Starter(rt, newteam.teams_dir(), scheduled=lambda: {x['team'] for x in schedule._load()})
    suggested = answers.Answers()

    brain = teammap.Brain(claude, codex, rt, starter, suggested)
    reviews = review.Review(rt, newteam.teams_dir())
    activity.register_merge(reviews)
    previews = preview.Previews(reviews)
    previews.stop_left_behind()
    reviews.on_archive = previews.stop
    menu = menubar.Menubar(brain, reviews)
    sched = schedule.Scheduler(rt, starter.start, starter.records,
                               announce=lambda title, body: None if menu.app_watching() else live.notify(title, body))
    tools = mcptools.Tools(brain, starter, scheduler=sched)
    board = tasks.Board(brain, reviews, tools._me)

    remotes = remote.Remotes(rt)

    def start_team(project, task, roles, practices, name=None, scripts=None, allow_scripts=(), cards=None, origin=None):
        """`scripts`, `allow_scripts`, `cards` and `origin` ({tool, label}) come with a team brought over (step 30)."""
        skipped = []
        if origin:
            roles, skipped = bringover.not_running(roles, origin['tool'])
        got = starter.start(project, task, roles, practices, name=name, scripts=scripts,
                            allow_scripts=allow_scripts)  # a name: chosen by the Mac's brain
        tasks.fresh(got['team'], task)  # a new team, a new board; restarting a saved team keeps its cards
        if origin:
            bringover.started(got['team'], project, roles, cards or [], scripts or {}, origin, previews.remember)
        return got | ({'skipped': skipped} if skipped else {})

    def tick():
        for step in (lambda: _notify_and_deliver(claude, codex, not menu.app_watching()),
                     lambda: brain.tick(announce=None if menu.app_watching() else live.notify),
                     lambda: machine.tick(status.now_utc()),
                     sched.tick,
                     previews.sweep,
                     lambda: remotes.tick(watching=menu.app_watching()),
                     lambda: _prune_tickets(rt)):
            try:
                step()
            except Exception:  # one failing step must not stop notifications, delivery or the others
                traceback.print_exc()
    table = remotes.wrap({'brain.snapshot': lambda: serve.snapshot(claude, codex, status.now_utc(), rt,
                                                          starting=starter.records()) | {'draft': tools.draft_next(),
                                                                                       'scheduled_teams': sched.run_teams()},
                 'project.check': newteam.project_check,
                 'team.presets': lambda: newteam.presets(),
                 'team.start': start_team,
                 'team.allow_script': scripts_mod.allow,
                 'bringover.find': lambda folder=None: bringover.find(folder=folder),
                 'bringover.workspaces': lambda id: bringover.workspaces(id),
                 'bringover.import': tools.bringover_import,
                 'team.suggest': newteam.suggest,
                 'team.improve_brief': newteam.improve_brief,
                 'team.rule_draft': newteam.rule_draft,
                 **brain.handlers(),
                 **reviews.handlers(),
                 **tools.handlers(),
                 **board.handlers(),
                 **sched.handlers(),
                 **connect.handlers(),
                 **menu.handlers(),
                 **setup.handlers(rt),
                 **previews.handlers(),
                 **remote.remote_handlers()})
    table |= actions.handlers(table)  # step 18a: assistants reach the same actions, routed the same way
    serve.serve(serve.brain_socket_path(), table, tick=tick)
    return 0


LOCAL_HANDLERS = ('brain.snapshot', 'project.check', 'team.presets', 'team.start', 'team.allow_script', 'bringover.find',
                  'bringover.workspaces', 'bringover.import', 'team.suggest', 'team.improve_brief', 'team.rule_draft')


def handler_names():
    """Every action name the brain serves (the table `serve` gets), without starting it: for the catalogue's test."""
    from unittest import mock
    me = mock.MagicMock()
    tables = (teammap.Brain.handlers(me), review.Review.handlers(me), mcptools.Tools.handlers(me),
              tasks.Board.handlers(me), schedule.Scheduler.handlers(me), connect.handlers(),
              menubar.Menubar.handlers(me), setup.handlers(me), preview.Previews.handlers(me),
              remote.remote_handlers(), remote.Remotes.handlers(me))
    return sorted({*LOCAL_HANDLERS, *(n for t in tables for n in t), 'assistant.actions', 'assistant.do'})


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
                    print(f'    open it: ASM_SESSION={getattr(rt, "session", "assieme")} ASM_SOCKET={rt.sock} asm attach {s.address}')
                    print(f'    then: assieme team start {args.preset}')
                if s.state in ('exited', 'not-reporting'):
                    print(f'    retry: assieme team start {args.preset} --only {s.role}')
    except (preset.PresetError, worktree.WorktreeError, team.TeamError, runtime.RuntimeError_) as e:
        hint = getattr(e, 'hint', '')
        print(f'assieme: {e}' + (f' ({hint})' if hint else ''), file=sys.stderr)
        if getattr(e, 'detail', ''):
            print(e.detail, file=sys.stderr)
        return 1
    return 0


def _quiet(switch):
    if switch == 'on':
        d = quiet.install()
        print(f"""Quiet send is installed.

Agents pick it up when their PATH starts with this folder. Add this line to the script that starts your
agents, before it starts them, then restart them:

    export PATH="{d}:$PATH"

Nothing changes until you also mark a pair quiet in the Assieme app.
Undo with `assieme quiet off`.""")
    elif switch == 'off':
        quiet.uninstall()
        print('Quiet send is off: the asm stand-in is removed. Held notes are still delivered while assieme runs.')
    else:
        ps, held = quiet.pairs(), quiet.held()
        print(f"stand-in installed: {'yes' if os.path.exists(os.path.join(quiet.shim_dir(), 'asm')) else 'no'}")
        print(f"deliverer running: {'yes' if quiet.deliverer_alive() else 'no (start the Assieme app)'}")
        print(f"quiet pairs: {', '.join(p['label'] for p in ps) or 'none'}")
        print(f"notes held: {sum(len(v) for v in held.values())}")
    return 0
