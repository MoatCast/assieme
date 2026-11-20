import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import AgentList, { type Selection } from "./AgentList";
import { listModel } from "./listModel";
import { MAP, Q, fixRow, pane, waitingFor } from "./windowFixtures";

afterEach(cleanup);

const VIEWS = { overview: "2 fixed today", terminals: "6 running", board: "5 open tasks", activity: "Last at 14:22" };
const MODEL = listModel(MAP({
  panes: [pane("planner", { state: "asking", since_s: 720, fix: Q("Flag or main?") }), pane("builder-3", { state: "stuck" }),
    pane("builder-1", { detail: "Editing retry.py" }), pane("tester", { state: "waiting" })],
  done: [fixRow("builder-3", "checking", "14:22")], fixes_today: { fixed: 2, checking: 1 },
}), [waitingFor("tester", 3)], [], {}, new Date(2026, 10, 20, 14, 26));

const show = (selected: Selection = { kind: "agent", role: "planner" }, model = MODEL) => {
  const onSelect = vi.fn();
  render(<AgentList model={model} selected={selected} views={VIEWS} onSelect={onSelect} />);
  return onSelect;
};

test("draws the heading and the three sections", () => {
  show();
  expect(screen.getByText("1 agent needs you")).toBeTruthy();
  expect(screen.getByText("2 working · 1 to review · 1 fix being checked")).toBeTruthy();
  for (const s of ["Needs you", "Working", "Done"]) expect(screen.getByText(s)).toBeTruthy();
  expect(screen.getByText("Asking: flag or main?")).toBeTruthy();
  expect(screen.getByText("Assieme is checking its fix")).toBeTruthy();
  expect(screen.getByText("3 commits to review")).toBeTruthy();
});

test("clicking a row selects its agent", () => {
  const onSelect = show();
  fireEvent.click(screen.getByRole("button", { name: /builder-1, working/ }));
  expect(onSelect).toHaveBeenCalledWith({ kind: "agent", role: "builder-1" });
});

test("Assieme's line opens Team overview", () => {
  const onSelect = show();
  fireEvent.click(screen.getByRole("button", { name: "Assieme fixed 2 problems today ›" }));
  expect(onSelect).toHaveBeenCalledWith({ kind: "view", view: "overview" });
});

test("the selected row says so", () => {
  show();
  expect(screen.getByRole("button", { name: "planner, needs you, asking: flag or main?, 12 minutes" }).getAttribute("aria-current")).toBe("true");
  expect(screen.getByRole("button", { name: /builder-1/ }).getAttribute("aria-current")).toBeNull();
});

test("the views stay outside the scrolling sections, with their counts", () => {
  const panes = Array.from({ length: 14 }, (_, i) => pane(`builder-${i}`));
  show({ kind: "view", view: "board" }, listModel(MAP({ panes }), [], [], {}));
  const sections = screen.getByTestId("agent-sections");
  const overview = screen.getByRole("button", { name: /Team overview/ });
  expect(sections.contains(overview)).toBe(false);
  expect(within(overview).getByText("2 fixed today")).toBeTruthy();
  expect(screen.getByRole("button", { name: /Board/ }).getAttribute("aria-current")).toBe("true");
});

test("a group opens to its agents", () => {
  const groups = [{ name: "frontend", roles: ["f1", "f2"], states: {}, need_you: 0, edges: [] }];
  show({ kind: "view", view: "overview" }, listModel(MAP({ groups, panes: [pane("f1"), pane("f2")] }), [], [], {}));
  expect(screen.queryByRole("button", { name: /f1, working/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /frontend/ }));
  expect(screen.getByRole("button", { name: /f1, working/ })).toBeTruthy();
});
