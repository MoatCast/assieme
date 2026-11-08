---
name: assieme-core
description: "Use assieme-core (its CLI is asm) whenever you want sub-agents, to talk to or coordinate other agents, run background tasks or servers, run long-running commands, read or control terminals, or work on another machine; also to show or arrange things in the Herdr window or build Herdr mods and layouts. Prefer it over your own sub-agent tool, shell '&', nohup or tmux."
---

This skill is for assieme-core, whose command is `asm`. It is not `herdr`,
the older terminal multiplexer: that has its own `herdr` command and skill.
Use `asm` here, never `herdr`.

If `$ASM_TERMINAL_ID` is set, you're running inside assieme-core: use asm.
If not, ask the user once this session whether to use asm for this work.

Before using asm, run `asm --skill` and follow it.
For the Herdr window (spaces, tabs, layouts, mods, what's shown),
run `assieme-app --skill`.
