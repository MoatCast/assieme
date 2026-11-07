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
