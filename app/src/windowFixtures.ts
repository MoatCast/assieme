// Test data for the one-window list and panel (listModel, AgentList, Window, AgentPanel, TeamOverview).
import type { ActivityRow, Pane, ReviewWaiting, Starting, TeamMap } from "./bridge";

export const pane = (role: string, over: Partial<Pane> = {}): Pane => ({
  role, agent: "claude", address: `t-${role}`, state: "working", since_s: 120, detail: "", live: true, fix: null, reachable: true, ...over,
});

export const MAP = (over: Partial<TeamMap> = {}): TeamMap => ({
  team: "shop", found: false, stopped: false, title: "All quiet", subtitle: "", panes: [], strip: [],
  nodes: [], edges: [], waits: [], groups: null, tips: [], done: [], spend: null, fixes_today: { fixed: 0, checking: 0 }, run: null, ...over,
});

export const Q = (text: string): Pane["fix"] => ({
  id: `q-${text}`, text, buttons: [
    { id: "b1", label: "Behind a flag", kind: "answer" }, { id: "b2", label: "To main", kind: "answer" }, { id: "b3", label: "Reply", kind: "reply" },
  ],
});

export const fixRow = (agent: string, outcome: ActivityRow["outcome"], at: string, over: Partial<ActivityRow> = {}): ActivityRow => ({
  id: `fix-${agent}-${at}`, at, day: "2026-11-20", title: `Nudged ${agent}`, reason: "", by: "auto", ok: true, action: "nudge", undo: null,
  agent, outcome, outcome_at: outcome === "checking" ? null : at, check_until: "14:32",
  measured: { minutes: 9, what: "the same test failed 4 times", tokens: 38000 }, problem: "stuck", ...over,
});

export const waitingFor = (role: string, ahead: number, team = "shop"): ReviewWaiting =>
  ({ team, project: "/p", role, branch: `assieme/${role}`, ahead });

export const starting = (roles: { role: string; state: string; detail?: string }[], over: Partial<Starting> = {}): Starting => ({
  team: "shop", project: "/p", done: false, error: null, roles: roles.map((r) => ({ detail: "", ...r })), ...over,
});
