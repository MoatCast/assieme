# Ladder groundwork

Recordings stay on this Mac in `~/.local/share/assieme-dev/replay/`, never in the repository. BatchWise logs are never recorded.
Use `python3` for every command.

| Step | Command |
|---|---|
| Record the inputs once | `python record.py snapshot ~/.local/share/assieme-dev/replay/first.tar` |
| Record hook labels and screens (leave it running while agents work) | `python record.py watch 7200 ~/.local/share/assieme-dev/replay/watch.jsonl` |
| Hook coverage | `python ladder.py coverage …/watch.jsonl` |
| Freeze the labelled test set | `python ladder.py testset …/watch.jsonl …/testset.jsonl` |
| The owner's labelling hour | `python ladder.py label …/watch.jsonl …/hand-labels.jsonl` |

## The labelling hour (about 60 minutes, any time, in pieces)

1. Run the `label` command above. It shows one terminal screen at a time.
2. Press one key and Enter: `w` working, `a` asking you something, `l` at a limit, `e` failed, `i` idle or finished, `s` stuck or looping, `u` unsure. `q` quits; answers are saved after every screen and the next run continues where you stopped.
3. Screens come from Codex agents and from any agent no hook labelled, with repeats removed, in a fixed shuffled order. Stop when the hour is up.

Agents started outside Assieme are not in the core, so their screens cannot be recorded. To label some, start those agents from Assieme for a while, or paste a screen into a file and ask for it to be added.
