// The only module that talks to Tauri. Screens import this; switching to another shell rewrites only this file
// and the native bridge behind it.
import { Channel, invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { openUrl } from "@tauri-apps/plugin-opener";

export type Agent = {
  agent: string;
  source: string;
  state: string;
  detail: string;
  seconds: number | null;
  herdr: string | null;
  cwd: string | null;
  herdr_address: string | null;
  group?: string;
};

export type TeamRow = {
  team: string;
  role: string | null;
  address: string;
  state: string;
  status: string | null;
  briefed: boolean;
  cwd?: string | null;
};

export type Snapshot = {
  at: string;
  clis: { claude: boolean; codex: boolean; hc: boolean; herdr: boolean };
  agents: Agent[];
  alerts: unknown[];
  queue: unknown[];
  stalls: unknown[];
  teams: TeamRow[];
  starting: Starting[];
  found_teams?: string[];
  first_run_done?: boolean;
  /** An assistant's draft waiting to open in New team. */
  draft?: string | null;
};

export type AgentKind = "claude" | "codex";

/** One agent in the New team form, in the brain's shape. */
export type RoleDraft = {
  name: string;
  agent: AgentKind;
  job: string;
  checkout: "branch" | "read-only";
  parent: string | null;
  brief: string;
  /** null: the agent's own default, as in the person's terminal. */
  model?: string | null;
  effort?: string | null;
};

export type ModelChoice = { id: string; label: string; efforts: string[] };
/** Per CLI: the models offered and the efforts offered when the model is left at Default. */
export type ModelOptions = Record<AgentKind, { models: ModelChoice[]; efforts: string[] }>;

export type Preset = { id: string; label: string; roles: RoleDraft[] };
export type PracticeGroup = { id: string; title: string; summary: string; rules: string[] };
export type Practices = { groups: PracticeGroup[]; basics: { title: string; detail: string }[] };
export type Starting = {
  team: string;
  project: string;
  done: boolean;
  error: string | null;
  roles: { role: string; state: string; detail: string }[];
};
export type TeamForm = { project: string; task: string; roles: RoleDraft[]; practices: Record<string, boolean> };
export type Suggestion = { roles: RoleDraft[]; why: string };
export type TeamDraft = TeamForm & { id: string; why: string };

export type TerminalHandle = {
  input(text: string): Promise<unknown>;
  resize(cols: number, rows: number): Promise<unknown>;
  close(): Promise<unknown>;
};

const brain = <T>(method: string, params: object = {}) => invoke<T>("brain_call", { method, params });

export const snapshot = () => brain<Snapshot>("brain.snapshot");
/** Hands over the draft and removes it from the brain: it opens once. */
export const takeDraft = (id: string) => brain<TeamDraft | null>("draft.take", { id });
export const checkProject = (path: string) => brain<{ ok: boolean; reason?: string }>("project.check", { path });
export const teamPresets = () =>
  brain<{ presets: Preset[]; practices: Practices; last: Preset | null; models: ModelOptions; practice_defaults?: Record<string, boolean> }>("team.presets");
/** Start creates a git worktree per agent before it answers, which can take a while on a large repository. */
export const startTeam = (form: TeamForm) =>
  invoke<{ team: string }>("brain_call", { method: "team.start", params: form, timeoutS: 120 });
/** Help me choose runs the person's own agent, so it may take minutes. */
export const suggestTeam = (a: { project: string; size: string; review: string; budget: string }) =>
  invoke<Suggestion>("brain_call", { method: "team.suggest", params: a, timeoutS: 180 });

export async function pickFolder(): Promise<string | null> {
  const p = await open({ directory: true, multiple: false });
  return typeof p === "string" ? p : null;
}

const FIELD = /^(project|roles|role:\d+|start|suggest): ([\s\S]*)$/;

/** The brain's New team errors read "<field>: <message>"; anything else belongs above the Start button. */
export function fieldOf(error: unknown): { field: string; message: string } {
  const text = error instanceof Error ? error.message : String(error);
  const m = FIELD.exec(text);
  return m ? { field: m[1], message: m[2] } : { field: "start", message: text };
}

/** Attach to a terminal in the `assieme` session; other sessions are read-only (`readTerminal`). */
export async function openTerminal(
  address: string,
  cols: number,
  rows: number,
  observe: boolean,
  onData: (bytes: Uint8Array) => void,
  onClose: (reason: string) => void,
): Promise<TerminalHandle> {
  const data = new Channel<ArrayBuffer>();
  data.onmessage = (b) => onData(new Uint8Array(b));
  const closed = new Channel<string>();
  closed.onmessage = onClose;
  const id = await invoke<number>("terminal_open", { address, cols, rows, observe, onData: data, onClose: closed });
  return {
    input: (text) => invoke("terminal_input", { id, data: Array.from(new TextEncoder().encode(text)) }),
    resize: (c, r) => invoke("terminal_resize", { id, cols: c, rows: r }),
    close: () => invoke("terminal_close", { id }),
  };
}

export const readTerminal = (session: string, address: string) =>
  invoke<string>("terminal_read", { session, address });

export type PaneButton = { id: string; label: string; kind: "menu" | "answer" | "action" | "reply" };
export type Pane = {
  role: string;
  agent: AgentKind;
  address: string;
  session?: string;
  reachable?: boolean;
  command?: string | null;
  state: "asking" | "stuck" | "waiting" | "working" | "idle" | "exited";
  since_s: number | null;
  detail: string;
  live: boolean;
  fix: { id: string; text: string; buttons: PaneButton[] } | null;
};
export type TeamView = { team: string; panes: Pane[]; strip: { role: string; text: string }[] };

export const teamView = (team: string) => brain<TeamView>("team.view", { team });
export const paneAct = (a: { team: string; role: string; fix: string; button: string }) =>
  brain<{ ok: boolean; detail: string }>("pane.act", a);

export type MapNode = { role: string; state: Pane["state"]; since_s: number | null; agent: AgentKind; parent: string | null; group: string | null };
export type MapEdge = { from: string; to: string; wakes: number; empty: number; noisy: { per_hour: number } | null };
export type MapGroup = {
  name: string;
  roles: string[];
  states: Record<string, Pane["state"]>;
  need_you: number;
  edges: { to: string; wakes: number; noisy: { per_hour: number } | null }[];
};
export type Tip = { id: string; from: string; to: string; share: number };
export type ActivityRow = {
  id: string | null;
  at: string;
  day: string;
  title: string;
  reason: string;
  by: string;
  ok: boolean;
  action: string;
  undo: { label: string; confirm?: string } | null;
};
export type TeamMap = {
  team: string;
  found: boolean;
  stopped: boolean;
  title: string;
  subtitle: string;
  panes: Pane[];
  strip: { role: string; text: string }[];
  nodes: MapNode[];
  edges: MapEdge[];
  waits: { from: string; to: string }[];
  groups: MapGroup[] | null;
  tips: Tip[];
  done: ActivityRow[];
  spend: { tokens: number; waste_share: number } | null;
  spend_ready?: boolean;
};
export type AgentDetail = {
  role: string;
  cli: AgentKind;
  job: string;
  state: Pane["state"];
  since_s: number | null;
  detail: string;
  fix: Pane["fix"];
  reachable: boolean;
  command: string | null;
  memory: { pct: number; amber: boolean } | null;
  recent: { at: string; text: string }[];
  wakers: { role: string; count: number }[];
  empty_wakes: number;
};
export type Settings = {
  fixes: Record<"nudge" | "compact" | "tell", "ask" | "auto">;
  practices: Record<string, boolean>;
  notify: { needs_you: boolean; stalls: boolean; finished: boolean };
  show_commands: boolean;
  groups: Record<string, string>;
  first_run_done: boolean;
};
export type QuietPair = { from_cwd: string; to_cwd: string; label: string; saved: number | null };
export type TeamListing = { name: string; kind: "assieme" | "found"; agents: number };
export type FoundAgent = { name: string; cwd: string; group: string };

export const teamMap = (team: string) => brain<TeamMap>("team.map", { team });
export const agentDetail = (team: string, role: string) => brain<AgentDetail | null>("agent.detail", { team, role });
export const tipApply = (team: string, tip: string) => brain<{ ok: boolean; detail: string }>("tip.apply", { team, tip });
/** Creating a worktree can take a while on a large repository. */
export const addAgent = (team: string, role: Partial<RoleDraft> & { kind: string }) =>
  invoke<{ team: string }>("brain_call", { method: "team.add_agent", params: { team, role }, timeoutS: 120 });
export const stopTeam = (team: string) => brain<{ stopped: number }>("team.stop", { team });
export const listTeams = () => brain<{ teams: TeamListing[]; found_agents: FoundAgent[] }>("teams.list");
export const activityList = (team: string | null, filter: "all" | "you" | "auto") =>
  brain<{ rows: ActivityRow[] }>("activity.list", { team, filter });
export const activityUndo = (id: string) => brain<{ ok: boolean; detail: string }>("activity.undo", { id });
export const getSettings = () => brain<Settings>("settings.get");
export const setSetting = (key: string, value: unknown) => brain<Settings>("settings.set", { key, value });
export const quietPairs = (team: string | null) =>
  brain<{ pairs: QuietPair[]; agents: { role: string; cwd: string }[]; held: number }>("quiet.pairs", { team });
export const quietSet = (a: { from_cwd: string; to_cwd: string; label: string; on: boolean }) =>
  brain<{ ok: boolean }>("quiet.set", a);
export const setGroups = (groups: Record<string, string>) => brain<Settings>("groups.set", { groups });

export type ReviewCheck = { id: string; state: "ok" | "bad" | "warn" | "running" | "none"; text: string; detail: string };
export type ReviewNote = { id: string; author: string; file: string; line: number; text: string };
export type ReviewData = {
  team: string;
  role: string;
  project: string;
  base: string;
  branch: string;
  tip: string;
  ahead: number;
  files: { path: string; add: number; del: number; binary: boolean; missing: boolean }[];
  diff: string;
  too_big: boolean;
  checks: ReviewCheck[];
  notes: ReviewNote[];
  agent: { live: boolean; working: boolean; address: string | null };
};
export type ReviewWaiting = { team: string; project: string; role: string; branch: string; ahead: number };
export type MergeResult = { role: string; base: string; problem: string | null; undo: string };

const slow = <T>(method: string, params: object, timeoutS: number) => invoke<T>("brain_call", { method, params, timeoutS });

export const reviewWaiting = () => brain<ReviewWaiting[]>("review.waiting");
/** `retest` tries a failed or impossible test run again; the screen asks for it when it opens. */
export const reviewGet = (team: string, role: string, retest = false) => slow<ReviewData>("review.get", { team, role, retest }, 60);
export const reviewMerge = (team: string, role: string, tip: string, force: boolean) =>
  slow<MergeResult>("review.merge", { team, role, tip, force }, 60);
export const reviewUndo = (id: string) => slow<{ role: string; base: string }>("review.undo", { id }, 60);
export const reviewDiscard = (team: string, role: string, tip: string) =>
  slow<{ role: string; problem: string | null }>("review.discard", { team, role, tip }, 60);
/** Pushes the branch first, so it may take minutes on a slow connection. */
export const reviewPullRequest = (team: string, role: string) =>
  slow<{ url: string | null; detail?: string }>("review.pull_request", { team, role }, 260);
/** A stopped agent starts again first, which takes up to a minute. */
export const reviewAsk = (team: string, role: string, text: string) =>
  slow<{ restarted: boolean }>("review.ask", { team, role, text }, 90);
export const reviewNote = (team: string, role: string, note: string, action: "fix" | "ignore") =>
  slow<{ ok: boolean }>("review.note", { team, role, note, action }, 90);
export const reviewEditor = (team: string, role: string, file: string, line?: number) =>
  brain<{ ok: boolean }>("review.open_editor", { team, role, file, line });
export const openLink = (url: string) => openUrl(url);

export type Assistants = { clients: { id: string; label: string; connected: boolean; blocked?: boolean }[]; problems: string[] };
/** These run each client's own command, which can take several seconds apiece. */
export const assistantsStatus = () => invoke<Assistants>("brain_call", { method: "assistants.status", params: {}, timeoutS: 60 });
export const assistantsConnect = () => invoke<Assistants>("brain_call", { method: "assistants.connect", params: {}, timeoutS: 120 });
export const assistantsDisconnect = () => invoke<Assistants>("brain_call", { method: "assistants.disconnect", params: {}, timeoutS: 120 });
export const serviceRunning = () => invoke<boolean>("service_running");
export const serviceInstall = () => invoke<void>("service_install");
