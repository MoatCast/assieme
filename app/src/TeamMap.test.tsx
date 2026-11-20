// The map of who wakes whom (moved from the Team tab's tests; it now lives in Team overview).
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { MapNode, Pane } from "./bridge";

const b = vi.hoisted(() => ({ teamBand: vi.fn() }));
vi.mock("./bridge", () => b);

import { AgentsMap, GroupsMap, layout } from "./TeamMap";

const node = (role: string, state: Pane["state"] = "working", parent: string | null = null, since_s = 120): MapNode =>
  ({ role, state, since_s, agent: "claude", parent, group: null });
const map = (props: Partial<Parameters<typeof AgentsMap>[0]>) =>
  render(<AgentsMap nodes={[node("a"), node("b")]} edges={[]} waits={[]} onPick={vi.fn()} {...props} />);

beforeEach(() => { vi.resetAllMocks(); b.teamBand.mockResolvedValue({}); });
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


test("groups with no wake-ups between them stack in two columns", () => {
  const box = (id: string) => ({ id, width: 270, height: 70 });
  const placed = layout(["a", "b", "c", "d"].map(box), [], [{ from: "a", to: "c" }, { from: "b", to: "d" }], "TB", 56);
  expect(placed.boxes.c.y).toBeGreaterThan(placed.boxes.a.y);
  expect(placed.width).toBeLessThan(4 * 270);
});

test("reports-to links shape the layout but aren't drawn", () => {
  map({ nodes: [node("a"), node("b", "working", "a")] });
  const [a, c] = [screen.getByTestId("node-a"), screen.getByTestId("node-b")];
  expect(parseFloat(c.style.top)).toBeGreaterThan(parseFloat(a.style.top));
  expect(screen.queryByTestId("edge-a-b")).toBeNull();
});

test("edges are labelled; dashed waits and the amber callout show", () => {
  map({
    edges: [{ from: "a", to: "b", wakes: 6, empty: 0, noisy: null }, { from: "b", to: "a", wakes: 41, empty: 30, noisy: { per_hour: 41 } }],
    waits: [{ from: "a", to: "b" }],
  });
  expect(screen.getByTestId("edge-a-b").textContent).toBe("6");
  expect(screen.getByText("41 wake-ups an hour")).toBeTruthy();
  expect(screen.getByTestId("wait-a>b").getAttribute("stroke-dasharray")).toBeTruthy();
  expect(screen.getByText("waiting on b")).toBeTruthy();
});

test("nodes use the list's words, and a click picks the agent", () => {
  const onPick = vi.fn();
  map({ nodes: [node("planner", "asking", null, 720), node("builder-3", "stuck"), node("tester", "idle")], checking: ["builder-3"], onPick });
  expect(within(screen.getByTestId("node-planner")).getByText("asking you · 12 min")).toBeTruthy();
  expect(within(screen.getByTestId("node-builder-3")).getByText("Assieme · checking")).toBeTruthy();
  expect(screen.getByTestId("node-builder-3").className).toContain("assieme");
  expect(screen.getByTestId("node-builder-3").className).not.toContain("needs");
  expect(within(screen.getByTestId("node-tester")).getByText("idle")).toBeTruthy();
  fireEvent.click(screen.getByTestId("node-tester"));
  expect(onPick).toHaveBeenCalledWith("tester");
});

test("compact nodes are shorter", () => {
  map({ compact: true });
  expect(screen.getByTestId("node-a").style.height).toBe("48px");
});

test("groups: the chevron shows chips, and Open the group asks for it", () => {
  const onOpen = vi.fn();
  render(<GroupsMap onOpen={onOpen} groups={[
    { name: "p", roles: ["p-1", "p-2"], states: { "p-1": "asking", "p-2": "working" }, need_you: 1, edges: [{ to: "q", wakes: 12, noisy: null }] },
    { name: "q", roles: ["q-1"], states: { "q-1": "working" }, need_you: 0, edges: [] },
  ]} />);
  expect(screen.getByTestId("group-p").textContent).toContain("1 needs you");
  expect(screen.getByTestId("edge-p-q").textContent).toBe("12");
  expect(screen.getByText("all working")).toBeTruthy();
  expect(screen.getByText("1 agent")).toBeTruthy(); // a group of one reads in the singular
  fireEvent.click(screen.getByRole("button", { name: "Show p" }));
  expect(screen.getByText("p-1")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Open the group/ }));
  expect(onOpen).toHaveBeenCalledWith("p");
});
