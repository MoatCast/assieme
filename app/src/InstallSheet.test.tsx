import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { SetupStatus } from "./bridge";

const b = vi.hoisted(() => ({
  setupCommands: vi.fn(), setupStatus: vi.fn(), installAgent: vi.fn(), signinAgent: vi.fn(),
  terminalState: vi.fn(), installStop: vi.fn(), openLink: vi.fn(),
}));
vi.mock("./bridge", () => b);
vi.mock("./Terminal", () => ({
  default: ({ session, address, typing }: { session: string; address: string; typing?: boolean }) => (
    <div data-testid="terminal">{`${session}:${address}:${typing ? "typing" : "watch"}`}</div>
  ),
}));

import InstallSheet from "./InstallSheet";

const COMMANDS = {
  claude: { name: "Claude Code", install: "curl -fsSL https://example.test/from-the-brain.sh | bash", signin: "claude auth login", status: "claude auth status" },
  codex: { name: "Codex", install: "curl -fsSL https://example.test/codex.sh | sh", signin: "codex login", status: "codex login status" },
};
const status = (installed: boolean, signed_in: boolean): SetupStatus => ({
  git: true, agents: { claude: { installed, signed_in }, codex: { installed: false, signed_in: false } },
});
const flush = () => act(async () => {});
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;

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
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const open = async (start: "install" | "signin" = "install", st: SetupStatus | null = status(false, false), onClose = vi.fn()) => {
  render(<InstallSheet agent="claude" start={start} status={st} onClose={onClose} pollMs={1000} />);
  await flush();
  return onClose;
};

test("Install shows the command from the brain, not a copy", async () => {
  await open();
  screen.getByText("Install Claude Code?");
  screen.getByText(/This downloads Anthropic’s installer from claude\.ai and runs it\. Claude Code goes in your home folder\. You’ll need a paid Claude plan \(Pro or above\)\./);
  screen.getByText("The exact command, if you want to check it");
  expect(screen.getByText(COMMANDS.claude.install)).toBeTruthy();
  screen.getByText(/Prefer another way\? Install it yourself and Assieme will find it\./);
  expect(button("Anthropic’s install guide")).toBeTruthy();
  fireEvent.click(button("Anthropic’s install guide"));
  expect(b.openLink).toHaveBeenCalledWith(expect.stringContaining("https://"));
});

test("Not now starts nothing", async () => {
  const close = await open();
  fireEvent.click(button("Not now"));
  expect(close).toHaveBeenCalled();
  expect(b.installAgent).not.toHaveBeenCalled();
  expect(b.signinAgent).not.toHaveBeenCalled();
});

test("Install starts the installer and shows its terminal, which can be cancelled", async () => {
  const close = await open();
  fireEvent.click(button("Install Claude Code"));
  await flush();
  expect(b.installAgent).toHaveBeenCalledWith("claude");
  screen.getByText("Installing Claude Code");
  screen.getByText("This can take a minute. It may look still while it downloads. You can ignore any setup notes: Assieme will find it.");
  expect(screen.getByTestId("terminal").textContent).toBe("assieme:t7:typing");
  fireEvent.click(button("Cancel"));
  await flush();
  expect(b.installStop).toHaveBeenCalledWith("t7");
  expect(close).toHaveBeenCalled();
});

test("a failed Install call shows next to the button and can be retried", async () => {
  b.installAgent.mockRejectedValueOnce(new Error("no runtime"));
  await open();
  fireEvent.click(button("Install Claude Code"));
  await flush();
  screen.getByText(/no runtime/);
  expect(screen.queryByTestId("terminal")).toBeNull();
  fireEvent.click(button("Install Claude Code"));
  await flush();
  expect(screen.getByTestId("terminal")).toBeTruthy();
});

const finishInstall = async () => {
  fireEvent.click(button("Install Claude Code"));
  await flush();
  b.terminalState.mockResolvedValue("finished");
  await tick(1000);
};

test("install moves to sign-in when the agent is found", async () => {
  await open();
  b.setupStatus.mockResolvedValue(status(true, false));
  await finishInstall();
  expect(b.setupStatus).toHaveBeenCalled();
  expect(b.signinAgent).toHaveBeenCalledWith("claude");
  screen.getByText("Sign in to Claude Code");
  expect(screen.getByTestId("terminal").textContent).toBe("assieme:t8:typing");
});

test("a terminal that ends is not enough: the agent must be found", async () => {
  await open();
  await finishInstall(); // status still says not installed
  expect(b.signinAgent).not.toHaveBeenCalled();
});

test("install moves to failed when it finished without the agent", async () => {
  const close = await open();
  await finishInstall();
  screen.getByText("Claude Code didn’t install");
  screen.getByText("Check your internet, then try again. The details are below.");
  expect(screen.getByTestId("terminal")).toBeTruthy();
  fireEvent.click(button("Try again"));
  await flush();
  expect(b.installAgent).toHaveBeenCalledTimes(2);
  screen.getByText("Installing Claude Code");
  b.terminalState.mockResolvedValue("finished");
  await tick(1000);
  fireEvent.click(button("Close"));
  expect(close).toHaveBeenCalled();
});

test("signed in already skips the sign-in step", async () => {
  const close = await open();
  b.setupStatus.mockResolvedValue(status(true, true));
  await finishInstall();
  expect(b.signinAgent).not.toHaveBeenCalled();
  expect(close).toHaveBeenCalled();
});

test("the terminal is only asked while it runs, one question at a time", async () => {
  await open();
  fireEvent.click(button("Install Claude Code"));
  await flush();
  b.terminalState.mockReturnValue(new Promise(() => {}));
  await tick(10000);
  expect(b.terminalState).toHaveBeenCalledTimes(1);
});

test("a terminal that can't be checked says so", async () => {
  await open();
  fireEvent.click(button("Install Claude Code"));
  await flush();
  b.terminalState.mockRejectedValue(new Error("gone quiet"));
  await tick(1000);
  screen.getByText(/gone quiet/);
});

test("Sign in on the checklist opens the sheet at sign-in", async () => {
  await open("signin", status(true, false));
  expect(b.signinAgent).toHaveBeenCalledWith("claude");
  expect(screen.queryByText("Install Claude Code?")).toBeNull();
  screen.getByText("Sign in to Claude Code");
  screen.getByText("A browser window will open. Sign in, then come back here. If it shows a code, paste it below.");
  expect(screen.getByTestId("terminal").textContent).toBe("assieme:t8:typing");
});

test("a failed sign-in start shows why and can be tried again", async () => {
  b.signinAgent.mockRejectedValueOnce(new Error("not installed yet"));
  await open("signin", status(true, false));
  screen.getByText(/not installed yet/);
  fireEvent.click(button("Try again"));
  await flush();
  expect(screen.getByTestId("terminal")).toBeTruthy();
});

test("Done stays disabled until signed in; Skip for now closes and stops the terminal", async () => {
  const close = vi.fn();
  const { rerender } = render(<InstallSheet agent="claude" start="signin" status={status(true, false)} onClose={close} pollMs={1000} />);
  await flush();
  screen.getByText("Waiting for sign-in…");
  expect(button("Done").disabled).toBe(true);
  rerender(<InstallSheet agent="claude" start="signin" status={status(true, true)} onClose={close} pollMs={1000} />);
  expect(screen.queryByText("Waiting for sign-in…")).toBeNull();
  expect(button("Done").disabled).toBe(false);
  rerender(<InstallSheet agent="claude" start="signin" status={status(true, false)} onClose={close} pollMs={1000} />);
  fireEvent.click(button("Skip for now"));
  await flush();
  expect(b.installStop).toHaveBeenCalledWith("t8");
  expect(close).toHaveBeenCalledTimes(1);
});

test("Done closes and stops the sign-in terminal", async () => {
  const close = await open("signin", status(true, true));
  fireEvent.click(button("Done"));
  await flush();
  expect(b.installStop).toHaveBeenCalledWith("t8");
  expect(close).toHaveBeenCalled();
});
