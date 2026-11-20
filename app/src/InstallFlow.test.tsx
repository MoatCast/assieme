import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { SetupStatus } from "./bridge";

const b = vi.hoisted(() => ({
  setupCommands: vi.fn(), setupStatus: vi.fn(), installAgent: vi.fn(), signinAgent: vi.fn(),
  terminalState: vi.fn(), installStop: vi.fn(), openLink: vi.fn(),
}));
vi.mock("./bridge", () => b);
vi.mock("./Terminal", () => ({ default: ({ address }: { address: string }) => <div data-testid="terminal">{address}</div> }));

import InstallFlow from "./InstallFlow";

const COMMANDS = { claude: { name: "Claude Code", install: "curl -fsSL https://example.test/x.sh | bash", signin: "claude auth login", status: "s" } };
const status = (installed: boolean, signed_in: boolean): SetupStatus => ({ git: true, agents: { claude: { installed, signed_in } } } as SetupStatus);
const flush = () => act(async () => {});
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

beforeEach(() => {
  vi.useFakeTimers();
  Object.values(b).forEach((f) => f.mockReset());
  b.setupCommands.mockResolvedValue(COMMANDS);
  b.installAgent.mockResolvedValue({ address: "t7" });
  b.signinAgent.mockResolvedValue({ address: "t8" });
  b.terminalState.mockResolvedValue("running");
  b.installStop.mockResolvedValue({ ok: true });
  b.setupStatus.mockResolvedValue(status(false, false));
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

type Action = { label: string; act: () => void; busy: boolean } | null;
function flow(start: "install" | "signin" = "install", st: SetupStatus | null = status(false, false)) {
  const h = { onDone: vi.fn(), onSkip: vi.fn(), actions: [] as Action[] };
  const el = (s: SetupStatus | null) => <InstallFlow agent="claude" start={start} status={s} pollMs={1000}
    onDone={h.onDone} onSkip={h.onSkip} onAction={(a) => h.actions.push(a)} />;
  const view = render(el(st));
  const last = () => h.actions[h.actions.length - 1];
  return { ...h, last, rerender: (s: SetupStatus | null) => view.rerender(el(s)) };
}

test("the primary is handed to the owner through onAction, not drawn in the flow", async () => {
  const h = flow();
  await flush();
  screen.getByText("Install Claude Code?");
  screen.getByText("The exact command, if you want to check it");
  expect(h.last()).toMatchObject({ label: "Install Claude Code", busy: false });
  expect(screen.queryByRole("button", { name: "Install Claude Code" })).toBeNull();
  await act(async () => h.last()!.act());
  expect(b.installAgent).toHaveBeenCalledWith("claude");
  screen.getByText("Installing Claude Code");
  expect(screen.getByTestId("terminal").textContent).toBe("t7");
  expect(h.last()).toMatchObject({ label: "Cancel" });
});

test("install then sign-in, and a signed-in agent is done", async () => {
  const h = flow();
  await flush();
  await act(async () => h.last()!.act());
  b.setupStatus.mockResolvedValue(status(true, false));
  b.terminalState.mockResolvedValue("finished");
  await tick(1000);
  screen.getByText("Sign in to Claude Code");
  expect(h.last()).toMatchObject({ label: "Done", busy: true });
  h.rerender(status(true, true));
  expect(h.last()).toMatchObject({ label: "Done", busy: false });
  await act(async () => h.last()!.act());
  expect(b.installStop).toHaveBeenCalledWith("t8");
  expect(h.onDone).toHaveBeenCalled();
});

test("Skip for now stops the terminal and skips", async () => {
  const h = flow("signin", status(true, false));
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
  await flush();
  expect(b.installStop).toHaveBeenCalledWith("t8");
  expect(h.onSkip).toHaveBeenCalled();
  expect(h.onDone).not.toHaveBeenCalled();
});

test("an install that finished without the agent offers Try again", async () => {
  const h = flow();
  await flush();
  await act(async () => h.last()!.act());
  b.terminalState.mockResolvedValue("finished");
  await tick(1000);
  screen.getByText("Claude Code didn’t install");
  expect(h.last()).toMatchObject({ label: "Try again" });
});
