import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const b = vi.hoisted(() => ({ connectAntigravity: vi.fn(), setupStatus: vi.fn(), terminalState: vi.fn(), installStop: vi.fn() }));
vi.mock("./bridge", () => b);
vi.mock("./Terminal", () => ({ default: ({ address }: { address: string }) => <div data-testid="terminal">{address}</div> }));

import ConnectSheet from "./ConnectSheet";

const flush = () => act(async () => {});
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const status = (connected: boolean) => ({ git: true, agents: { antigravity: { installed: true, signed_in: true, connected } } });

beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

test("asks first, with the exact command; Not now changes nothing", async () => {
  const onClose = vi.fn();
  render(<ConnectSheet onConnected={() => {}} onClose={onClose} />);
  screen.getByRole("heading", { name: "Connect Assieme to Antigravity?" });
  screen.getByText("agy plugin install ~/.local/state/assieme/agy-plugin");
  fireEvent.click(screen.getByRole("button", { name: "Not now" }));
  expect(onClose).toHaveBeenCalled();
  expect(b.connectAntigravity).not.toHaveBeenCalled();
});

test("Connect runs it in a terminal you can see, and a fresh status saying connected finishes", async () => {
  const onConnected = vi.fn();
  b.connectAntigravity.mockResolvedValue({ address: "t9" });
  b.terminalState.mockResolvedValue("finished");
  b.setupStatus.mockResolvedValue(status(true));
  render(<ConnectSheet onConnected={onConnected} onClose={() => {}} pollMs={100} />);
  fireEvent.click(screen.getByRole("button", { name: "Connect Antigravity" }));
  await flush();
  expect(screen.getByTestId("terminal").textContent).toBe("t9");
  await tick(150);
  expect(onConnected).toHaveBeenCalled();
});

test("a plugin that didn't install says so, with Try again", async () => {
  b.connectAntigravity.mockResolvedValue({ address: "t9" });
  b.terminalState.mockResolvedValue("finished");
  b.setupStatus.mockResolvedValue(status(false));
  render(<ConnectSheet onConnected={() => {}} onClose={() => {}} pollMs={100} />);
  fireEvent.click(screen.getByRole("button", { name: "Connect Antigravity" }));
  await flush();
  await tick(150);
  screen.getByText(/didn’t connect/);
  screen.getByRole("button", { name: "Try again" });
});
