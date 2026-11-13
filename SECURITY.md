# Security

Assieme starts coding agents on your computer and can send them input, so security reports matter even at this early
stage.

**Please don't report a vulnerability in a public issue.** Use
[GitHub's private reporting](https://github.com/MoatCast/assieme/security/advisories/new) instead, and include the steps
to reproduce it and what an attacker could do.

Assieme is a work in progress with no stable release yet, so fixes go to the `main` branch only.

## What to keep in mind

- Agents run as your user, with your user's access. Assieme's rules are instructions to the agents and sandbox settings
  for read-only roles, not a security boundary.
- While a team's Safe by default practice is on, every agent Assieme starts gets a git setting that makes `git push`
  fail. This is a guard rail, not a security boundary: it doesn't stop deploy tools, GitHub API calls (`gh` calling the
  API, for instance), or an agent that deliberately clears the setting. It also doesn't apply when the repository or
  your git settings give a remote its own push address.
- Assieme checks sign-in by running each agent's status command with its output discarded. It never reads or stores
  agent tokens.
- Asking you first happens in the MCP server. A program already running as your user, including an agent with shell
  access, could reach Assieme's local service directly.
- A scheduled run starts its team at the set time without asking again, with the agents, models and practices you
  chose when you set it up. A live preview runs the project's own dev script, after asking you, so it runs that
  project's code on your Mac.
- Agents' output, and anything they read, can be untrusted. Treat what they say with the same care as any input from the
  web.
