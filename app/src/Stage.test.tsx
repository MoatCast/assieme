import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { MapGroup, MapNode } from "./bridge";
import { setMusicianMode } from "./musicianMode";
import Stage, { STAGE_KEY, StageToggle, useStageShown } from "./Stage";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

const node = (role: string, state: MapNode["state"] = "working", group: string | null = null): MapNode =>
  ({ role, state, since_s: 1, agent: "claude", parent: null, group });
const SMALL = [node("planner", "asking"), node("builder-1"), node("builder-2"), node("builder-3", "stuck"), node("reviewer"),
  node("tester", "idle")];

function Shown() {
  const shown = useStageShown();
  return <><StageToggle />{shown && <Stage nodes={SMALL} groups={[]} />}</>;
}

test("hidden until the button shows it, and it stays shown", () => {
  render(<Shown />);
  expect(screen.queryByRole("img", { name: /^The band/ })).toBeNull();
  const button = screen.getByRole("button", { name: "Show the stage" });
  expect(button.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(button);
  expect(screen.getByRole("img", { name: /^The band/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Hide the stage" }).getAttribute("aria-pressed")).toBe("true");
  expect(localStorage.getItem(STAGE_KEY)).toBe("shown");
  fireEvent.click(screen.getByRole("button", { name: "Hide the stage" }));
  expect(screen.queryByRole("img", { name: /^The band/ })).toBeNull();
  expect(localStorage.getItem(STAGE_KEY)).toBeNull();
});

test("another window's choice follows", () => {
  render(<Shown />);
  act(() => {
    localStorage.setItem(STAGE_KEY, "shown");
    window.dispatchEvent(new StorageEvent("storage", { key: STAGE_KEY }));
  });
  expect(screen.getByRole("img", { name: /^The band/ })).toBeTruthy();
});

test("with Musicians off, neither the button nor the stage", () => {
  localStorage.setItem(STAGE_KEY, "shown");
  setMusicianMode("off");
  render(<Shown />);
  expect(screen.queryByRole("button", { name: /the stage/ })).toBeNull();
  expect(screen.queryByRole("img", { name: /^The band/ })).toBeNull();
});

test("up to twelve agents: one musician each, in its row; one image with one label", () => { // Review Focus 5
  render(<Stage nodes={SMALL} groups={[]} />);
  const stage = screen.getByRole("img", { name: "The band: 6 agents, 2 need you" });
  const inner = stage.querySelector("[aria-hidden=true]")!;
  expect(inner.querySelectorAll(".musician")).toHaveLength(7); // six and you, conducting
  expect(stage.querySelector('[data-seat="back"] .musician')).toBeTruthy(); // timpani
  expect(stage.querySelectorAll('[data-seat="front"] .musician')).toHaveLength(4); // piano and strings
});

test("the agent that needs you raises its hand", () => {
  render(<Stage nodes={[node("builder-3", "stuck")]} groups={[]} />);
  const fills = [...document.querySelectorAll("rect")].map((r) => r.getAttribute("fill"));
  expect(fills.some((f) => f?.includes("needs-you"))).toBe(true);
});

test("over twelve: one musician per group with a count, and the same height", () => { // Review Focus 3
  const nodes = Array.from({ length: 30 }, (_, i) => node(`builder-${i + 1}`, i === 0 ? "asking" : "working", `g${i % 4}`));
  const groups: MapGroup[] = [0, 1, 2, 3].map((g) => {
    const roles = nodes.filter((n) => n.group === `g${g}`).map((n) => n.role);
    return { name: `g${g}`, roles, states: Object.fromEntries(roles.map((r) => [r, nodes.find((n) => n.role === r)!.state])),
      need_you: g === 0 ? 1 : 0, edges: [] };
  });
  const { container } = render(<Stage nodes={nodes} groups={groups} />);
  expect(screen.getByRole("img", { name: "The band: 30 agents in 4 groups, 1 needs you" })).toBeTruthy();
  expect(container.querySelectorAll(".stage-count")).toHaveLength(4);
  expect(screen.getAllByText("×8")).toHaveLength(2); // groups of 8, 8, 7 and 7
  const tall = (container.firstChild as HTMLElement).style.height;
  cleanup();
  const small = render(<Stage nodes={SMALL} groups={[]} />).container;
  expect((small.firstChild as HTMLElement).style.height).toBe(tall);
});
