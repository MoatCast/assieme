import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Snapshot } from "./bridge";

const snapshot = vi.fn<() => Promise<Snapshot>>();
vi.mock("./bridge", () => ({ snapshot: () => snapshot() }));
vi.mock("./ChangeGroups", () => ({ default: () => <div>change the groups sheet</div> }));
vi.mock("./NewTeam", () => ({
  default: ({ clis, onClose }: { clis: { codex: boolean }; onClose: () => void }) => (
    <div>
      <h2>New team screen</h2>
      <span>{clis.codex ? "codex on" : "codex off"}</span>
      <button type="button" onClick={onClose}>close new team</button>
    </div>
  ),
}));
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
  starting: [],
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

test("one snapshot call at a time", async () => {
  snapshot.mockReturnValue(new Promise(() => {}));
  render(<FirstRun pollMs={5000} />);
  await flush();
  await act(async () => {
    vi.advanceTimersByTime(20000);
  });
  expect(snapshot).toHaveBeenCalledTimes(1);
});

test("new team opens from the header and closes", async () => {
  snapshot.mockResolvedValue(SNAP);
  render(<FirstRun pollMs={5000} />);
  await flush();
  fireEvent.click(screen.getByText("New team"));
  expect(screen.getByText("New team screen")).toBeTruthy();
  expect(screen.getByText("codex off")).toBeTruthy();
  fireEvent.click(screen.getByText("close new team"));
  expect(screen.queryByText("New team screen")).toBeNull();
});

const STARTING = (over: Partial<Snapshot["starting"][number]>) => ({
  ...SNAP,
  starting: [{ team: "shop", project: "/Users/me/shop", done: false, error: null, roles: [
    { role: "builder", state: "starting", detail: "" },
    { role: "reviewer", state: "needs-you", detail: "codex wants you to review its hooks once: hc attach t2" },
  ], ...over }],
});

test("a starting team shows its roles", async () => {
  snapshot.mockResolvedValue(STARTING({}));
  render(<FirstRun pollMs={5000} />);
  await flush();
  const card = screen.getByTestId("starting-shop");
  expect(within(card).getByText("shop")).toBeTruthy();
  expect(within(card).getByText("builder")).toBeTruthy();
  expect(within(card).getByText("Starting…")).toBeTruthy();
  expect(within(card).getByText("Needs you")).toBeTruthy();
  expect(within(card).getByText(/review its hooks once/)).toBeTruthy();
});

test("a start error shows on the team", async () => {
  snapshot.mockResolvedValue(STARTING({ done: true, error: "boom" }));
  render(<FirstRun pollMs={5000} />);
  await flush();
  expect(within(screen.getByTestId("starting-shop")).getByText("Couldn't start: boom")).toBeTruthy();
});

test("done teams without errors fall back to the live list", async () => {
  snapshot.mockResolvedValue(STARTING({ done: true, roles: [
    { role: "builder", state: "running", detail: "" },
    { role: "reviewer", state: "already-running", detail: "" },
  ] }));
  render(<FirstRun pollMs={5000} />);
  await flush();
  expect(screen.queryByTestId("starting-shop")).toBeNull();
});

test("a finished start keeps roles that need you or quit", async () => {
  snapshot.mockResolvedValue(STARTING({ done: true, roles: [
    { role: "builder", state: "running", detail: "" },
    { role: "reviewer", state: "needs-you", detail: "codex wants you to review its hooks once: hc attach t2" },
  ] }));
  render(<FirstRun pollMs={5000} />);
  await flush();
  const card = screen.getByTestId("starting-shop");
  expect(within(card).getByText("Running")).toBeTruthy();
  expect(within(card).getByText(/review its hooks once/)).toBeTruthy();
});

test("Open my team and Change the groups", async () => {
  snapshot.mockResolvedValue(SNAP);
  const openTeam = vi.fn();
  render(<FirstRun pollMs={100000} onOpenTeam={openTeam} />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "Open my team" }));
  expect(openTeam).toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Change the groups" }));
  expect(screen.getByText("change the groups sheet")).toBeTruthy();
});

test("a person's own group wins over the folder name", async () => {
  snapshot.mockResolvedValue({ ...SNAP, agents: [{ ...agent("a", "/x/one"), group: "mine" }, { ...agent("b", "/x/two"), group: "mine" }] });
  render(<FirstRun pollMs={100000} />);
  await act(async () => {});
  expect(within(screen.getByTestId("project-mine")).getByText("a")).toBeTruthy();
  expect(within(screen.getByTestId("project-mine")).getByText("b")).toBeTruthy();
});

test("an agent of a running team isn't found a second time by its folder", async () => {
  snapshot.mockResolvedValue({ ...SNAP, agents: [{ ...agent("kid", "/p/wt/kid"), group: "p" }, agent("solo", "/x/solo")],
    teams: [{ team: "p", role: "kid", address: "t2", state: "live", status: "idle", briefed: true, cwd: "/p/wt/kid" }] });
  render(<FirstRun pollMs={100000} />);
  await act(async () => {});
  expect(screen.queryByTestId("project-p")).toBeNull();
  expect(screen.getByTestId("team-p")).toBeTruthy();
  expect(screen.getByTestId("project-solo")).toBeTruthy();
});
