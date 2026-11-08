# assieme-core (`asm`)

Assieme's own runtime: it runs Claude Code and Codex agents in terminals that stay alive after the window closes. A
one-time fork of herdr-core; see [NOTICE](NOTICE), [the fork plan](../FORK-PLAN.md) and
[the spec](../docs/superpowers/specs/2026-11-08-own-runtime-design.md).

People never install this. The app ships the binary.

## Build

Needs Rust and Zig 0.16.0. Zig builds the terminal library from `vendor/ghostty`; users never need it.

```bash
git submodule update --init --depth 1 runtime/vendor/ghostty
```

```bash
cd runtime && ZIG=/path/to/zig-0.16.0/zig cargo build
```

## Test

```bash
cd runtime && ZIG=/path/to/zig-0.16.0/zig cargo test --locked
```

To type-check without Zig (nothing links):

```bash
cd runtime && ZIG=$PWD/scripts/zig-stub.sh cargo check --all-targets
```

The comparison with real `hc` is in `tests/test_runtime_side_by_side.py` at the repo root.

## What it adds to herdr-core

| Addition | Call | State |
|---|---|---|
| Delivery control | `terminal.send` with `deliver: "next_turn"`, reported by `delivery` events | Works; tested with a fake agent, not a real one |
| Pushed status | `terminal.status.set` with a `source` | Works; tested with a fake agent |
| Permission by intent (A6) | `terminal.run` with `deny_programs` | macOS only; breaks agents that apply their own sandbox. See the spec |

Docs: [commands](docs/commands.md), [machines](docs/machines.md), [templates](docs/templates.md),
[security](docs/security.md), [API](schema/README.md).

## Upstream

No routine merges. Read their changelog weekly and hand-pick fixes in the holder, relay, stitch and input code.
