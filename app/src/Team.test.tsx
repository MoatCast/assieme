import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Pane, TeamMap } from "./bridge";

const b = vi.hoisted(() => ({
  teamMap: vi.fn(), reviewWaiting: vi.fn(), paneAct: vi.fn(), tipApply: vi.fn(), activityUndo: vi.fn(), agentDetail: vi.fn(),
}));
vi.mock("./bridge", () => b);
vi.mock("./Terminal", () => ({ default: () => <div /> }));

import Team, { tokens } from "./Team";
import { layout } from "./TeamMap";

const pane = (role: string, over: Partial<Pane> = {}): Pane => ({
  role, agent: "claude", address: `t-${role}`, state: "working", since_s: 120, detail: "", live: true, fix: null, reachable: true, ...over,
});
const node = (role: string, state: Pane["state"] = "working", parent: string | null = null) =>
  ({ role, state, since_s: 120, agent: "claude" as const, parent, group: null });
const MAP = (over: Partial<TeamMap> = {}): TeamMap => ({
  team: "shop", found: false, stopped: false, title: "All quiet", subtitle: "Two working.", panes: [pane("a"), pane("b")], strip: [],
  nodes: [node("a"), node("b")], edges: [], waits: [], groups: null, tips: [], done: [], spend: null, ...over,
});
const FIX = { id: "f1", text: "Ship behind a flag?", buttons: [
  { id: "b1", label: "Behind a flag", kind: "answer" as const }, { id: "b2", label: "To main", kind: "answer" as const }, { id: "b3", label: "Reply", kind: "reply" as const },
] };

const flush = () => act(async () => {});
const open = async (map: TeamMap, props: Partial<Parameters<typeof Team>[0]> = {}) => {
  b.teamMap.mockResolvedValue(map);
  const calls = { term: vi.fn(), all: vi.fn(), neu: vi.fn(), review: vi.fn() };
  render(<Team team="shop" pollMs={100000} onNewTeam={calls.neu} onOpenTerminal={calls.term} onSeeAll={calls.all} onReview={calls.review} {...props} />);
  await flush();
  return calls;
};

beforeEach(() => {
  vi.resetAllMocks();
  b.reviewWaiting.mockResolvedValue([]);
  b.paneAct.mockResolvedValue({ ok: true, detail: "" });
  b.tipApply.mockResolvedValue({ ok: true, detail: "" });
  b.activityUndo.mockResolvedValue({ ok: true, detail: "" });
});
afterEach(cleanup);

test("layout places every box without overlap, with real dagre", () => {
  const boxes = ["a", "b", "c", "d"].map((id) => ({ id, width: 150, height: 54 }));
  const p = layout(boxes, [{ from: "a", to: "b" }, { from: "a", to: "c" }, { from: "c", to: "d" }]);
  const placed = Object.values(p.boxes);
  for (let i = 0; i < placed.length; i++)
    for (let j = i + 1; j < placed.length; j++) {
      const [x, y] = [placed[i], placed[j]];
      expect(x.x + x.width <= y.x || y.x + y.width <= x.x || x.y + x.height <= y.y || y.y + y.height <= x.y).toBe(true);
    }
  expect(p.lines["a>b"].points.length).toBeGreaterThan(1);
});

test("a wake-up cycle still lays out", () => {
  const p = layout([{ id: "a", width: 150, height: 54 }, { id: "b", width: 150, height: 54 }], [{ from: "a", to: "b" }, { from: "b", to: "a" }]);
  expect(p.lines["a>b"].points.length).toBeGreaterThan(1);
  expect(p.lines["b>a"].points.length).toBeGreaterThan(1);
});

test("reports-to links shape the layout but aren't drawn", async () => {
  await open(MAP({ nodes: [node("a"), node("b", "working", "a")] }));
  const [a, c] = [screen.getByTestId("node-a"), screen.getByTestId("node-b")];
  expect(parseFloat(c.style.top)).toBeGreaterThan(parseFloat(a.style.top));
  expect(screen.queryByTestId("edge-a-b")).toBeNull();
});

test("title, subtitle, and nothing waiting", async () => {
  await open(MAP());
  expect(screen.getByRole("heading", { name: "All quiet" })).toBeTruthy();
  expect(screen.getByText("Two working.")).toBeTruthy();
  expect(screen.getByText("Nothing waiting on you")).toBeTruthy();
});

test("edges are labelled; dashed waits and the amber callout show", async () => {
  await open(MAP({
    edges: [{ from: "a", to: "b", wakes: 6, empty: 0, noisy: null }, { from: "b", to: "a", wakes: 41, empty: 30, noisy: { per_hour: 41 } }],
    waits: [{ from: "a", to: "b" }],
  }));
  expect(screen.getByTestId("edge-a-b").textContent).toBe("6");
  expect(screen.getByText("41 wake-ups an hour")).toBeTruthy();
  expect(screen.getByTestId("wait-a>b").getAttribute("stroke-dasharray")).toBeTruthy();
  expect(screen.getByText("waiting on b")).toBeTruthy();
  expect(screen.getAllByText("waiting on").length).toBe(1);
});

test("a card's button acts once, and Write a reply opens the terminal", async () => {
  const calls = await open(MAP({ panes: [pane("a", { state: "asking", fix: FIX, detail: "Holding up 2 agents" })], title: "One agent needs you" }));
  expect(screen.getByText("“Ship behind a flag?”")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Behind a flag" }));
  await flush();
  expect(b.paneAct).toHaveBeenCalledWith({ team: "shop", role: "a", fix: "f1", button: "b1" });
  fireEvent.click(screen.getByRole("button", { name: "To main" }));
  await flush();
  expect(b.paneAct).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Write a reply" }));
  expect(calls.term).toHaveBeenCalledWith("a");
});

test("a stale button shows the brain's message", async () => {
  b.paneAct.mockResolvedValue({ ok: false, detail: "This question changed. Take a look." });
  await open(MAP({ panes: [pane("a", { state: "asking", fix: FIX })] }));
  fireEvent.click(screen.getByRole("button", { name: "To main" }));
  await flush();
  expect(screen.getByText("This question changed. Take a look.")).toBeTruthy();
});

test("a stuck card shows its command and Open terminal", async () => {
  const fix = { id: "f", text: "stuck", buttons: [{ id: "n", label: "Ask it to try another way", kind: "action" as const }] };
  const calls = await open(MAP({ panes: [pane("a", { state: "stuck", detail: "Same command x4", command: "pytest", fix })] }));
  expect(screen.getByText("pytest")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Open terminal" }));
  expect(calls.term).toHaveBeenCalledWith("a");
});

test("an agent Assieme can't reach says so and offers no terminal", async () => {
  const fix = { id: "f", text: "stuck", buttons: [] };
  await open(MAP({ found: true, panes: [pane("a", { state: "stuck", detail: "Silent", fix, reachable: false })] }));
  expect(screen.getByText(/isn’t in a terminal Assieme can reach/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open terminal" })).toBeNull();
});

test("Turn on calls the brain", async () => {
  await open(MAP({ tips: [{ id: "a>b", from: "a", to: "b", share: 0.12 }] }));
  expect(screen.getByText("Save about 12%.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Turn on" }));
  await flush();
  expect(b.tipApply).toHaveBeenCalledWith("shop", "a>b");
});

test("work ready to review shows, this team first, and opens Review", async () => {
  b.reviewWaiting.mockResolvedValue([
    { team: "other", project: "p", role: "elsewhere", branch: "y", ahead: 1 },
    { team: "shop", project: "p", role: "tester", branch: "x", ahead: 3 },
  ]);
  const calls = await open(MAP());
  expect(screen.getByText("Ready to review")).toBeTruthy();
  expect(screen.getByText("3 commits")).toBeTruthy();
  expect(screen.getByText("other · 1 commit")).toBeTruthy(); // another team's, or nothing would lead to a stopped team's work
  fireEvent.click(screen.getAllByRole("button", { name: "Review changes" })[0]);
  expect(calls.review).toHaveBeenCalledWith("shop", "tester");
});

test("nothing ready to review shows no card, and a failed check doesn't take the map down", async () => {
  b.reviewWaiting.mockRejectedValue(new Error("no"));
  await open(MAP());
  expect(screen.queryByText("Ready to review")).toBeNull();
  expect(screen.getByText("All quiet")).toBeTruthy();
});

test("no spend line claim before the first read of the logs", async () => {
  await open(MAP({ spend_ready: false }));
  expect(screen.queryByText("No tokens used yet today.")).toBeNull();
});

test("the spend line, with and without tokens", async () => {
  await open(MAP({ spend: { tokens: 4_200_000, waste_share: 0.3 } }));
  expect(screen.getByText("4.2M tokens")).toBeTruthy();
  expect(screen.getByText("30%")).toBeTruthy();
  cleanup();
  await open(MAP());
  expect(screen.getByText("No tokens used yet today.")).toBeTruthy();
  expect(tokens(1500)).toBe("2K");
});

test("Done for you: Undo where it applies, none otherwise, and See all", async () => {
  const row = (id: string, title: string, undo: { label: string } | null) =>
    ({ id, at: "14:02", day: "2026-11-08", title, reason: "", by: "you", ok: true, action: "x", undo });
  const calls = await open(MAP({ done: [row("1", "Freed up memory for tester", null), row("2", "Started holding notes from a to b", { label: "Stop" })] }));
  expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Stop" }));
  await flush();
  expect(b.activityUndo).toHaveBeenCalledWith("2");
  fireEvent.click(screen.getByRole("button", { name: "See all" }));
  expect(calls.all).toHaveBeenCalled();
});

test("groups: the chevron shows chips, Open the group redraws, All groups returns", async () => {
  const roles = ["p-1", "p-2"];
  await open(MAP({
    title: "One agent needs you",
    nodes: [node("p-1", "asking"), node("p-2"), node("q-1")],
    edges: [{ from: "p-1", to: "p-2", wakes: 4, empty: 0, noisy: null }],
    groups: [
      { name: "p", roles, states: { "p-1": "asking", "p-2": "working" }, need_you: 1, edges: [{ to: "q", wakes: 12, noisy: null }] },
      { name: "q", roles: ["q-1"], states: { "q-1": "working" }, need_you: 0, edges: [] },
    ],
  }));
  expect(screen.getByTestId("group-p").textContent).toContain("1 needs you");
  expect(screen.getByTestId("edge-p-q").textContent).toBe("12");
  expect(screen.getByText("all working")).toBeTruthy();
  expect(screen.queryByText("p-1")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Show p" }));
  expect(screen.getByText("p-1")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Open the group/ }));
  expect(screen.getByRole("heading", { name: "p: one agent needs you" })).toBeTruthy();
  expect(screen.getByTestId("node-p-1")).toBeTruthy();
  expect(screen.queryByTestId("node-q-1")).toBeNull();
  expect(screen.getByTestId("edge-p-1-p-2")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "‹ All groups" }));
  expect(screen.getByTestId("group-q")).toBeTruthy();
});

test("a stopped team offers New team, with no stale buttons", async () => {
  const calls = await open(MAP({ stopped: true, panes: [pane("a", { state: "exited", fix: FIX })] }));
  expect(screen.getByText(/shop has stopped/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "To main" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "New team" }));
  expect(calls.neu).toHaveBeenCalled();
});

test("no team at all offers New team", async () => {
  render(<Team team={null} onNewTeam={vi.fn()} onOpenTerminal={vi.fn()} onSeeAll={vi.fn()} onReview={vi.fn()} />);
  await flush();
  expect(screen.getByText(/No team is running/)).toBeTruthy();
  expect(b.teamMap).not.toHaveBeenCalled();
});

test("offline keeps the last map", async () => {
  vi.useFakeTimers();
  try {
    b.teamMap.mockResolvedValueOnce(MAP()).mockRejectedValue(new Error("down"));
    render(<Team team="shop" pollMs={1000} onNewTeam={vi.fn()} onOpenTerminal={vi.fn()} onSeeAll={vi.fn()} onReview={vi.fn()} />);
    await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    expect(screen.getByText("Reconnecting…")).toBeTruthy();
    expect(screen.getByTestId("node-a")).toBeTruthy();
  } finally {
    vi.useRealTimers();
  }
});

test("a failed add shows on the Team tab", async () => {
  await open(MAP(), { starting: [{ team: "shop", project: "/p", done: true, error: "no git", roles: [{ role: "x", state: "starting", detail: "" }] }] });
  expect(screen.getByText("Couldn’t add the agent: no git")).toBeTruthy();
});

test("clicking a node opens its detail", async () => {
  b.agentDetail.mockResolvedValue({ role: "a", cli: "codex", job: "fixes tests", state: "working", since_s: 5, detail: "", fix: null, reachable: true, command: null, memory: null, recent: [], wakers: [], empty_wakes: 0 });
  await open(MAP());
  fireEvent.click(screen.getByTestId("node-a"));
  await flush();
  expect(screen.getByText("Codex · fixes tests")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByText("Codex · fixes tests")).toBeNull();
});

test("an added agent that stopped at its hook review says so on the Team tab", async () => {
  await open(MAP(), { starting: [{ team: "shop", project: "/p", done: true, error: null, roles: [{ role: "x", state: "needs-you", detail: "codex wants you to review its hooks once" }] }] });
  expect(screen.getByText("x needs you: codex wants you to review its hooks once")).toBeTruthy();
  cleanup();
  await open(MAP(), { starting: [{ team: "shop", project: "/p", done: true, error: null, roles: [{ role: "y", state: "running", detail: "" }] }] });
  expect(screen.queryByText(/y needs you/)).toBeNull();
  cleanup();
  const gone = { team: "shop", project: "/p", done: true, error: null, roles: [{ role: "b", state: "needs-you", detail: "review" }] };
  await open(MAP({ panes: [pane("a"), pane("b", { state: "exited" })] }), { starting: [gone] });
  expect(screen.queryByText(/b needs you/)).toBeNull();
  cleanup();
  await open(MAP(), { starting: [{ team: "shop", project: "/p", done: false, error: null, roles: [{ role: "z", state: "starting", detail: "" }] }] });
  expect(screen.getByText("Starting z…")).toBeTruthy();
});
