# Assieme

Keeps a team of coding agents moving, so you don't have to keep asking "Status".

Assieme reads the logs your agents already write (Claude Code with its subagents and Agent Teams, Codex,
herdr `hc` messages) and tells you, in plain words:

- **what needs you**: an agent waiting on your answer, a stuck agent, a whole team stalled and who it waits on;
- **what it costs**: who woke whom, and how many tokens went on wake-ups that did no work;
- **what keeps being refused**, which rule did it, and a narrower rule to use instead.

Every alert comes with a fix. When Assieme can carry the fix out itself (today: nudging a stuck agent through
herdr), it takes one key and a confirmation. Nothing acts on its own.

Local only, no telemetry, no config, standard library only, message text hidden by default.
See [DESIGN.md](DESIGN.md) and [ROADMAP.md](ROADMAP.md).

## Start

Python 3.10+. Install it once with [uv](https://docs.astral.sh/uv/), giving the path to this folder:

```bash
uv tool install --editable ~/path/to/assieme
```

Then run `assieme` from anywhere. It finds Claude Code, Codex and herdr by itself. `--editable` means changes
to this folder take effect without reinstalling. Remove it with `uv tool uninstall assieme`. (`pipx install
--editable <path>` works too.)

## The live view

`assieme` with no command opens a view that refreshes every 5 seconds:

```
assieme  14:02  1 stuck, 1 waiting on you, 6 idle, 3 exited

!! Team stalled for 10 min: 2 agents wait on steward (builder, coord); steward waits on you (asked 10 min ago)
Needs you (1):
  [1] steward: a permission prompt, waiting 10 min; holds up builder, coord  -> answer it in hc attach t2
Other alerts:
  [2] builder is stuck: silent 18 min, last Bash: pytest  -> press 2 to nudge

Last 24 h: wakes that did no work cost 414M tokens (30% of input). Cut first: builder -> coord (82M).
```

**Needs you** is your queue: only decisions that are yours, longest-blocking first. That covers a question, a plan
to approve, a Codex approval, a permission prompt herdr reports, or a reply that ended with a question in a turn
you started. Each item shows how long it has waited and which agents it holds up. A new item, or a new team
stall, pops up one desktop notification (macOS Notification Centre, or `notify-send` on Linux). Notifications
carry names only, never message text, and items older than 2 hours never pop up.

**Other alerts** are fixes Assieme can carry out itself, through `hc send`:

| Fix | When | What it sends |
|---|---|---|
| nudge | an agent is stuck, a message never started a turn, or an idle agent holds up a stalled team | a short note on what looks wrong and what to do |
| tell | the same command was refused twice in 30 minutes | which rule refused it, and to stop retrying (the alert also shows the narrower rule for you) |
| compact | a team agent is idle with 150K+ tokens of context, which every wake re-reads | `/compact` |

| make it quiet | a pair is one of the costliest senders of wakes that did no work | nothing: it turns on quiet send for that pair |

A fix you carried out stays hidden for 15 minutes.

### Quiet send

Most wakes between agents are routine notes ("suite done") that the receiver only has to read. On a quiet pair,
those notes are held and handed over as one batch while the receiver is busy anyway, so they join its current
turn instead of starting a new one. If the receiver stays idle for 20 minutes, the batch goes anyway: one wake
instead of many. Questions (ending in `?`) and notes starting with `URGENT` or `!` always go at once, and an agent
can force any note with `hc send --now`. A failed delivery stays held, nothing is dropped.

Agents keep typing `hc send`. Turn it on once:

```bash
assieme quiet on     # installs the hc stand-in and prints the one PATH line for your agents' start script
assieme quiet status # stand-in installed? deliverer running? which pairs are quiet, how many notes held
assieme quiet off    # removes it
```

Then mark pairs quiet in the live view. Held notes are delivered by `assieme` or `assieme watch`; when
neither is running, the stand-in passes every note straight through.

Press a number to act on an alert. Assieme shows the exact message it will send and waits for `y`. Every
action is logged to `~/.local/state/assieme/actions.jsonl` and listed in the view. `q` quits.
Piped into another program, it prints one snapshot instead.

## Commands

```bash
assieme status            # one snapshot: one line per agent, alerts first
assieme status --watch    # the same, refreshing every 5 s
assieme watch             # no screen: just the notifications, for a background tab
assieme report --hours 24 # who woke whom, what it cost, what was refused (Markdown; --json for data)
```

### Start a team

A preset is a folder with a `team.toml` (the team's name, project and roles: Claude Code or Codex, model, parent,
`checkout = "branch"` or `"read-only"`) and one Markdown brief per role. Each agent gets its own git worktree under
`<project>/.assieme/worktrees/<role>`, runs in its own `hc` session (`HC_SESSION=assieme`, separate from herdr),
and gets its brief once its first turn has ended. Needs `hc` (herdr-core) installed.

```bash
assieme team start ~/presets/checkout --task "Add retries to the payment client"
assieme team status checkout    # one line per agent: terminal, live or finished, idle/working/blocked
assieme team stop checkout
```

Assieme asks once per project before answering an agent's "trust this folder?" prompt. Codex asks you to review
Assieme's status hooks once; `assieme team start` prints the command to open that agent and answer it, and
running `assieme team start` again afterwards sends that agent its brief.

### Live health

```bash
assieme status            # one line per agent, alerts first
assieme status --watch    # refresh every 5 s until Ctrl-C
assieme status --json
```

Each agent active in the last 12 hours (`--hours`) gets one state, read from its own logs:

| State | Means |
|---|---|
| stuck | working, but no new log row for 15 min, the same command 4 times in a row, or a subagent gone silent |
| waiting on you | it asked a question, Codex wants approval, or herdr reports a permission prompt |
| idle, work waiting | another agent sent it a message (`hc send`) that started no turn |
| waiting on subagents | its turn ended but subagents are still running |
| working | in a turn, with the running tool named |
| idle | turn over |
| exited | its session is closed: no Claude Code or Codex process runs in its folder |

**Stall alarm.** Agents that message each other form a team. When nobody on a team is making progress but work
is open, the top line says so and traces the chain of who waits on whom, for example:

```
!! Team stalled for 10 min: 2 agents wait on steward (builder, coord); steward waits on you (asked 10 min ago)
```

An agent waits on another when its last message went there and nothing came back, or when it runs `hc wait` on
it. A report that the receiver read and finished with does not count as waiting. Chains end at a person, a stuck
agent, a message that never started a turn, or two agents waiting on each other.

When herdr-core is installed, the HERDR column shows herdr's own status for the same terminal (`unreported`
when the agent never told herdr). Use `--no-herdr` to skip it. Command arguments are hidden unless `--show-text`.

### Report options

The report opens with **Send less**: what wakes that did no work cost in total, and the agent pairs most worth
cutting (each worth at least 1% of input), for example:

```
1. builder -> coord: 198 of 252 wakes did no work and cost 82.0M tokens (6% of all input); batch these notes or stop sending them
```

The wake table shows each pair's wakes per hour over the window and in the last hour, and flags a pair that just
got noisy (10+ wakes in the last hour and at least 3x its usual rate).

**Refused commands** say where each refusal came from and what to do about it: a named deny rule and the file it
lives in, the auto-mode classifier and its reason, a built-in safety check, or the Codex sandbox. A rule that
matched a word in the command rather than the program being run is called out with a narrower rule to use
instead, for example:

```
steward: 'Bash: hc' refused 4 times by deny rule `Bash(*cloud-launch*)` (build/herdr/steward.json); the rule matched
a word in the command, not the program run (hc); deny running it instead: `Bash(cloud-launch:*)`
```

Rules are read from each agent's `.claude/settings*.json`, `~/.claude/settings.json`, and the `--settings` file of
any running Claude Code agent. For past runs, pass role files with `--rules FILE` (repeatable); those are tried for
every agent, after its own. Assieme only suggests; it never edits settings.

Options: `--claude-dir` (default `~/.claude/projects`), `--codex-dir` (default `~/.codex/sessions`),
`--generic FILE` (repeatable), `--show-text` (adds wake snippets and full command prefixes; off by default).

Flags are raised for an agent-to-agent edge with more than 20 empty wakes, a command refused 3+ times by one
agent, or a context above 250K tokens.

### Generic events

Any tool can be added by writing JSONL like:

```json
{"ts": "2026-11-07T10:00:00Z", "agent": "builder", "type": "wake", "from": "coordinator"}
{"ts": "2026-11-07T10:00:05Z", "agent": "builder", "type": "call", "input_tokens": 41000, "output_tokens": 300}
{"ts": "2026-11-07T10:00:09Z", "agent": "builder", "type": "refusal", "command": "git push"}
```

## Test

```bash
python -m unittest
```

Tests build synthetic logs in a temporary directory; no real logs are read.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
