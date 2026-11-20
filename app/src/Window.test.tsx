import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { TeamMap } from "./bridge";
import { MAP, Q, fixRow, pane } from "./windowFixtures";

const b = vi.hoisted(() => ({ teamMap: vi.fn(), reviewWaiting: vi.fn() }));
vi.mock("./bridge", () => b);
// The panels have their own tests; here each one only says which it is.
vi.mock("./AgentPanel", () => ({ default: ({ team, pane, onReview }: { team: string; pane: { role: string }; onReview: (t: string, r: string) => void }) => (
  <><div data-testid="panel-agent">{pane.role}<div data-terminal tabIndex={-1} data-testid="term" /></div>
    <button type="button" onClick={() => onReview(team, pane.role)}>Review changes</button></>) }));
vi.mock("./Review", () => ({ default: ({ role, from, onBack, onMerged }: { role: string; from: string; onBack: () => void; onMerged: (m: object) => void }) => (
  <div data-testid="panel-review">review of {role} from {from}
    <button type="button" onClick={onBack}>back</button>
    <button type="button" onClick={() => onMerged({ role, base: "main", problem: null, undo: "u1" })}>merge</button></div>) }));
vi.mock("./TeamOverview", () => ({ default: () => <div data-testid="panel-overview" /> }));
vi.mock("./Terminals", () => ({ default: () => <div data-testid="panel-terminals" /> }));
vi.mock("./Board", () => ({ default: () => <div data-testid="panel-board" /> }));
vi.mock("./Activity", () => ({ default: () => <div data-testid="panel-activity" /> }));

import Window, { initialSelection } from "./Window";
import { listModel } from "./listModel";

const flush = () => act(async () => {});
const ASKING = MAP({ panes: [pane("planner", { state: "asking", since_s: 720, fix: Q("Flag or main?") }),
  pane("builder-4", { state: "asking", since_s: 10, fix: Q("Go?") }), pane("builder-1")] });
const QUIET = MAP({ panes: [pane("builder-1"), pane("tester", { state: "idle" })] });

const show = async (map: TeamMap, team: string | null = "shop") => {
  b.teamMap.mockResolvedValue(map);
  const props = { team: team as string | null, starting: [], open: null, onNewTeam: vi.fn(), onNewTask: vi.fn(), onMerged: vi.fn(), onDiscarded: vi.fn(),
    onBringOver: vi.fn(), boardKey: 0, pollMs: 100000 };
  const r = render(<Window {...props} />);
  await flush();
  return { ...r, props };
};
const selectedRow = () => document.querySelector(".agent-list [aria-current='true']");

beforeEach(() => {
  vi.resetAllMocks();
  b.reviewWaiting.mockResolvedValue([]);
});
afterEach(cleanup);

test("initialSelection: the longest wait, else Team overview", () => {
  expect(initialSelection(listModel(ASKING, [], [], {}))).toEqual({ kind: "agent", role: "planner" });
  expect(initialSelection(listModel(QUIET, [], [], {}))).toEqual({ kind: "view", view: "overview" });
});

test("opens on the agent that has waited longest", async () => {
  await show(ASKING);
  expect(screen.getByTestId("panel-agent").textContent).toBe("planner");
  expect(selectedRow()?.getAttribute("aria-label")).toMatch(/^planner,/);
});

test("opens on Team overview when nobody needs you", async () => {
  await show(QUIET);
  expect(screen.getByTestId("panel-overview")).toBeTruthy();
});

test("clicking a row shows its agent, and a view its panel", async () => {
  await show(QUIET);
  fireEvent.click(screen.getByRole("button", { name: /tester, done/ }));
  expect(screen.getByTestId("panel-agent").textContent).toBe("tester");
  fireEvent.click(screen.getByRole("button", { name: /Board/ }));
  expect(screen.getByTestId("panel-board")).toBeTruthy();
});

test("selection moves to Team overview when the agent stops", async () => {
  const { rerender, props } = await show(ASKING);
  b.teamMap.mockResolvedValue(MAP({ panes: [pane("planner", { state: "exited" }), pane("builder-1")] }));
  rerender(<Window {...props} pollMs={100001} />); // a new poll
  await flush();
  expect(screen.getByTestId("panel-overview")).toBeTruthy();
});

test("a team switch never keeps the old team's agent", async () => {
  const { rerender, props } = await show(MAP({ panes: [pane("builder-1"), pane("tester", { state: "idle" })] }));
  fireEvent.click(screen.getByRole("button", { name: /builder-1, working/ }));
  let answer: (m: TeamMap) => void = () => {};
  b.teamMap.mockReturnValue(new Promise<TeamMap>((r) => { answer = r; }));
  rerender(<Window {...props} team="other" />);
  await flush();
  expect(screen.queryByTestId("panel-agent")).toBeNull(); // the old team's map is gone, so is its agent
  await act(async () => answer(MAP({ team: "other", panes: [pane("builder-1"), pane("x", { state: "asking", since_s: 60, fix: Q("Go?") })] })));
  expect(screen.getByTestId("panel-agent").textContent).toBe("x");
});

test("↑ and ↓ move through the list", async () => {
  await show(QUIET);
  const row = screen.getByRole("button", { name: /builder-1, working/ });
  row.focus();
  fireEvent.keyDown(row, { key: "ArrowDown" });
  expect(document.activeElement?.getAttribute("aria-label")).toMatch(/^tester,/);
  expect(screen.getByTestId("panel-agent").textContent).toBe("tester");
  fireEvent.keyDown(document.activeElement!, { key: "ArrowUp" });
  expect(screen.getByTestId("panel-agent").textContent).toBe("builder-1");
});

test("⌘L returns from the terminal to the list; Escape stays in the terminal", async () => {
  await show(ASKING);
  const term = screen.getByTestId("term");
  term.focus();
  fireEvent.keyDown(term, { key: "Escape" });
  expect(document.activeElement).toBe(term);
  fireEvent.keyDown(term, { key: "l", metaKey: true });
  expect(document.activeElement).toBe(selectedRow());
});

test("Return on a row goes into the panel", async () => {
  await show(QUIET);
  const row = screen.getByRole("button", { name: /builder-1, working/ });
  row.focus();
  fireEvent.keyDown(row, { key: "Enter" });
  await flush();
  expect(document.activeElement).toBe(screen.getByTestId("term"));
});

test("⌘2 selects All terminals, ⌘4 Activity", async () => {
  await show(ASKING);
  fireEvent.keyDown(window, { key: "2", metaKey: true });
  expect(screen.getByTestId("panel-terminals")).toBeTruthy();
  fireEvent.keyDown(window, { key: "4", metaKey: true });
  expect(screen.getByTestId("panel-activity")).toBeTruthy();
});

test("announces a fix outcome politely", async () => {
  const { rerender, props } = await show(MAP({ panes: [pane("builder-3", { state: "stuck" })], done: [fixRow("builder-3", "checking", "14:22")] }));
  const now = new Date();
  const hhmm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  b.teamMap.mockResolvedValue(MAP({ panes: [pane("builder-3")], done: [fixRow("builder-3", "fixed", "14:22", { outcome_at: hhmm })] }));
  rerender(<Window {...props} pollMs={100001} />);
  await flush();
  const live = screen.getByRole("status");
  expect(live.getAttribute("aria-live")).toBe("polite");
  expect(live.textContent).toContain("builder-3: Fixed by Assieme");
  expect(screen.getByText(`✓ Fixed by Assieme · ${hhmm}`)).toBeTruthy();
});

test("a new needs-you row is announced", async () => {
  const { rerender, props } = await show(QUIET);
  b.teamMap.mockResolvedValue(MAP({ panes: [pane("builder-1", { state: "asking", fix: Q("Go?") }), pane("tester", { state: "idle" })] }));
  rerender(<Window {...props} pollMs={100001} />);
  await flush();
  expect(screen.getByRole("status").textContent).toContain("builder-1 needs you");
});

test("selecting a fixed row puts its line back to normal", async () => {
  const now = new Date();
  const hhmm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  await show(MAP({ panes: [pane("builder-3", { detail: "Editing" })], done: [fixRow("builder-3", "fixed", "14:22", { outcome_at: hhmm })] }));
  expect(screen.getByText(`✓ Fixed by Assieme · ${hhmm}`)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /builder-3/ }));
  expect(screen.queryByText(`✓ Fixed by Assieme · ${hhmm}`)).toBeNull();
});

test("Review opens in the panel; Back returns to the agent; a merge says so until the selection changes", async () => {
  b.reviewWaiting.mockResolvedValue([{ team: "shop", project: "/p", role: "tester", branch: "b", ahead: 3 }]);
  const { props } = await show(QUIET);
  fireEvent.click(screen.getByRole("button", { name: /tester, done/ }));
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  expect(screen.getByTestId("panel-review").textContent).toContain("review of tester from tester");
  fireEvent.click(screen.getByRole("button", { name: "back" }));
  expect(screen.getByTestId("panel-agent").textContent).toContain("tester");
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  fireEvent.click(screen.getByRole("button", { name: "merge" }));
  expect(props.onMerged).toHaveBeenCalled();
  expect(screen.getByText("Merged into main · 3 commits")).toBeTruthy();
  expect(screen.queryByTestId("panel-review")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /builder-1, working/ }));
  expect(screen.queryByText("Merged into main · 3 commits")).toBeNull();
});

test("no team running: the heading alone, and the panel offers to start one", async () => {
  b.reviewWaiting.mockResolvedValue([{ team: "website", project: "/w", role: "docs-writer", branch: "b", ahead: 2 }]);
  const { props } = await show(MAP(), null);
  expect(screen.getByText("No team is running")).toBeTruthy();
  expect(screen.getByText("Your agents show here once a team starts.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Team overview/ })).toBeNull();
  expect(screen.getByRole("heading", { name: "Start a team of agents" })).toBeTruthy();
  expect(screen.getByText(/fixes problems on its own while they work\.$/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "New team" }));
  expect(props.onNewTeam).toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Bring your team over" }));
  expect(props.onBringOver).toHaveBeenCalled();
  expect(screen.getByText("Ready to review from earlier teams")).toBeTruthy();
  expect(screen.getByText("website · 2 commits")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  expect(screen.getByTestId("panel-review").textContent).toContain("review of docs-writer");
});

test("a stopped team: the run in the heading, and the panel says when it stopped", async () => {
  await show(MAP({ stopped: true, panes: [pane("a", { state: "exited" }), pane("b", { state: "exited" })],
    run: { minutes: 192, fixed: 4, tasks_done: 3, stopped_at: "12:40" } }));
  expect(screen.getByText("The team has stopped")).toBeTruthy();
  expect(screen.getAllByText("Ran 3 h 12 min · Assieme fixed 4 problems · 3 tasks done")).toHaveLength(2); // the heading and the panel
  expect(screen.getByText("All 2 agents stopped at 12:40.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Open Activity" }));
  expect(screen.getByTestId("panel-activity")).toBeTruthy();
});

test("an unreachable machine keeps the rows, hollow, and says when it went", async () => {
  const away = { name: "Mac mini", target: "mini", state: "unreachable" as const, since_ms: new Date(2026, 10, 20, 14, 2).getTime() };
  await show(MAP({ unreachable: away, panes: [pane("a"), pane("b", { state: "idle" })] }));
  expect(screen.getByText("Can’t reach Mac mini")).toBeTruthy();
  expect(document.querySelectorAll(".agent-list .list-dot.stale")).toHaveLength(2);
  expect(screen.getByText("Since 14:02. Assieme keeps trying and reconnects as soon as Mac mini is back. Answers and other changes wait until then.")).toBeTruthy();
});

test("offline: the last map stays at full strength, and the panel says so", async () => {
  const { rerender, props } = await show(QUIET);
  b.teamMap.mockRejectedValue(new Error("down"));
  rerender(<Window {...props} pollMs={100001} />);
  await flush();
  expect(screen.getByRole("button", { name: /builder-1, working/ })).toBeTruthy();
  expect(screen.getByText("Reconnecting…")).toBeTruthy();
});
