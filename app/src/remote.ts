// Remote machines (step 15): the brain's machine methods, and the New team and setup calls aimed at another machine.
// The brain sends a call with `machine` to the brain there, over hc's machine line; this Mac's calls stay in bridge.ts.
import { invoke } from "@tauri-apps/api/core";
import type { AgentKind, FolderPreview, ProjectCheck, Suggestion, SetupStatus, TeamForm } from "./bridge";

const call = <T>(method: string, params: object = {}, timeoutS?: number) =>
  invoke<T>("brain_call", { method, params, ...(timeoutS ? { timeoutS } : {}) });

/** A machine's line, as hc reports it. since_ms: when it went down. */
export type Machine = {
  name: string; target: string; state: "connecting" | "connected" | "needs_login" | "needs_update" | "unreachable";
  since_ms?: number | null; why?: string | null; job?: SetupJob | null;
  /** Assieme was set up there from this Mac (or found ready). */
  set_up?: boolean;
};
export type SetupStep = { id: "runtime" | "assieme" | "brain"; state: "waiting" | "doing" | "done" | "failed" };
export type SetupJob = { target: string; machine: string | null; done: boolean; error: string | null; steps: SetupStep[] };
export type Host = { target: string; address: string; from: "ssh" | "tailscale"; name?: string; online?: boolean };
/** A machine with, when its brain answers, whether Assieme there is current and its Claude Code and Codex. */
export type MachineCheck = Machine & {
  ready: boolean; outdated?: boolean; problem?: string; git?: boolean;
  agents?: Record<AgentKind, { installed: boolean; signed_in: boolean }>;
};
/** What Test connection found. */
export type TestResult = Partial<MachineCheck> & {
  result: "ready" | "not_set_up" | "needs_login" | "new_host_key" | "host_key_changed" | "unreachable" | Machine["state"];
  target: string; why?: string;
};

export const remoteHosts = () => call<{ ssh: Host[]; tailscale: Host[]; machines: Machine[] }>("remote.hosts");
export const remoteMachines = () => call<Machine[]>("remote.machines");
export const remoteCheck = (machine: string) => call<MachineCheck>("remote.check", { machine }, 40);
/** Dialling a machine waits for ssh's own timeout (10 s) at most. */
export const remoteTest = (target: string) => call<TestResult>("remote.test", { target }, 60);
export const remoteSetup = (target: string) => call<{ job: SetupJob }>("remote.setup", { target });
export const remoteJob = (target: string) => call<{ job: SetupJob | null }>("remote.job", { target });
export const remoteConnect = (machine: string) => call<Machine>("remote.connect", { machine }, 30);
export const remoteForget = (machine: string) => call<{ ok: boolean }>("remote.forget", { machine });
/** Terminal on `ssh <target>`, or on hc's login for a machine already added: the person answers ssh there. */
export const remoteTerminal = (a: { target?: string; machine?: string }) => call<{ ok: boolean }>("remote.terminal", a);
/** Git repositories in that machine's home folder, as New team's suggestions. */
export const remoteRepos = (machine: string) => call<string[]>("remote.repos", { machine }, 30);

// The same calls as bridge.ts, on a machine.
export const checkProjectOn = (path: string, machine: string) => call<ProjectCheck>("project.check", { path, machine }, 40);
export const startTeamOn = (form: TeamForm, machine: string) => call<{ team: string }>("team.start", { ...form, machine }, 120);
export const suggestTeamOn = (a: { project: string; size: string; review: string; budget: string }, machine: string) =>
  call<Suggestion>("team.suggest", { ...a, machine }, 180);
export const setupStatusOn = (machine: string) => call<SetupStatus>("setup.status", { machine });
export const installAgentOn = (agent: AgentKind, machine: string) => call<{ address: string }>("setup.install_agent", { agent, machine });
export const signinAgentOn = (agent: AgentKind, machine: string) => call<{ address: string }>("setup.signin_agent", { agent, machine });
export const folderPreviewOn = (path: string, machine: string) => call<FolderPreview>("setup.folder_preview", { project: path, machine }, 120);
export const folderSaveOn = (path: string, machine: string) => call<{ ok: boolean }>("setup.folder_save", { project: path, machine }, 120);

/** "14:02": when a machine was last reached. */
export const since = (ms?: number | null) =>
  ms ? new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : "";
