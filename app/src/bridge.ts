// The only module that talks to Tauri. Screens import this; switching to another shell rewrites only this file
// and the native bridge behind it.
import { Channel, invoke } from "@tauri-apps/api/core";

export type Agent = {
  agent: string;
  source: string;
  state: string;
  detail: string;
  seconds: number | null;
  herdr: string | null;
  cwd: string | null;
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
};

export type TerminalHandle = {
  input(text: string): Promise<unknown>;
  resize(cols: number, rows: number): Promise<unknown>;
  close(): Promise<unknown>;
};

export const snapshot = () => invoke<Snapshot>("brain_call", { method: "brain.snapshot", params: {} });

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
