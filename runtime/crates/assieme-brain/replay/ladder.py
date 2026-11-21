"""The intelligence ladder's groundwork, from a `record.py watch` recording (WATCH.jsonl).

    ladder.py coverage WATCH.jsonl        how much running time the hooks label exactly, per agent kind
    ladder.py testset WATCH.jsonl OUT     freeze the hook-labelled test set (refuses to overwrite)
    ladder.py label WATCH.jsonl OUT       the owner's hand-labelling: one key per screen, resumable

Today's hooks report three states (working, idle, blocked); the others arrive with ladder step 1.
"""
import collections
import hashlib
import json
import os
import random
import sys

# Seconds between a hook's event and the screen that explains it (research report: a permission prompt is
# announced about 6 s after it appears; Stop and prompt-submit fire at the moment itself).
DELAY = {'blocked': 6, 'idle': 0, 'working': 0}
KEYS = {'w': 'working', 'a': 'asking', 'l': 'at a limit', 'e': 'failed', 'i': 'idle', 's': 'stuck or looping', 'u': 'unsure'}


def rows(path):
    return [json.loads(l) for l in open(path)]


def coverage(path):
    seen = collections.Counter()
    labelled = collections.Counter()
    outside = total = 0
    for r in rows(path):
        outside += max(r['agent_processes'] - len(r['terminals']), 0)
        total += r['agent_processes']
        for t in r['terminals']:
            seen[t['kind']] += 1
            labelled[t['kind']] += bool(t['status'])
    for k in seen:
        print(f'{k}: {labelled[k]}/{seen[k]} samples labelled by a hook = {labelled[k] / seen[k]:.0%}')
    print(f'agent processes started outside Assieme: {outside}/{total} samples = {outside / max(total, 1):.0%} (no hook reaches the brain)')


def events(path):
    """One entry per hook event: the terminal, its state and the screen sampled at the event time minus its delay."""
    rs, out, done = rows(path), [], set()
    for r in rs:
        for t in r['terminals']:
            state = (t['status'] or {}).get('state') if isinstance(t['status'], dict) else t['status']
            key = (t['address'], t['status_ms'])
            if state in DELAY and t['status_ms'] and key not in done:
                done.add(key)
                at = t['status_ms'] / 1000 - DELAY[state]
                best = min(((abs(s['t'] - at), x) for s in rs for x in s['terminals'] if x['address'] == t['address']),
                           key=lambda p: p[0], default=None)
                if best and best[0] <= 5:  # the watcher samples every 5 s
                    out.append({'kind': t['kind'], 'label': state, 'at_ms': t['status_ms'], 'screen': best[1]['screen']})
    return out


def testset(path, out):
    if os.path.exists(out):
        sys.exit(f'{out} is frozen; delete it by hand to rebuild')
    ev = events(path)
    with open(out, 'w') as f:
        for e in ev:
            f.write(json.dumps(e) + '\n')
    open(out + '.sha256', 'w').write(hashlib.sha256(open(out, 'rb').read()).hexdigest() + '\n')
    print(len(ev), 'events frozen,', dict(collections.Counter((e['kind'], e['label']) for e in ev)))


def label(path, out):
    """Codex screens, and any screen a hook did not label: the owner presses one key for each."""
    done = {json.loads(l)['id'] for l in open(out)} if os.path.exists(out) else set()
    screens = {}
    for r in rows(path):
        for t in r['terminals']:
            if t['kind'] == 'codex' or not t['status']:
                screens.setdefault(hashlib.sha1(t['screen'].encode()).hexdigest()[:10], t)  # a repeated screen once
    todo = [i for i in sorted(screens) if i not in done]
    random.Random(1).shuffle(todo)
    print('keys:', ', '.join(f'{k} {v}' for k, v in KEYS.items()), ', q quit (your answers are kept)\n')
    with open(out, 'a') as f:
        for n, i in enumerate(todo, 1):
            print(f'--- {n}/{len(todo)} {screens[i]["kind"]} ---\n' + '\n'.join(screens[i]['screen'].splitlines()[-25:]))
            k = input('state> ').strip().lower()
            if k == 'q':
                break
            if k in KEYS:
                f.write(json.dumps({'id': i, 'kind': screens[i]['kind'], 'label': KEYS[k], 'screen': screens[i]['screen']}) + '\n')
                f.flush()


if __name__ == '__main__':
    {'coverage': coverage, 'testset': testset, 'label': label}[sys.argv[1]](*sys.argv[2:])
