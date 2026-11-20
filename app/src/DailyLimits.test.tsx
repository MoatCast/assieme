// Daily limits (moved from the Team tab's tests; the sheet now opens from Team overview's Change).
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

const b = vi.hoisted(() => ({ budgetSet: vi.fn() }));
vi.mock("./bridge", () => b);

import DailyLimits from "./DailyLimits";

afterEach(cleanup);

const BUDGET = { limit: null, allowed: null, used: 4.2e6, at_limit: "pause" as const,
  agents: [{ role: "a", used: 3e6, limit: null }, { role: "b", used: 1.2e6, limit: null }] };

test("Daily limits saves millions as tokens, and empty as no limit", async () => {
  b.budgetSet.mockResolvedValue({});
  const onClose = vi.fn();
  render(<DailyLimits team="shop" budget={BUDGET} onClose={onClose} />);
  fireEvent.change(screen.getByLabelText("The whole team, in millions of tokens a day"), { target: { value: "2.5" } });
  fireEvent.change(screen.getByLabelText("a, in millions of tokens a day"), { target: { value: "nope" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(screen.getByText(/A limit is a number above zero/)).toBeTruthy();
  expect(b.budgetSet).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("a, in millions of tokens a day"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Just tell me" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await act(async () => {});
  expect(b.budgetSet).toHaveBeenCalledWith("shop", { "*": 2500000 }, "tell");
  expect(onClose).toHaveBeenCalled();
});
