// The only module that talks to Tauri. Screens import this; switching to another shell rewrites only this file
// and the native bridge behind it.
import { Channel, invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

export type Agent = {
  agent: string;
  source: string;
  state: string;
  detail: string;
  seconds: number | null;
  herdr: string | null;
  cwd: string | null;
  herdr_address: string | null;
};

export type TeamRow = {
  team: string;
  role: string | null;
  address: string;
  state: string;
  status: string | null;
  briefed: boolean;
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

export type TerminalHandle = {
  input(text: string): Promise<unknown>;
  resize(cols: number, rows: number): Promise<unknown>;
  close(): Promise<unknown>;
};

const brain = <T>(method: string, params: object = {}) => invoke<T>("brain_call", { method, params });

export const snapshot = () => brain<Snapshot>("brain.snapshot");
export const checkProject = (path: string) => brain<{ ok: boolean; reason?: string }>("project.check", { path });
export const teamPresets = () =>
  brain<{ presets: Preset[]; practices: Practices; last: Preset | null; models: ModelOptions }>("team.presets");
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

export const serviceRunning = () => invoke<boolean>("service_running");
export const serviceInstall = () => invoke<void>("service_install");
