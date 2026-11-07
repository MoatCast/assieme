# wakegraph

Who woke whom in your multi-agent coding team, what each wake cost, and which commands keep being refused.

wakegraph reads the logs your agents already write (Claude Code, its subagents and Agent Teams, Codex, herdr
`hc` messages, or any tool that emits a simple JSONL) and reports:

- **Who woke whom**: every sender -> receiver pair, the channel, and how many wakes.
- **What each wake cost**: the receiver's input tokens over its calls in that turn (context x calls).
- **Empty wakes**: wakes the receiver answered in at most 2 calls, i.e. it had nothing to do.
- **Refusal loops**: commands a permission rule or sandbox kept refusing.

Local only, no telemetry, standard library only, message text hidden by default. See [DESIGN.md](DESIGN.md).

## Install

Python 3.10+.

```bash
pip install .
```

## Use

```bash
wakegraph report --hours 24          # Markdown (default)
wakegraph report --hours 24 --json
```

### Live health

```bash
wakegraph status            # one line per agent, alerts first
wakegraph status --watch    # refresh every 5 s until Ctrl-C
wakegraph status --json
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
every agent, after its own. wakegraph only suggests; it never edits settings.

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
