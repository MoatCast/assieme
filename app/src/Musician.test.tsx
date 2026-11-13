import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import Musician, { musicianLabel, poseFor } from "./Musician";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const fills = (el: Element) => [...el.querySelectorAll("rect")].map((r) => r.getAttribute("fill"));

test("each state draws its pose", () => {
  expect(poseFor("working")).toBe("play");
  expect(poseFor("asking")).toBe("hand");
  expect(poseFor("stuck")).toBe("hand");
  expect(poseFor("idle")).toBe("rest");
  expect(poseFor("waiting")).toBe("rest");
  expect(poseFor("exited")).toBe("grey");
  render(<Musician instrument="violin" state="working" role="builder-1" />);
  expect(screen.getByRole("img").querySelectorAll("g")).toHaveLength(2); // two frames to swap
});

test("an unknown state rests", () => { // Review Focus 4
  expect(poseFor("thinking-hard")).toBe("rest");
  render(<Musician instrument="cello" state="thinking-hard" role="b" />);
  expect(screen.getByRole("img").querySelectorAll("g")).toHaveLength(1);
});

test("label names role, instrument and state", () => { // Review Focus 5
  render(<Musician instrument="violin" state="asking" role="builder-2" />);
  expect(screen.getByRole("img", { name: "builder-2, violin, needs you" })).toBeTruthy();
  expect(musicianLabel("reviewer", "reviewer", "working")).toBe("reviewer, score, working");
  expect(musicianLabel("t", "timpani", "exited")).toBe("t, timpani, stopped");
});

test("only whole sizes, crisp edges", () => {
  render(<Musician instrument="bass" state="idle" role="b" size={3} />);
  const svg = screen.getByRole("img").querySelector("svg")!;
  expect([svg.getAttribute("width"), svg.getAttribute("height")]).toEqual(["48", "54"]);
  expect(svg.getAttribute("shape-rendering")).toBe("crispEdges");
});

test("no motion when reduced", () => {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce"), addEventListener() {}, removeEventListener() {} }));
  render(<Musician instrument="violin" state="working" role="b" />);
  const img = screen.getByRole("img");
  expect(img.className).not.toContain("playing");
  expect(img.querySelectorAll("g")).toHaveLength(1);
});

test("plays when motion is allowed", () => {
  render(<Musician instrument="violin" state="working" role="b" />);
  expect(screen.getByRole("img").className).toContain("playing");
});

test("coral only on the hand pose", () => {
  for (const state of ["working", "idle", "exited"]) {
    const { container } = render(<Musician instrument="piano" state={state} role="p" />);
    expect(fills(container).some((f) => f?.includes("needs-you"))).toBe(false);
    cleanup();
  }
  const { container } = render(<Musician instrument="piano" state="asking" role="p" />);
  expect(fills(container).some((f) => f?.includes("needs-you"))).toBe(true);
});

test("grey draws every pixel in one colour; the second palette swaps clothes and trim", () => {
  const { container } = render(<Musician instrument="cello" state="exited" role="c" />);
  expect(new Set(fills(container))).toEqual(new Set(["var(--quiet-musician-grey)"]));
  cleanup();
  const one = render(<Musician instrument="violin" state="idle" role="v" />).container;
  const m = fills(one).filter((f) => f === "var(--quiet-musician-m)").length;
  cleanup();
  const two = render(<Musician instrument="violin" state="idle" role="v" palette={1} />).container;
  expect(fills(two).filter((f) => f === "var(--quiet-musician-c)").length).toBe(m);
});

test("one rect per run of the same colour", () => {
  const { container } = render(<Musician instrument="timpani" state="idle" role="t" />);
  // TIMPANI rest row "WWWWWW.WWWWWW.." draws two rects, not twelve
  expect(container.querySelectorAll('rect[width="6"]').length).toBeGreaterThanOrEqual(2);
});
