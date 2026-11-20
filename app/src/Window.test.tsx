import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { TeamMap } from "./bridge";
import { MAP, Q, fixRow, pane } from "./windowFixtures";

const b = vi.hoisted(() => ({ teamMap: vi.fn(), reviewWaiting: vi.fn() }));
vi.mock("./bridge", () => b);
// The panels have their own tests; here each one only says which it is.
vi.mock("./AgentPanel", () => ({ default: ({ pane }: { pane: { role: string } }) => (
  <div data-testid="panel-agent">{pane.role}<div data-terminal tabIndex={-1} data-testid="term" /></div>) }));
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

const show = async (map: TeamMap, team = "shop") => {
  b.teamMap.mockResolvedValue(map);
  const props = { team, starting: [], open: null, onNewTeam: vi.fn(), onReview: vi.fn(), onNewTask: vi.fn(), boardKey: 0, pollMs: 100000 };
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
