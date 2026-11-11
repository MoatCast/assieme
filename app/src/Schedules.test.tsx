import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const b = vi.hoisted(() => ({
  scheduleList: vi.fn(), scheduleSave: vi.fn(), schedulePause: vi.fn(), scheduleRemove: vi.fn(), scheduleStop: vi.fn(),
  scheduleRunNow: vi.fn(), checkProject: vi.fn(), installTools: vi.fn(), pickFolder: vi.fn(),
  folderPreview: vi.fn(), folderSave: vi.fn(),
}));
vi.mock("./bridge", () => b);

import Schedules, { dayWords, nextRun } from "./Schedules";

const TEAMS = [{ id: "one-agent", label: "One agent", who: "Claude Code, its default model" },
  { id: "saved:cheap", label: "cheap (saved)", who: "Claude Code, Haiku" }];
const row = (over: object) => ({
  id: "s1", name: "Nightly tests", team: "nightly-tests", project: "/Users/x/shop", project_name: "shop", preset: "one-agent",
  preset_label: "One agent", who: "Claude Code, its default model", task: "Run the tests.", every: { kind: "day", at: "02:00" },
  when: "Every day at 02:00", paused: false, state: "waiting", result: null, line: "Next: tomorrow at 02:00.", note: null,
  next: "2026-11-12T01:00:00Z", review_role: null, ...over,
});
const flush = () => act(async () => {});
const open = async (rows: object[]) => {
  b.scheduleList.mockResolvedValue({ schedules: rows, teams: TEAMS });
  const onOpen = vi.fn();
  render(<Schedules onOpen={onOpen} />);
  await flush();
  return onOpen;
};
const click = async (name: string) => {
  fireEvent.click(screen.getByRole("button", { name }));
  await flush();
};

beforeEach(() => vi.resetAllMocks());
afterEach(cleanup);

test("an empty page shows examples and a New schedule button", async () => {
  await open([]);
  expect(screen.getByText("No schedules yet")).toBeTruthy();
  expect(screen.getByRole("button", { name: "New schedule" }).className).toContain("primary");
});

test("a row names the agents and models that will run", async () => {
  await open([row({})]);
  expect(screen.getByText("shop · Every day at 02:00 · One agent: Claude Code, its default model")).toBeTruthy();
});

test("a running run offers Open and Stop run, and shows a skipped time", async () => {
  const onOpen = await open([row({ state: "running", result: "running", line: "Running now, started 12:00.", note: "Skipped 18:00: the last run was still going." })]);
  expect(screen.getByText("Skipped 18:00: the last run was still going.")).toBeTruthy();
  await click("Open");
  expect(onOpen).toHaveBeenCalledWith("nightly-tests", null);
  b.scheduleStop.mockResolvedValue([row({ result: "stopped" })]);
  await click("Stop run");
  expect(b.scheduleStop).toHaveBeenCalledWith("s1");
});

test("a finished run opens its branch in Review", async () => {
  const onOpen = await open([row({ result: "finished", review_role: "nightly-tests-builder" })]);
  await click("Review");
  expect(onOpen).toHaveBeenCalledWith("nightly-tests", "nightly-tests-builder");
});

test("a run that couldn't start can try again now, and says why if it can't", async () => {
  await open([row({ result: "failed", line: "Couldn’t start today at 02:00: the folder is gone." })]);
  b.scheduleRunNow.mockRejectedValue(new Error("run: It is running now."));
  await click("Try again now");
  expect(b.scheduleRunNow).toHaveBeenCalledWith("s1");
  expect(screen.getByText("It is running now.")).toBeTruthy();
});

test("pause and resume", async () => {
  await open([row({ paused: true, state: "paused", line: "Paused. It won’t start until you resume it." })]);
  expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
  b.schedulePause.mockResolvedValue([row({})]);
  await click("Resume");
  expect(b.schedulePause).toHaveBeenCalledWith("s1", false);
});

test("a new weekly schedule is saved with its day and time", async () => {
  await open([]);
  await click("New schedule");
  b.pickFolder.mockResolvedValue("/Users/x/shop");
  b.checkProject.mockResolvedValue({ ok: true });
  fireEvent.change(screen.getByPlaceholderText("Nightly tests"), { target: { value: "Deps" } });
  await click("Choose…");
  fireEvent.change(screen.getByPlaceholderText(/Run the tests/), { target: { value: "Update the dependencies." } });
  fireEvent.change(screen.getByRole("combobox", { name: "Team" }), { target: { value: "saved:cheap" } });
  await click("Every week");
  fireEvent.change(screen.getByRole("combobox", { name: "Day" }), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("Time"), { target: { value: "07:30" } });
  b.scheduleSave.mockResolvedValue([row({ name: "Deps" })]);
  await click("Save");
  expect(b.scheduleSave).toHaveBeenCalledWith({ name: "Deps", project: "/Users/x/shop", preset: "saved:cheap", task: "Update the dependencies.",
    every: { kind: "week", day: 2, at: "07:30" } });
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("a folder that needs setting up says so, and a refusal shows by its field", async () => {
  await open([]);
  await click("New schedule");
  b.pickFolder.mockResolvedValue("/Users/x/plain");
  b.checkProject.mockResolvedValue({ ok: false, reason: "This folder has no saved version yet.", setup: true });
  await click("Choose…");
  expect(screen.getByText("This folder has no saved version yet.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Set up this folder" })).toBeTruthy();
  fireEvent.change(screen.getByPlaceholderText("Nightly tests"), { target: { value: "n" } });
  fireEvent.change(screen.getByPlaceholderText(/Run the tests/), { target: { value: "t" } });
  await click("Every few hours");
  b.scheduleSave.mockRejectedValue(new Error("project: This folder has no saved version yet. Set it up first."));
  await click("Save");
  expect(b.scheduleSave.mock.calls[0][0].every).toEqual({ kind: "hours", n: 6 });
  expect(screen.getByText("This folder has no saved version yet. Set it up first.")).toBeTruthy();
});

test("editing can remove the schedule", async () => {
  await open([row({})]);
  await click("Edit");
  expect(screen.getByRole("dialog", { name: "Edit schedule" })).toBeTruthy();
  b.scheduleRemove.mockResolvedValue([]);
  await click("Remove schedule");
  expect(b.scheduleRemove).toHaveBeenCalledWith("s1");
  expect(screen.getByText("No schedules yet")).toBeTruthy();
});

test("next run in words", () => {
  const now = new Date(2026, 10, 11, 17, 0); // a Wednesday
  expect(dayWords(nextRun({ kind: "day", at: "02:00" }, now), now)).toBe("tomorrow at 02:00");
  expect(dayWords(nextRun({ kind: "day", at: "18:00" }, now), now)).toBe("today at 18:00");
  expect(dayWords(nextRun({ kind: "week", day: 0, at: "07:00" }, now), now)).toBe("Monday at 07:00");
  expect(dayWords(nextRun({ kind: "hours", n: 6 }, now), now)).toBe("today at 23:00");
});
