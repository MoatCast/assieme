import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import PlanBars, { hhmm } from "./PlanBars";

afterEach(cleanup);

const AT_15 = new Date(2026, 10, 21, 15, 0).getTime() / 1000;

test("bar shows percent", () => {
  render(<PlanBars limits={{ claude: { used: 72.4, reached: false, resets_at: AT_15 } }} />);
  expect(screen.getByText("Claude")).toBeTruthy();
  expect(screen.getByText("72% used").parentElement?.title).toBe("Resets at 15:00");
});

test("reached shows back at", () => {
  render(<PlanBars limits={{ codex: { used: 100, reached: true, resets_at: AT_15 } }} />);
  expect(screen.getByText("Back at 15:00").parentElement?.className).toBe("plan-bar reached");
});

test("no limits no bars", () => {
  const { container } = render(<PlanBars limits={{}} />);
  expect(container.innerHTML).toBe("");
});

test("a kind with no percent and no limit reached gets no bar, never 0% used", () => {
  const { container } = render(<PlanBars limits={{ claude: { used: null, reached: false, resets_at: null } }} />);
  expect(container.innerHTML).toBe("");
});

test("hhmm is local time", () => expect(hhmm(AT_15)).toBe("15:00"));
