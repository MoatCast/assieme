import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const b = vi.hoisted(() => ({ briefGet: vi.fn(), briefSave: vi.fn(), briefRemove: vi.fn(), fieldOf: (e: unknown) => ({ field: "", message: String((e as Error).message) }) }));
vi.mock("./bridge", () => b);

import TeamBrief, { when } from "./TeamBrief";

const BRIEF = { goal: "Ship retries", rules: "No API changes", decisions: [
  { id: "d1", q: "Flag or main?", a: "Behind a flag", role: "planner", by: "you", ms: Date.now() },
  { id: "d2", q: "Which Python?", a: "3.12 only", role: "tester", by: "agent", ms: 0 }] };
const open = async () => {
  const close = vi.fn();
  render(<TeamBrief team="shop" onClose={close} />);
  await act(async () => {});
  return close;
};

beforeEach(() => {
  vi.resetAllMocks();
  b.briefGet.mockResolvedValue(BRIEF);
  b.briefSave.mockResolvedValue(BRIEF);
  b.briefRemove.mockResolvedValue(BRIEF);
});
afterEach(cleanup);

test("shows the goal, the rules and each decision with who asked", async () => {
  await open();
  expect((screen.getByLabelText("Goal") as HTMLTextAreaElement).value).toBe("Ship retries");
  expect((screen.getByLabelText("Rules") as HTMLTextAreaElement).value).toBe("No API changes");
  expect(screen.getByText("Behind a flag")).toBeTruthy();
  expect(screen.getByText("planner")).toBeTruthy();
  expect(when(Date.now())).toMatch(/^today /);
  expect(when(0)).not.toMatch(/today/);
});

test("Save sends the edits and the removals; Cancel sends nothing", async () => {
  const close = await open();
  fireEvent.change(screen.getByLabelText("Goal"), { target: { value: "Ship backoff" } });
  fireEvent.click(screen.getByRole("button", { name: "Remove: Which Python?" }));
  expect(screen.queryByText("3.12 only")).toBeNull();
  expect(b.briefRemove).not.toHaveBeenCalled();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save" })));
  expect(b.briefSave).toHaveBeenCalledWith("shop", "Ship backoff", "No API changes");
  expect(b.briefRemove).toHaveBeenCalledWith("shop", "d2");
  expect(close).toHaveBeenCalled();
  cleanup();
  vi.clearAllMocks();
  const again = await open();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(again).toHaveBeenCalled();
  expect(b.briefSave).not.toHaveBeenCalled();
});

test("an empty brief says so, and a failed save stays open with the reason", async () => {
  b.briefGet.mockResolvedValue({ goal: "", rules: "", decisions: [] });
  b.briefSave.mockRejectedValue(new Error("The service isn't running."));
  const close = await open();
  expect(screen.getByText("None yet.")).toBeTruthy();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save" })));
  expect(screen.getByText("The service isn't running.")).toBeTruthy();
  expect(close).not.toHaveBeenCalled();
});
