// The only module that talks to Tauri. Screens import this; switching to another shell rewrites only this file
// and the native bridge behind it.
import { Channel, invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { ask, open, save } from "@tauri-apps/plugin-dialog";
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
  /** A team on another machine (step 15); reachable is false while its line is down. */
  machine?: string;
  reachable?: boolean;
};

export type Snapshot = {
  at: string;
  /** An older service names only Claude Code and Codex. */
  clis: Partial<Record<AgentKind, boolean>> & { hc: boolean; herdr: boolean };
  agents: Agent[];
  alerts: unknown[];
  queue: unknown[];
  stalls: unknown[];
  teams: TeamRow[];
  starting: Starting[];
  found_teams?: string[];
  /** Teams of scheduled runs: kept in the team picker after their agents stop. */
  scheduled_teams?: string[];
  first_run_done?: boolean;
  /** An assistant's draft waiting to open in New team. */
  draft?: string | null;
};

export type AgentKind = "claude" | "codex" | "gemini" | "opencode" | "cursor";
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
  /** planner, builder, reviewer or tester; null for a custom role. */
  kind?: string | null;
  /** Paths in the project only this agent edits. */
  owns?: string[];
  /** null: starts as agents did before levels existed (a team saved earlier). */
  level?: Level | null;
  rules?: Rule[];
  /** Runs in a container that sees only its own copy. */
  container?: boolean;
  /** That container may use this kind's sign-in from the Mac. */
  share_signin?: boolean;
};

export type Level = "safe" | "read-only" | "trusted";
/** A rule on the program run: its first words, matched as a prefix. `words` is how the person wrote it. */
export type Rule = { decision: "block" | "allow"; program: string[]; words: string };
export type KindInfo = { brief: string; job: string; checkout: "branch" | "read-only" };
export type PermissionLevel = { id: Level; title: string; about: string; summary: string; allowed: string[]; blocked: string[] };
export type PermissionTable = { levels: PermissionLevel[]; items: Record<string, { words: string; note: string; codex_note?: string }> };

export type ModelChoice = { id: string; label: string; efforts: string[] };
/** Per CLI: the models offered and the efforts offered when the model is left at Default. */
export type ModelOptions = Record<AgentKind, { models: ModelChoice[]; efforts: string[] }>;

export type Preset = { id: string; label: string; roles: RoleDraft[] };
/** One rule of a practice group: `text` is what agents are told. Its `id` is its switch's key. */
export type PracticeRule = { id: string; title: string; detail: string; note?: string; text: string };
export type PracticeGroup = { id: string; title: string; summary: string; detail: string; rules: PracticeRule[] };
/** A preset for bigger teams: off unless its key is true. */
export type BiggerPreset = { id: string; title: string; summary: string; detail: string };
export type Practices = { groups: PracticeGroup[]; basics: { title: string; detail: string }[]; bigger: BiggerPreset[] };
export type Starting = {
  team: string;
  project: string;
  done: boolean;
  error: string | null;
  roles: { role: string; state: string; detail: string }[];
};
export type TeamForm = { project: string; task: string; roles: RoleDraft[]; practices: Record<string, boolean> };
export type Suggestion = { roles: RoleDraft[]; why: string };
/** One kind of thing an import from herdr couldn't carry over, and the agents it is about. */
export type ImportNote = { text: string; agents: string[] };
/** from "herdr": an import, with what didn't carry over and, for a team still running in herdr, its name. */
export type TeamDraft = TeamForm & { id: string; why: string; from?: "herdr"; notes?: ImportNote[]; running?: string };
export type HerdrTeam = { id: string; label: string; where: string; agents: number; running: boolean };

export type TerminalHandle = {
  input(text: string): Promise<unknown>;
  resize(cols: number, rows: number): Promise<unknown>;
  close(): Promise<unknown>;
};

const brain = <T>(method: string, params: object = {}) => invoke<T>("brain_call", { method, params });

export const snapshot = () => brain<Snapshot>("brain.snapshot");
/** Hands over the draft and removes it from the brain: it opens once. */
export const takeDraft = (id: string) => brain<TeamDraft | null>("draft.take", { id });
/** setup, tools and too_large say which kind of problem `reason` is; uncommitted counts what Save them first would commit. */
export type ProjectCheck = { ok: boolean; reason?: string; setup?: boolean; tools?: boolean; too_large?: boolean; uncommitted?: number };
export const checkProject = (path: string) => brain<ProjectCheck>("project.check", { path });
export const teamPresets = () =>
  brain<{ presets: Preset[]; practices: Practices; last: Preset | null; models: ModelOptions; practice_defaults?: Record<string, boolean>;
    kinds?: Record<string, KindInfo>; permissions?: PermissionTable }>("team.presets");
/** Start creates a git worktree per agent before it answers, which can take a while on a large repository. */
export const startTeam = (form: TeamForm) =>
  invoke<{ team: string }>("brain_call", { method: "team.start", params: form, timeoutS: 120 });
/** Help me choose runs the person's own agent, so it may take minutes. */
export const suggestTeam = (a: { project: string; size: string; review: string; budget: string }) =>
  invoke<Suggestion>("brain_call", { method: "team.suggest", params: a, timeoutS: 180 });

/** Improve this brief: the person's own agent rewrites it, read-only in the project. Nothing is saved. */
export const improveBrief = (project: string, role: RoleDraft) =>
  invoke<{ brief: string }>("brain_call", { method: "team.improve_brief", params: { project, role }, timeoutS: 180 });
/** Add a rule: plain words to a rule on the program run, shown before it is added. Nothing is saved. */
export const ruleDraft = (project: string, words: string) =>
  invoke<Rule>("brain_call", { method: "team.rule_draft", params: { project, words }, timeoutS: 130 });

/** herdr teams on this Mac that can be imported, and one of them (or `script:<path>`) as a New team draft. */
/** unreadable: herdr is running but couldn't be read, in words; null when it was read or isn't running. */
export const herdrFind = () => brain<{ teams: HerdrTeam[]; unreadable?: string | null }>("herdr.find");
export const herdrImport = (source: string) => brain<TeamDraft>("herdr.import", { source });

/** The app's own dialog: `window.confirm` shows nothing in its web view, so it would replace without asking. */
export const confirmReplace = () =>
  ask("Replace the agents you've edited?", { title: "New team", kind: "warning", okLabel: "Replace", cancelLabel: "Keep them" });

export async function pickScript(): Promise<string | null> {
  const p = await open({ directory: false, multiple: false, title: "Choose a launch script" });
  return typeof p === "string" ? p : null;
}

export async function pickFolder(): Promise<string | null> {
  const p = await open({ directory: true, multiple: false });
  return typeof p === "string" ? p : null;
}

/** The save dialog names a folder that doesn't exist yet; folderCreate makes it. */
export async function pickNewFolder(): Promise<string | null> {
  return (await save({ title: "New folder", defaultPath: "New project" })) ?? null;
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

export type PaneButton = { id: string; label: string; kind: "menu" | "answer" | "action" | "reply" | "budget" };
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
  /** `budget` is set on a pause at a daily limit: its scope ("*" is the whole team), who is paused, and the line under the question. */
  fix: { id: string; text: string; buttons: PaneButton[]; budget?: string; who?: string; detail?: string } | null;
};
export type TeamView = { team: string; panes: Pane[]; strip: { role: string; text: string }[] };

export const teamView = (team: string) => brain<TeamView>("team.view", { team });
export const paneAct = (a: { team: string; role: string; fix: string; button: string }) =>
  brain<{ ok: boolean; detail: string }>("pane.act", a);

export type MenuButton = { id: string; label: string; kind: PaneButton["kind"] };
export type MenuItem = { role: string; state: Pane["state"]; since_s: number | null; text: string; says: string; fix: string | null; buttons: MenuButton[] };
export type MenuTeam = { name: string; where: string; need: number; items: MenuItem[] };
export type MenuState = { count: number; teams: MenuTeam[]; spend_tokens: number | null; auto_fixes: number; paused_until: string | null };

export const menuState = () => brain<MenuState>("menu.state");
export const notificationsPause = () => brain<MenuState>("notifications.pause");
export const notificationsResume = () => brain<MenuState>("notifications.resume");
export const menuFit = (height: number) => invoke<void>("menu_fit", { height });
export const notificationsOff = () => invoke<boolean>("notifications_off");
export const notificationSettingsOpen = () => invoke<void>("notification_settings_open");
export const onMenuOpened = (run: () => void) => listen("menu-opened", run);
export const onMenuState = (run: (m: MenuState | null) => void) => listen<MenuState | null>("menu-state", (e) => run(e.payload));

export type OpenRequest = { team: string; role: string | null; view: "terminals" | "review" | null };
/** Bring the main window forward, on this agent if one is given. Closes the menu bar panel. */
export const windowOpen = (a: Partial<OpenRequest> = {}) => invoke<void>("window_open", a);
/** Light or dark window chrome; "system" follows the Mac. */
export const windowTheme = async (t: "light" | "dark" | "system") => getCurrentWindow().setTheme(t === "system" ? null : t);
/** The menu bar or a notification asks the main window to show an agent. */
export const onOpen = (run: (r: OpenRequest) => void) => listen<OpenRequest>("assieme-open", (e) => run(e.payload));

export type MapNode = { role: string; state: Pane["state"]; since_s: number | null; agent: AgentKind; parent: string | null; group: string | null; paused?: boolean };
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
  /** Its machine can't be reached: this is the last view from there (step 15). */
  unreachable?: import("./remote").Machine;
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
  /** Daily limits, in tokens. Null for agents found on this Mac: they get none. */
  budget?: Budget | null;
};
export type Budget = {
  limit: number | null;
  allowed: number | null;
  used: number;
  at_limit: "pause" | "tell";
  agents: { role: string; used: number; limit: number | null }[];
};
/** `limits` maps "*" (the whole team) or an agent's name to tokens a day; leave one out for no limit. */
export const budgetSet = (team: string, limits: Record<string, number>, at_limit: Budget["at_limit"]) =>
  brain<TeamMap>("budget.set", { team, limits, at_limit });
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
export type TeamBriefData = { goal: string; rules: string; decisions: { id: string; q: string; a: string; role: string; by: "you" | "agent"; ms: number }[] };
export const briefGet = (team: string) => brain<TeamBriefData>("brief.get", { team });
export const briefSave = (team: string, goal: string, rules: string) => brain<TeamBriefData>("brief.save", { team, goal, rules });
export const briefRemove = (team: string, id: string) => brain<TeamBriefData>("brief.remove", { team, id });
export const stopTeam = (team: string) => brain<{ stopped: number }>("team.stop", { team });
export const listTeams = () => brain<{ teams: TeamListing[]; found_agents: FoundAgent[] }>("teams.list");
export const activityList = (team: string | null, filter: "all" | "you" | "auto") =>
  brain<{ rows: ActivityRow[] }>("activity.list", { team, filter });
/** Improve this team: Apply findings give rules with the agents' next turn; Review ones change nothing. `draft` is the
 *  herdr source that opens the same agents in New team; `copy` the rules to paste for an agent Assieme can't reach. */
export type Finding = { id: string; kind: "apply" | "review"; title: string; detail: string; restart: boolean; copy: string | null; draft: string | null; to: string[] };
export type Improvement = { team: string; found: boolean; findings: Finding[]; in_place: string[] };
export const teamImprove = (team: string) => invoke<Improvement>("brain_call", { method: "team.improve", params: { team }, timeoutS: 60 });
export const teamImproveApply = (team: string, ids: string[]) =>
  brain<{ ok: boolean; applied: { id: string; entry: string }[] }>("team.improve_apply", { team, ids });
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
export type MergeResult = { role: string; base: string; problem: string | null; undo: string; preview_stopped?: boolean };

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

/** One agent's live preview: its dev server in its worktree. `needs_command` asks the person how the project starts. */
export type Preview = {
  team: string;
  role: string;
  project: string;
  state: "starting" | "running" | "failed" | "needs_command" | "confirm";
  command?: string;
  url?: string | null;
  port?: number | null;
  reason?: string;
  output?: string;
  fix?: "install" | "retry" | "command" | null;
  /** The install command "Install and try again" runs, named on screen because it downloads. */
  install?: string | null;
  /** For `confirm`: each command with the package scripts it runs (their content, and whether the branch changed them),
   *  the names of the changed ones, and what to send back to run it. */
  steps?: { line: string; scripts: { name: string; runs: string; changed: boolean }[] }[];
  changed?: string[];
  fingerprint?: string;
};
export const previewList = () => brain<Preview[]>("preview.list");
export const previewStart = (team: string, role: string, command: string | null = null, install = false,
  confirm: string | null = null) => slow<Preview>("preview.start", { team, role, command, install, confirm }, 60);
export const previewStop = (team: string, role: string) => slow<{ stopped: boolean }>("preview.stop", { team, role }, 30);

export type BoardStatus = "todo" | "doing" | "review" | "done";
export type BoardCard = {
  id: string;
  title: string;
  role: string | null;
  status: BoardStatus;
  /** "you", or the agent that made the card. */
  by: string;
  note: string;
  needs_you: boolean;
  moved_s: number;
  /** Doing: what its agent is up to right now. */
  agent?: Pick<Pane, "role" | "state" | "since_s" | "detail" | "fix" | "reachable"> | null;
  /** Review: what the brain knows about its agent's branch; null when there is none. */
  branch?: { ahead: number; files: number; add: number; del: number; tests: Pick<ReviewCheck, "state" | "text"> | null; notes: string[] } | null;
};
export type BoardData = { team: string; title: string; cards: BoardCard[]; spend: TeamMap["spend"]; spend_ready?: boolean };
/** A Review card reads its branch from git, which can be slow on a large repository. */
export const boardGet = (team: string) => slow<BoardData>("board.get", { team }, 60);
export const boardAdd = (team: string, title: string, role: string | null) =>
  brain<{ card: BoardCard; told: boolean }>("board.add", { team, title, role });
export const boardMove = (team: string, id: string, status: BoardStatus) => brain<BoardCard>("board.move", { team, id, status });
export const boardRemove = (team: string, id: string) => brain<{ ok: boolean }>("board.remove", { team, id });

export type Assistants = { clients: { id: string; label: string; connected: boolean; blocked?: boolean }[]; problems: string[] };
/** These run each client's own command, which can take several seconds apiece. */
export const assistantsStatus = () => invoke<Assistants>("brain_call", { method: "assistants.status", params: {}, timeoutS: 60 });
export const assistantsConnect = () => invoke<Assistants>("brain_call", { method: "assistants.connect", params: {}, timeoutS: 120 });
export const assistantsDisconnect = () => invoke<Assistants>("brain_call", { method: "assistants.disconnect", params: {}, timeoutS: 120 });
export const serviceRunning = () => invoke<boolean>("service_running");
export const serviceInstall = () => invoke<void>("service_install");

export type SetupStatus = { git: boolean; agents: Partial<Record<AgentKind, { installed: boolean; signed_in: boolean }>> };
export type SetupCommands = Record<AgentKind, { name: string; install: string; signin: string; status: string }>;
// The brain answers status in about 5 s, under the 20 s default call timeout.
export const setupStatus = () => brain<SetupStatus>("setup.status");
export const setupCommands = () => brain<SetupCommands>("setup.commands");
export const installTools = () => brain<{ ok: boolean }>("setup.install_tools");
export const installAgent = (agent: AgentKind) => brain<{ address: string }>("setup.install_agent", { agent });
export const signinAgent = (agent: AgentKind) => brain<{ address: string }>("setup.signin_agent", { agent });
export const terminalState = (address: string) => brain<"running" | "finished">("setup.terminal_state", { address });
export const installStop = (address: string) => brain<{ ok: boolean }>("setup.install_stop", { address });
/** The container tool on this Mac, whether it runs, and whether this kind's container is built. */
export type ContainerState = { tool: "docker" | "container" | null; running: boolean; image_ready: boolean };
export const containerStatus = (agent: AgentKind) => brain<ContainerState>("container.status", { agent });
export const containerBuild = (agent: AgentKind) => brain<{ address: string }>("container.build", { agent });
export const containerOpen = () => brain<{ ok: boolean }>("container.open");
/** Listing and saving walk the whole folder, so they get the long timeout. */
export type FolderPreview = { files: number; sample: string[]; left_out: string[]; empty: boolean; first: boolean };
export const folderPreview = (path: string) => slow<FolderPreview>("setup.folder_preview", { project: path }, 120);
export const folderSave = (path: string) => slow<{ ok: boolean }>("setup.folder_save", { project: path }, 120);
export const folderCreate = (path: string) => slow<{ path: string }>("setup.folder_create", { path }, 120);

/** Scheduled runs (Paper "v2 — Settings: scheduled runs"). `every` is a day or week time, or every n hours. */
export type Every = { kind: "day"; at: string } | { kind: "week"; day: number; at: string } | { kind: "hours" | "minutes"; n: number };
export type Schedule = {
  id: string; name: string; team: string; project: string; project_name: string; preset: string; preset_label: string; who: string;
  task: string; every: Every; when: string; paused: boolean; state: "running" | "paused" | "waiting";
  result: "running" | "started" | "finished" | "ended" | "stopped" | "failed" | null; line: string; note: string | null;
  next: string; review_role: string | null;
};
export type ScheduleTeam = { id: string; label: string; who: string };
export type ScheduleForm = { id?: string; name: string; project: string; preset: string; task: string; every: Every };
/** `teams` also lists the team choices, which reads every preset: ask for it when a sheet opens, not on every poll. */
export const scheduleList = (teams = false) => brain<{ schedules: Schedule[]; teams?: ScheduleTeam[] }>("schedule.list", { teams });
/** Saving checks the folder and the team, so it can take a moment on a large project. */
export const scheduleSave = (form: ScheduleForm) =>
  invoke<Schedule[]>("brain_call", { method: "schedule.save", params: form, timeoutS: 60 });
export const schedulePause = (id: string, paused: boolean) => brain<Schedule[]>("schedule.pause", { id, paused });
export const scheduleRemove = (id: string) => brain<Schedule[]>("schedule.remove", { id });
export const scheduleStop = (id: string) => brain<Schedule[]>("schedule.stop", { id });
/** Try again now starts the team, which creates its worktrees first. */
export const scheduleRunNow = (id: string) =>
  invoke<Schedule[]>("brain_call", { method: "schedule.run_now", params: { id }, timeoutS: 120 });
