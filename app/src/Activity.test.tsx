import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { ActivityRow } from "./bridge";

const b = vi.hoisted(() => ({ activityList: vi.fn(), activityUndo: vi.fn() }));
vi.mock("./bridge", () => b);

import Activity from "./Activity";

const today = (() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; })();
const row = (id: string, over: Partial<ActivityRow> = {}): ActivityRow =>
  ({ id, at: "14:02", day: today, title: `Row ${id}`, reason: `Because ${id}`, by: "you", ok: true, action: "x", undo: null, ...over });
const open = async (rows: ActivityRow[]) => {
  b.activityList.mockResolvedValue({ rows });
  render(<Activity team="shop" pollMs={100000} />);
  await act(async () => {});
};

beforeEach(() => { vi.resetAllMocks(); });
afterEach(cleanup);

test("filters ask the brain", async () => {
  await open([]);
  expect(b.activityList).toHaveBeenLastCalledWith("shop", "all");
  fireEvent.click(screen.getByRole("button", { name: "By you" }));
  await act(async () => {});
  expect(b.activityList).toHaveBeenLastCalledWith("shop", "you");
  fireEvent.click(screen.getByRole("button", { name: "Automatic" }));
  await act(async () => {});
  expect(b.activityList).toHaveBeenLastCalledWith("shop", "auto");
});

test("rows are grouped by day", async () => {
  await open([row("1"), row("2", { day: "2026-01-02" })]);
  expect(screen.getByText("Today")).toBeTruthy();
  expect(screen.getByText("2026-01-02")).toBeTruthy();
  expect(screen.getByText("Because 1")).toBeTruthy();
});

test("the button says what the brain says, and rows without one have none", async () => {
  await open([row("1", { undo: { label: "Undo" } }), row("2", { undo: { label: "Stop" } }), row("3")]);
  expect(screen.getByRole("button", { name: "Undo" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
  expect(screen.getByTestId("row-3").querySelector("button")).toBeNull();
});

test("a refused Undo shows its message on the row", async () => {
  b.activityUndo.mockResolvedValue({ ok: false, detail: "This was already undone." });
  await open([row("1", { undo: { label: "Undo" } })]);
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await act(async () => {});
  expect(b.activityUndo).toHaveBeenCalledWith("1");
  expect(screen.getByText("This was already undone.")).toBeTruthy();
});

test("Undo refreshes the list, and its button waits while pending", async () => {
  let done: (v: unknown) => void = () => {};
  b.activityUndo.mockReturnValue(new Promise((r) => { done = r; }));
  await open([row("1", { undo: { label: "Undo" } })]);
  const before = b.activityList.mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await act(async () => {});
  expect((screen.getByRole("button", { name: "Undo" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect(b.activityUndo).toHaveBeenCalledTimes(1);
  await act(async () => { done({ ok: true, detail: "" }); });
  expect(b.activityList.mock.calls.length).toBeGreaterThan(before);
});

test("an empty list says so", async () => {
  await open([]);
  expect(screen.getByText("Nothing yet.")).toBeTruthy();
});

test("Stop on an added agent asks first, and Keep it running changes nothing", async () => {
  b.activityUndo.mockResolvedValue({ ok: true, detail: "" });
  await open([row("1", { undo: { label: "Stop", confirm: "This ends builder-1." } })]);
  fireEvent.click(screen.getByRole("button", { name: "Stop" }));
  expect(screen.getByText("This ends builder-1.")).toBeTruthy();
  expect(b.activityUndo).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Keep it running" }));
  expect(b.activityUndo).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Stop" }));
  fireEvent.click(screen.getByRole("button", { name: "Stop it" }));
  await act(async () => {});
  expect(b.activityUndo).toHaveBeenCalledWith("1");
});
