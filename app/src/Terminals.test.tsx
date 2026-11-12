import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Pane, PaneButton, TeamView } from "./bridge";

const b = vi.hoisted(() => ({ teamView: vi.fn(), paneAct: vi.fn(), reviewWaiting: vi.fn(), openLink: vi.fn(), previewList: vi.fn((): Promise<unknown[]> => Promise.resolve([])), previewStart: vi.fn(), previewStop: vi.fn(), }));
vi.mock("./bridge", () => b);
vi.mock("./Terminal", () => ({
  default: ({ address, typing }: { address: string; typing?: boolean }) => (
    <div data-testid={`term-${address}`}>{typing ? "typing" : "watching"}</div>
  ),
}));

import Terminals from "./Terminals";

const btn = (id: string, label: string, kind: PaneButton["kind"]): PaneButton => ({ id, label, kind });
const P = (over: Partial<Pane>): Pane => ({
  role: "x", agent: "claude", address: "t0", state: "idle", since_s: 30, detail: "", live: true, fix: null, ...over,
});
const VIEW: TeamView = {
  team: "shop",
  panes: [
    P({ role: "planner", address: "t1", state: "asking", since_s: 720, detail: "Holding up 2 agents",
      fix: { id: "f1", text: "Flag or main?", buttons: [btn("b1", "Behind a flag", "answer"), btn("b2", "To main", "answer"), btn("b3", "Reply", "reply")] } }),
    P({ role: "builder-1", agent: "codex", address: "t2", state: "working", since_s: 5 }),
    P({ role: "builder-3", address: "t3", state: "stuck", since_s: 1080, detail: "Same failure 4 times",
      fix: { id: "f3", text: "stuck", buttons: [btn("b4", "Ask it to try another way", "action")] } }),
    P({ role: "perm", address: "t4", state: "asking", since_s: 60,
      fix: { id: "f4", text: "Create note.txt?", buttons: [btn("b5", "Yes", "menu"), btn("b6", "No", "menu"), btn("b7", "Reply", "reply")] } }),
    P({ role: "tester", address: "t5", state: "exited", live: false, detail: "Error: boom" }),
  ],
  strip: [
    { role: "planner", text: "planner is asking: flag or main?" },
    { role: "perm", text: "perm is asking: Create note.txt?" },
    { role: "builder-3", text: "builder-3 seems stuck: same failure" },
  ],
};

const flush = () => act(async () => {});
const onReview = vi.fn();

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  b.teamView.mockResolvedValue(VIEW);
  b.paneAct.mockResolvedValue({ ok: true, detail: "sent" });
  b.reviewWaiting.mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

async function show() {
  const onNewTeam = vi.fn();
  render(<Terminals team="shop" pollMs={3000} onNewTeam={onNewTeam} onReview={onReview} />);
  await flush();
  return onNewTeam;
}

test("grid shows every pane with its state", async () => {
  await show();
  const planner = screen.getByTestId("pane-planner");
  expect(within(planner).getByText("planner")).toBeTruthy();
  expect(within(planner).getByText("Claude Code")).toBeTruthy();
  expect(within(planner).getByText("asking you · 12m")).toBeTruthy();
  expect(within(screen.getByTestId("pane-builder-1")).getByText("Codex")).toBeTruthy();
  expect(within(screen.getByTestId("pane-builder-1")).getByText("working · now")).toBeTruthy();
  expect(within(screen.getByTestId("pane-builder-3")).getByText("seems stuck · 18m")).toBeTruthy();
  expect(within(planner).getByTestId("term-t1").textContent).toBe("watching");
});

test("strip lists who needs you and Review focuses the first", async () => {
  await show();
  expect(screen.getByText("3 need you")).toBeTruthy();
  expect(screen.getByText("planner is asking: flag or main?")).toBeTruthy();
  fireEvent.click(screen.getByText("Review"));
  expect(screen.getByText("‹ All agents")).toBeTruthy();
  expect(screen.getByTestId("term-t1").textContent).toBe("typing");
});

test("each button kind calls paneAct, disabled while pending", async () => {
  let done: (v: unknown) => void = () => {};
  b.paneAct.mockReturnValueOnce(new Promise((r) => (done = r)));
  await show();
  const flag = screen.getByText("Behind a flag") as HTMLButtonElement;
  fireEvent.click(flag);
  expect(b.paneAct).toHaveBeenCalledWith({ team: "shop", role: "planner", fix: "f1", button: "b1" });
  expect(flag.disabled).toBe(true);
  done({ ok: true, detail: "sent" });
  await flush();
  fireEvent.click(screen.getByText("Yes"));
  fireEvent.click(screen.getByText("Ask it to try another way"));
  await flush();
  expect(b.paneAct.mock.calls.map((c) => c[0].button)).toEqual(["b1", "b5", "b4"]);
});

test("a refused press shows its message", async () => {
  b.paneAct.mockResolvedValue({ ok: false, detail: "This question changed. Take a look." });
  await show();
  fireEvent.click(screen.getByText("Yes"));
  await flush();
  expect(within(screen.getByTestId("pane-perm")).getByText("This question changed. Take a look.")).toBeTruthy();
});

test("reply and clicking a pane open it full size ready to type; Esc and back return", async () => {
  await show();
  fireEvent.click(within(screen.getByTestId("pane-perm")).getByText("Reply"));
  expect(b.paneAct).not.toHaveBeenCalled();
  expect(screen.getByTestId("term-t4").textContent).toBe("typing");
  expect(screen.queryByTestId("pane-planner")).toBeNull();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.getByTestId("pane-planner")).toBeTruthy();
  fireEvent.click(within(screen.getByTestId("pane-builder-1")).getByText("builder-1"));
  expect(screen.getByTestId("term-t2").textContent).toBe("typing");
  fireEvent.click(screen.getByText("‹ All agents"));
  expect(screen.getByTestId("pane-builder-3")).toBeTruthy();
});

test("an exited pane has no buttons", async () => {
  await show();
  const p = screen.getByTestId("pane-tester");
  expect(within(p).getByText("exited")).toBeTruthy();
  expect(within(p).queryAllByRole("button").filter((x) => x.textContent !== "tester")).toEqual([]);
});

test("no team", async () => {
  const onNewTeam = vi.fn();
  render(<Terminals team={null} pollMs={3000} onNewTeam={onNewTeam} onReview={onReview} />);
  await flush();
  expect(screen.getByText("No team is running. Start one from New team.")).toBeTruthy();
  fireEvent.click(screen.getByText("New team"));
  expect(onNewTeam).toHaveBeenCalled();
  expect(b.teamView).not.toHaveBeenCalled();
});

test("polls one call at a time every 3 s", async () => {
  let answer: (v: unknown) => void = () => {};
  b.teamView.mockReturnValueOnce(new Promise((r) => (answer = r)));
  render(<Terminals team="shop" pollMs={3000} onNewTeam={() => {}} onReview={onReview} />);
  await flush();
  await act(async () => vi.advanceTimersByTime(9000));
  expect(b.teamView).toHaveBeenCalledTimes(1);
  answer(VIEW);
  await flush();
  await act(async () => vi.advanceTimersByTime(3000));
  expect(b.teamView).toHaveBeenCalledTimes(2);
});

test("a fix pressed once can't be pressed again until it changes; the view refreshes at once", async () => {
  await show();
  expect(b.teamView).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText("Ask it to try another way"));
  await flush();
  expect(b.teamView).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByText("Ask it to try another way"));
  await flush();
  expect(b.paneAct).toHaveBeenCalledTimes(1);
  expect((screen.getByText("Ask it to try another way") as HTMLButtonElement).disabled).toBe(true);
});

test("brain offline shows Reconnecting", async () => {
  b.teamView.mockRejectedValue("no brain");
  render(<Terminals team="shop" pollMs={3000} onNewTeam={() => {}} onReview={onReview} />);
  await flush();
  expect(screen.getByText("Reconnecting…")).toBeTruthy();
});

const WAITING = (role: string, team = "shop", ahead = 3) => ({ team, project: "checkout", role, branch: `assieme/${team}/${role}`, ahead });

test("an idle agent with commits offers Review changes; busy or asking ones don't", async () => {
  b.reviewWaiting.mockResolvedValue([WAITING("idle-one"), WAITING("planner"), WAITING("builder-1"), WAITING("elsewhere", "other")]);
  b.teamView.mockResolvedValue({ ...VIEW, panes: [...VIEW.panes, P({ role: "idle-one", address: "t6" }), P({ role: "elsewhere", address: "t7" })] });
  render(<Terminals team="shop" pollMs={1000} onNewTeam={() => {}} onReview={onReview} />);
  await flush();
  await flush();
  const pane = within(screen.getByTestId("pane-idle-one"));
  expect(pane.getByText("3 commits ready to review")).toBeTruthy();
  fireEvent.click(pane.getByRole("button", { name: "Review changes" }));
  expect(onReview).toHaveBeenCalledWith("shop", "idle-one");
  for (const role of ["planner", "builder-1", "elsewhere"])
    expect(within(screen.getByTestId(`pane-${role}`)).queryByText("Review changes")).toBeNull();
});

test("with no team running, work from a stopped team is still listed", async () => {
  b.reviewWaiting.mockResolvedValue([WAITING("builder-1"), WAITING("builder-2", "shop", 1)]);
  render(<Terminals team={null} pollMs={1000} onNewTeam={() => {}} onReview={onReview} />);
  await flush();
  expect(screen.getByText("Waiting for review")).toBeTruthy();
  expect(screen.getByText("checkout · 1 commit")).toBeTruthy();
  fireEvent.click(screen.getAllByRole("button", { name: "Review changes" })[1]);
  expect(onReview).toHaveBeenCalledWith("shop", "builder-2");
});

test("agents with no terminal to show say so, and work waiting is still listed", async () => {
  b.teamView.mockResolvedValue({ ...VIEW, panes: [] });
  b.reviewWaiting.mockResolvedValue([WAITING("builder-1")]);
  render(<Terminals team="found" pollMs={1000} onNewTeam={() => {}} onReview={onReview} />);
  await flush();
  expect(screen.getByText(/aren’t in terminals Assieme can reach/)).toBeTruthy();
  expect(screen.getByText("Waiting for review")).toBeTruthy();
});

test("a failed waiting list doesn't break the page", async () => {
  b.reviewWaiting.mockRejectedValue(new Error("offline"));
  render(<Terminals team={null} pollMs={1000} onNewTeam={() => {}} onReview={onReview} />);
  await flush();
  expect(screen.getByText(/No team is running/)).toBeTruthy();
  expect(screen.queryByText("Waiting for review")).toBeNull();
});

const PREVIEW = (role: string, more: object = {}) => ({ team: "shop", project: "checkout", role, state: "running", command: "npm run dev",
  url: "http://localhost:5175", port: 5175, reason: "", output: "", fix: null, ...more });

test("finished work can be previewed from its pane, and a running or failed preview shows at its foot", async () => {
  b.reviewWaiting.mockResolvedValue([WAITING("idle-one"), WAITING("idle-two"), WAITING("idle-three")]);
  b.teamView.mockResolvedValue({ ...VIEW, panes: [P({ role: "idle-one", address: "t6" }), P({ role: "idle-two", address: "t7" }),
    P({ role: "idle-three", address: "t8" })] });
  b.previewList.mockResolvedValue([PREVIEW("idle-two"), PREVIEW("idle-three", { state: "failed", url: null, reason: "It stopped." })]);
  b.previewStart.mockResolvedValue(PREVIEW("idle-one", { state: "starting", url: null }));
  render(<Terminals team="shop" pollMs={1000} onNewTeam={() => {}} onReview={onReview} />);
  await flush();
  await flush();
  fireEvent.click(within(screen.getByTestId("pane-idle-one")).getByRole("button", { name: "Preview" }));
  await flush();
  expect(b.previewStart).toHaveBeenCalledWith("shop", "idle-one", null, false, null);
  const two = within(screen.getByTestId("pane-idle-two"));
  expect(two.getByText("localhost:5175")).toBeTruthy();
  fireEvent.click(two.getByRole("button", { name: "Open preview" }));
  expect(b.openLink).toHaveBeenCalledWith("http://localhost:5175");
  expect(two.getByRole("button", { name: "Review changes" })).toBeTruthy();
  const three = within(screen.getByTestId("pane-idle-three"));
  expect(three.getByText("The preview didn't start")).toBeTruthy();
  fireEvent.click(three.getByRole("button", { name: "See why" }));
  expect(onReview).toHaveBeenCalledWith("shop", "idle-three");
});
