import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Snapshot } from "./bridge";

const snapshot = vi.fn<() => Promise<Snapshot>>();
vi.mock("./bridge", () => ({ snapshot: () => snapshot() }));
vi.mock("./Terminal", () => ({
  default: ({ session, address }: { session: string; address: string }) => (
    <div data-testid="terminal">{`${session}:${address}`}</div>
  ),
}));

import FirstRun from "./FirstRun";

const agent = (name: string, cwd: string, source = "claude", herdr_address: string | null = null) => ({
  agent: name, source, state: "idle", detail: "", seconds: 5, herdr: null, cwd, herdr_address,
});

const SNAP: Snapshot = {
  at: "2026-11-07T12:00:00Z",
  clis: { claude: true, codex: false, hc: true, herdr: true },
  agents: [
    agent("planner", "/Users/me/code/checkout-service", "claude", "t4"),
    agent("builder", "/Users/me/code/checkout-service", "codex"),
    agent("writer", "/Users/me/code/docs-site"),
  ],
  alerts: [], queue: [], stalls: [],
  teams: [{ team: "demo", role: "coordinator", address: "t3", state: "live", status: "idle", briefed: true }],
};

const flush = () => act(async () => {});

beforeEach(() => {
  vi.useFakeTimers();
  snapshot.mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

test("shows detected CLIs", async () => {
  snapshot.mockResolvedValue(SNAP);
  render(<FirstRun pollMs={5000} />);
  await flush();
  expect(within(screen.getByTestId("cli-claude")).getByText("2 agents")).toBeTruthy();
  expect(within(screen.getByTestId("cli-codex")).getByText("not found")).toBeTruthy();
});

test("groups agents by project folder", async () => {
  snapshot.mockResolvedValue(SNAP);
  render(<FirstRun pollMs={5000} />);
  await flush();
  const card = screen.getByTestId("project-checkout-service");
  expect(within(card).getByText("2 agents")).toBeTruthy();
  expect(within(card).getByText("planner")).toBeTruthy();
  expect(within(card).getByText("builder")).toBeTruthy();
  expect(within(screen.getByTestId("project-docs-site")).getByText("1 agent, working alone")).toBeTruthy();
});

test("herdr agent opens read-only", async () => {
  snapshot.mockResolvedValue(SNAP);
  render(<FirstRun pollMs={5000} />);
  await flush();
  fireEvent.click(screen.getByText("planner"));
  expect(screen.getByTestId("terminal").textContent).toBe("default:t4");
});

test("agent without a terminal is not clickable", async () => {
  snapshot.mockResolvedValue(SNAP);
  render(<FirstRun pollMs={5000} />);
  await flush();
  expect(screen.getByText("builder").closest("button")).toBeNull();
});

test("assieme team agent opens live", async () => {
  snapshot.mockResolvedValue(SNAP);
  render(<FirstRun pollMs={5000} />);
  await flush();
  fireEvent.click(within(screen.getByTestId("team-demo")).getByText("coordinator"));
  expect(screen.getByTestId("terminal").textContent).toBe("assieme:t3");
});

test("reconnecting then recovers", async () => {
  snapshot.mockRejectedValueOnce(new Error("no brain")).mockResolvedValue(SNAP);
  render(<FirstRun pollMs={5000} />);
  await flush();
  expect(screen.getByText("Reconnecting…")).toBeTruthy();
  await act(async () => {
    vi.advanceTimersByTime(5000);
  });
  expect(screen.queryByText("Reconnecting…")).toBeNull();
  expect(screen.getByTestId("project-checkout-service")).toBeTruthy();
});
