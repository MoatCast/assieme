import type { AgentKind } from "./bridge";

/** Every kind, in menu order, with the name people know it by and who makes it. */
export const AGENT_KINDS: { id: AgentKind; name: string; maker: string }[] = [
  { id: "claude", name: "Claude Code", maker: "Anthropic" },
  { id: "codex", name: "Codex", maker: "OpenAI" },
  { id: "gemini", name: "Gemini CLI", maker: "Google" },
  { id: "opencode", name: "OpenCode", maker: "open source" },
  { id: "cursor", name: "Cursor", maker: "Anysphere" },
];
export const kindName = (k: AgentKind) => AGENT_KINDS.find((x) => x.id === k)?.name ?? k;
/** Which kinds are installed, from a snapshot's clis (null until the service has said). */
export const installedOf = (clis: Record<string, boolean> | null | undefined) =>
  clis ? Object.fromEntries(AGENT_KINDS.map((k) => [k.id, !!clis[k.id]])) as Record<AgentKind, boolean> : null;
