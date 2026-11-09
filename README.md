# Assieme

**Run a team of coding agents without babysitting it.** Assieme starts mixed teams of Claude Code and Codex agents,
shows you their live terminals, notices when one needs help, and fixes the problem with your say-so.

> [!IMPORTANT]
> **Work in progress: a macOS app is coming soon.** There is no download yet, things will change, and some features
> have only had light real-world use. You can try it today by building from source on an Apple silicon Mac.

## What sets it apart

Most agent coordinators give you a place to start agents and see what they're doing. You still have to notice the
problem, work out why it happened and decide what to do. Assieme is built around that part of the job.

**It tells you why a team has stopped, not just that it has.** Assieme follows the chain of who is waiting on whom
down to the cause:

```
!! Team stalled for 10 min: 2 agents wait on lead (builder, reviewer); lead waits on you (asked 10 min ago)
```

**Every alert comes with a fix.** Each one says what is wrong, why it matters and the one action that fixes it. Fixes
start as a question. You can press one button, or switch that kind of fix to automatic once you trust it. Every action
is logged with its reason, and those that can be undone have an Undo button.

| Problem | How Assieme spots it | The fix |
|---|---|---|
| An agent is stuck | No progress for 15 minutes, the same command repeating, or the agent says so | Ask it to try another way |
| A command keeps being refused | It names the rule that refused it and finds where the rule came from | Tell the agent to stop retrying, and suggest you a narrower rule |
| An agent keeps hitting the same failure | The same failure keeps coming back | Tell it to change approach |
| Memory is filling up | It measures how fast the context grows | Free up memory at the next pause |
| Tests are wasting time | The full suite repeats, or heavy jobs overlap | Ask the agent to run fewer tests, or to wait its turn |
| A decision is yours | A question, approval or permission prompt | Your queue, longest-blocking first, with suggested answers |

**It shows what agents cost each other.** Every message between agents can wake one that re-reads its whole context.
Assieme traces who woke whom, what each wake cost and which ones did no work, then names the pairs most worth cutting.
*Quiet send* holds routine notes and hands them over with the receiver's next turn, while questions and urgent notes
still go at once.

**Agents report for themselves.** Agents inside a team use Assieme's MCP tools to message each other and to say when
they are stuck or need a decision, so status comes from the agent as well as from its logs.

**A new team starts the way a tuned one ends.** Teams get built-in practices learned from running real agent teams:
one coordinator for outside-world work, reviewers that only read, at most two repair attempts, clear stop rules and a
fixed handoff format. Optional groups can be switched off, while the basics stay on.

**Your assistant can help, but you stay in charge.** Connect Claude Code, Codex or the Claude app with one button.
They can read team status and design a team with you. Anything that changes something asks you first, and a new team
only ever starts when you click Start.

## What you can do today

- **Create a team:** pick a project and mix Claude Code and Codex agents, each with its own role, model and thinking
  effort. Presets and *Help me choose* cover the common shapes.
- **Watch and type:** see a live grid of terminals that keep running when the window closes, and open any one full size
  to type into it.
- **See the team:** a map of who wakes whom, what needs you, what's ready to review, and today's token use.
- **Review each agent's work:** every agent that edits gets its own git worktree and branch. One screen has the diff,
  test results, conflict and file checks, and reviewer notes, with Ask for changes, Open pull request, Merge, Discard,
  and Undo after a merge.
- **Answer from anywhere:** a menu bar badge counts what needs you, with answer buttons in its panel. Mac notifications
  carry the same answers.
- **Keep your existing agents:** Assieme finds agents already running in plain terminals or herdr and shows them, with
  fewer actions available.
- **Check it all afterwards:** Activity lists everything Assieme did and why, with Undo where it can be undone.

**Coming next:** the macOS download, a task board, budgets with "pause and ask me", remote machines over SSH, a shared
team brief, scheduled runs, a live preview per agent, more agent CLIs, and dark mode. A Rust runtime, forked from
herdr-core, is in `runtime/` and tested side by side with the original, but the app doesn't use it yet.

## Safe by default

- It never kills an agent without asking, never widens permission rules and never answers a permission prompt for you.
- Fixes ask first until you choose otherwise, and an agent's work stays on its own branch until you merge it.
- It runs on your Mac with no telemetry. It talks only to what you use: your agents, your git remote when you push or
  open a pull request, and any assistant you connect.
- Reports and the terminal views hide message text unless you pass `--show-text`. Mac notifications can show an agent's
  question, including on your lock screen, so you can answer it.

## Try it from source

You need an Apple silicon Mac, [uv](https://docs.astral.sh/uv/), Node, Rust, and Claude Code or Codex.

```bash
git clone https://github.com/MoatCast/assieme.git
cd assieme/app
npm install
sh scripts/bundle.sh
npx tauri build --bundles app
open src-tauri/target/release/bundle/macos/Assieme.app
```

`bundle.sh` downloads a pinned Python and herdr-core and checks them against their published checksums. The app's First
run screen offers to start its background service. The app isn't notarised yet, so macOS may ask you to allow it.

## Command line

The app is the main way in, and everything you need day to day is there. For power users and scripts, install the
commands with `uv tool install .` from this folder (Python 3.11+). Starting teams from here also needs herdr-core's `hc`
on your PATH.

```bash
assieme                                        # live view: what needs you, alerts, one-key fixes
assieme status [--watch] [--json]              # one line per agent, alerts first
assieme report --hours 24 [--json]             # who woke whom, what it cost, what was refused
assieme watch                                  # no screen: just the notifications
assieme team start ~/presets/checkout --task "Add retries to the payment client"
assieme team status checkout
assieme team stop checkout
assieme quiet on|off|status                    # quiet send for agents using `hc send`
assieme mcp                                    # the MCP server, for clients that start it themselves
```

A preset is a folder with a `team.toml` (the team's name, project and roles) and one Markdown brief per role. Each role
picks Claude Code or Codex, a model, a parent, and `checkout = "branch"` or `"read-only"`.

`assieme report` also reads other tools through `--generic FILE`, as JSONL like this:

```json
{"ts": "2026-11-07T10:00:00Z", "agent": "builder", "type": "wake", "from": "coordinator"}
{"ts": "2026-11-07T10:00:05Z", "agent": "builder", "type": "call", "input_tokens": 41000, "output_tokens": 300}
{"ts": "2026-11-07T10:00:09Z", "agent": "builder", "type": "refusal", "command": "git push"}
```

## How it's built

| Layer | Built with |
|---|---|
| **App** | Tauri 2, Rust, React 19, TypeScript, xterm.js |
| **Brain** | A Python background service (standard library), with detection, fixes and team rules |
| **MCP server** | A separate process using the official MCP Python SDK |
| **Runtime** | herdr-core today, with Assieme's own fork in `runtime/` for later |

To build the Rust runtime, first run `git submodule update --init` to fetch Ghostty's terminal library; you'll also
need Zig 0.16. See [DESIGN.md](DESIGN.md) and [FORK-PLAN.md](FORK-PLAN.md) for more detail.

## Tests

```bash
uv run --with pytest python -m pytest
cd app && npx vitest run && npx tsc --noEmit
cd app/src-tauri && cargo test
```

Tests use synthetic logs only. No real logs are read or committed.

## Licence

Apache-2.0. See [LICENSE](LICENSE). The runtime in `runtime/` is a fork of
[herdr-core](https://github.com/herdrdev/herdr-core), also Apache-2.0, and keeps its own notices. Assieme isn't
affiliated with or endorsed by herdr.
