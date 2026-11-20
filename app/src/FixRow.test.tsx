import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("./bridge", () => ({ activityUndo: vi.fn() }));

import FixRow from "./FixRow";
import { fixRow } from "./windowFixtures";

afterEach(cleanup);

test("a fix row says the problem as measured, then what Assieme did", () => {
  render(<FixRow row={fixRow("builder-3", "checking", "14:22", { title: "Asked builder-3 to try another way" })} />);
  expect(screen.getByText("14:22")).toBeTruthy();
  expect(screen.getByText("builder-3")).toBeTruthy();
  expect(screen.getByTestId("fix-text").textContent)
    .toBe("builder-3 · stuck 9 min, the same test failed 4 times (38K tokens) → asked it to try another way");
  expect(screen.getByLabelText("Assieme")).toBeTruthy();
});

test("the outcome tags carry an icon as well as a colour", () => {
  const { rerender } = render(<FixRow row={fixRow("a", "checking", "14:22")} />);
  expect(screen.getByText("◌ Checking · until 14:32").className).toContain("checking");
  rerender(<FixRow row={fixRow("a", "still_stuck", "14:22")} />);
  expect(screen.getByText("! Still stuck").className).toContain("still_stuck");
  rerender(<FixRow row={fixRow("a", "fixed", "14:22")} />);
  expect(screen.getByText("✓ Fixed").className).toContain("fixed");
});

test("a row with nothing measured shows its title", () => {
  render(<FixRow row={fixRow("tester", "fixed", "13:02", { title: "Freed up memory for tester", measured: null })} />);
  expect(screen.getByTestId("fix-text").textContent).toBe("tester · freed up memory for it");
});

test("a held pair reads as one sentence", () => {
  const row = fixRow("reviewer", "fixed", "13:41", { action: "quiet", title: "Held reviewer's routine notes until builder-1 was free",
    measured: { minutes: null, what: "41 wake-ups an hour with no work", tokens: null } });
  render(<FixRow row={row} />);
  expect(screen.getByTestId("fix-text").textContent)
    .toBe("reviewer · 41 wake-ups an hour with no work → held its routine notes until builder-1 was free");
});
