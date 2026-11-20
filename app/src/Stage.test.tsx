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

test("no figure covers another, except the front row standing before the middle row (as in Paper)", () => { // real window
  const team = [...SMALL, node("designer"), node("writer")]; // three in the middle row: the reviewer and two flutes
  const { container } = render(<Stage nodes={team} groups={[]} />);
  const boxes = [...container.querySelectorAll<HTMLElement>(".stage-player, .stage-you")].map((el) => {
    const x = parseFloat(el.style.left);
    const y = parseFloat(el.style.top);
    const you = el.classList.contains("stage-you");
    return { x, y, w: you ? 54 : 48, h: you ? 51 : 54, seat: you ? "you" : el.closest("[data-seat]")?.getAttribute("data-seat") };
  });
  expect(boxes).toHaveLength(9);
  for (const [i, a] of boxes.entries()) {
    expect(a.x).toBeGreaterThanOrEqual(0);
    expect(a.x + a.w).toBeLessThanOrEqual(380);
    for (const b of boxes.slice(i + 1)) {
      if (new Set([a.seat, b.seat]).size === 2 && [a.seat, b.seat].every((s) => s === "front" || s === "middle")) continue;
      const apart = a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
      expect(apart, JSON.stringify([a, b])).toBe(true);
    }
  }
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

test("the band button's on state is the neutral selection, never the accent: blue means Assieme only", async () => {
  // @ts-expect-error type error without @types/node package, as in vite.config.ts
  const { readFileSync } = (await import("node:fs")) as { readFileSync: (f: string, e: string) => string };
  const on = readFileSync("src/Stage.css", "utf8").match(/\.stage-toggle\.on \{([^}]*)\}/)![1];
  expect(on).not.toContain("accent");
  expect(on).toContain("--quiet-select");
  expect(on).toContain("--quiet-outline");
});
