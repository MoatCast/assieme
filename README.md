<p align="center">
  <img src="app/src-tauri/icons/128x128@2x.png" width="128" alt="Assieme">
</p>

<h1 align="center">Assieme</h1>

<p align="center">
  <em>Your coding agents, playing together. You conduct.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/github/v/release/MoatCast/assieme?style=flat-square&color=2747D9&label=release" alt="Release">
  <img src="https://img.shields.io/badge/macOS-Apple%20silicon-2747D9?style=flat-square" alt="macOS on Apple silicon">
  <img src="https://img.shields.io/badge/built%20in-Rust-2747D9?style=flat-square" alt="Built in Rust">
  <img src="https://img.shields.io/badge/agents-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20more-2747D9?style=flat-square" alt="Claude Code, Codex and more">
  <img src="https://img.shields.io/badge/licence-Apache--2.0-2747D9?style=flat-square" alt="Apache-2.0">
</p>

<p align="center">
  <img src="assets/readme/band.png" width="880" alt="The band: a pianist, a violinist, a cellist raising a hand because it needs you, a flautist, the conductor, a trumpeter, a clarinettist, a bassist and a timpanist.">
</p>

Assieme ("together", in Italian) runs a team of coding agents on your Mac: Claude Code, Codex, Antigravity, Gemini
CLI, OpenCode and Cursor. Each agent works on its own branch, in its own live terminal. One window shows who is
working, who has stopped and who needs you. When an agent gets stuck, loops or runs low on memory, Assieme fixes it on
its own and lists what it did. Questions, merges and money stay yours.

<p align="center">
  <img src="assets/readme/needs-you.png" width="880" alt="The Assieme window: on the left, the team's agents grouped as needs you, working and done; on the right, the planner asking whether to ship behind a flag or straight to main, with answer buttons above its live terminal.">
</p>

---

## Why Assieme

- **One window for the whole team.** The agents on the left, grouped by what they need from you. An agent with a
  question goes to the top, with how long it has waited and who it is holding up; answer with one click.
- **It fixes problems on its own.** A stuck agent, a repeated refused command, memory filling up: Assieme acts, checks
  that the fix worked, and pauses the agent and asks you if it did not.
- **You decide what matters.** Agents never push. You review each branch, then merge, with Undo.
- **Fast and light, because it runs on Rust.** Assieme is one Rust program that runs the terminals, watches the agents
  and answers the app. There is no Python or Node runtime to install. It idles at about 35 MB of memory, its agent
  hooks start in under 5 ms, and the app is a 30 MB download.

## Assieme fixes it, then tells you

<p align="center">
  <img src="assets/readme/fixing.png" width="880" alt="builder-3 is stuck: the same test failed 4 times. Assieme asked it to try another way at 14:22 and is checking until 14:32.">
</p>

Each fix shows what Assieme noticed, what it did and when it will check the result. A fix that does not work pauses
the agent and asks you. Every action is in Activity, with Undo where it applies.

## The team at a glance

<p align="center">
  <img src="assets/readme/overview.png" width="880" alt="Team overview: 2 problems fixed today, $4.20 spent of a $10 daily limit, what Assieme did today, and a map of who works with whom.">
</p>

What Assieme did today, what the team has spent against its daily limit, and a map of who works with whom.

## Every terminal, live

<p align="center">
  <img src="assets/readme/terminals.png" width="880" alt="Six live terminals in a grid. The planner's pane has its answer buttons; builder-3's pane shows Assieme checking its fix; the tester's pane has Review changes.">
</p>

Real terminals that keep running when the window is closed. Answer from the pane, or open one full size to type.

## Review, then merge

<p align="center">
  <img src="assets/readme/review.png" width="880" alt="A review of the tester's branch: tests pass 212 of 212, no conflicts with main, a reviewer's note on line 15 with Ask tester to fix it, and Merge into main.">
</p>

The diff, the test results, the conflict check and the reviewer's notes, on one screen. Ask for changes, open a pull
request, or merge. Assieme refuses a merge that would lose work, and Undo puts the branch back.

## A new team, ready to work

<p align="center">
  <img src="assets/readme/new-team.png" width="880" alt="New team, last step: what Assieme does before the agents start, while they work and always, then 6 agents on This Mac and Start 6 agents.">
</p>

Choose a project, a task and a team, from one agent to a planner with builders, a reviewer and a tester. Before you
start, Assieme shows what it does for the team: rules for every agent, fixes while they work, a branch per agent and no
pushes.

## Also in the app

- **Daily limits** per team or per agent: at the limit, Assieme pauses and asks you, or just tells you.
- **Bring your team over** from another tool, with its agents, folders and branches.
- **Remote machines:** run agents on a Linux box or another Mac over SSH, shown in the same window.
- **Previews** of a web app an agent is building, stopped when its work is merged.
- **Your assistant helps, you stay in charge.** Connect Assieme to Claude Code, Codex or the Claude app, then ask
  "What needs me?" from your phone. Every change asks you first.

## Install

**Download** the latest `.dmg` from [Releases](https://github.com/MoatCast/assieme/releases/latest), open it and drag
Assieme to Applications. It is signed and notarised by Apple, and updates itself.

You need an Apple silicon Mac and at least one coding agent, such as Claude Code or Codex. First run finds the agents
you already use and sets up the rest.

<details>
<summary>Build from source</summary>

You need Node, Rust, [just](https://github.com/casey/just), [Zig](https://ziglang.org) 0.16 (for the terminal
library) and `cargo install --locked cargo-zigbuild` (for the Linux builds remote machines use).

```bash
git clone --recurse-submodules https://github.com/MoatCast/assieme.git
cd assieme/runtime && just check     # the runtime and the brain, with their tests
cd ../app && npm install
sh scripts/bundle.sh                 # builds the runtime into the app
npx tauri build --bundles app
open src-tauri/target/release/bundle/macos/Assieme.app
```

Set `ZIG` to your Zig binary if it is not on your `PATH`.

</details>

<details>
<summary>Command line</summary>

For scripts and power users: install it from Settings › General › Command line tools in the app.

```bash
assieme status                  # one line per agent: working, stuck, waiting on you
assieme team status             # the agents of every team
assieme team start <preset> --task "Add retries to the payment client"
assieme team stop <team>
```

</details>

## Safe by default

Assieme never kills an agent without asking, never widens an agent's permissions and never answers a permission
prompt for you. Pushing is blocked by default; an agent's work stays on its own branch until you merge it.
Assieme runs on your Mac with no telemetry, and talks only to your agents, your git remote and any assistant you
connect.

## Licence

Apache-2.0. See [LICENSE](LICENSE). The runtime in `runtime/` is a fork of
[herdr-core](https://github.com/herdrdev/herdr-core), also Apache-2.0. Bug reports and ideas are welcome; see
[CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
