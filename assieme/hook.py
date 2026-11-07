"""Status hook for Claude Code and Codex: `python -m assieme.hook <idle|working|blocked>`.

Writes an OSC 7501 status report to the agent's own terminal, which herdr-core reads as the agent's status.
Hooks run without a terminal of their own, so the agent's is found by walking up the process tree.
A hook must never break the agent: every failure is ignored and the exit code is always 0.
"""
import os
import subprocess
import sys

STATES = ('idle', 'working', 'blocked')


def osc(state):
    fields = [f'state={state}'] + (['kind=permission'] if state == 'blocked' else []) + ['app=agent']
    return b'\x1b]7501;' + ':'.join(fields).encode() + b'\x1b\\'


def find_tty(pid, ps=subprocess.run):
    while pid and pid > 1:
        out = ps(['ps', '-o', 'tty=,ppid=', '-p', str(pid)], capture_output=True, text=True).stdout.split()
        if len(out) != 2:
            return None
        tty, ppid = out
        if tty not in ('', '?', '??'):
            return '/dev/' + tty
        pid = int(ppid)
    return None


def main(argv):
    try:
        sys.stdin.read()
        if argv[:1] and argv[0] in STATES:
            tty = find_tty(os.getppid())
            if tty:
                with open(tty, 'wb') as f:
                    f.write(osc(argv[0]))
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
