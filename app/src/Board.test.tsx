import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { BoardCard, BoardData } from "./bridge";

const b = vi.hoisted(() => ({ boardGet: vi.fn(), boardMove: vi.fn(), boardRemove: vi.fn(), boardAdd: vi.fn(), paneAct: vi.fn() }));
vi.mock("./bridge", () => b);
vi.mock("./Terminal", () => ({ default: () => <div /> }));

import Board from "./Board";

const card = (id: string, over: Partial<BoardCard> = {}): BoardCard => ({
  id, title: `task ${id}`, role: "builder-1", status: "todo", by: "planner", note: "", needs_you: false, moved_s: 0, ...over,
});
const BOARD = (cards: BoardCard[], over: Partial<BoardData> = {}): BoardData => ({ team: "shop", title: "Retry with backoff", cards, spend: null, ...over });

const flush = () => act(async () => {});
const open = async (data: BoardData, props: Partial<Parameters<typeof Board>[0]> = {}) => {
  b.boardGet.mockResolvedValue(data);
  const calls = { term: vi.fn(), review: vi.fn(), neu: vi.fn() };
  const view = render(<Board team="shop" pollMs={100000} onNewTeam={calls.neu} onOpenTerminal={calls.term} onReview={calls.review} {...props} />);
  await flush();
  return { ...calls, ...view };
};
const column = (name: string) => within(screen.getByRole("region", { name }));

beforeEach(() => {
  vi.resetAllMocks();
  b.paneAct.mockResolvedValue({ ok: true, detail: "" });
});
afterEach(cleanup);

test("cards sit in their columns, with the head counting them", async () => {
  await open(BOARD([
    card("1", { role: null, by: "you" }), card("2", { status: "doing" }), card("3", { status: "done", note: "212 tests pass", moved_s: 540 }),
  ], { spend: { tokens: 12400, waste_share: 0.1 } }));
  expect(screen.getByRole("heading", { name: "Retry with backoff" })).toBeTruthy();
  expect(screen.getByText(/3 tasks/).textContent).toBe("3 tasks · planned by planner · 1 done");
  expect(screen.getByText(/Today:/).textContent).toBe("Today: 12K tokens");
  expect(column("To do").getByText("task 1")).toBeTruthy();
  expect(column("To do").getByText("Not assigned yet")).toBeTruthy();
  expect(column("Doing").getByText("task 2")).toBeTruthy();
  expect(column("Done").getByText("212 tests pass · 9m ago")).toBeTruthy();
  expect(column("Review").getByText(/Nothing to review/)).toBeTruthy();
});

test("a doing card shows its agent, and its fix when the agent needs you", async () => {
  const fix = { id: "f1", text: "Seems stuck.", buttons: [{ id: "b1", label: "Ask it to try another way", kind: "action" as const }] };
  const { term } = await open(BOARD([
    card("1", { status: "doing", agent: { role: "builder-1", state: "working", since_s: 120, detail: "", fix: null, reachable: true } }),
    card("2", { status: "doing", role: "builder-3", agent: { role: "builder-3", state: "stuck", since_s: 1080, detail: "", fix, reachable: true } }),
  ]));
  expect(screen.getByTestId("task-1").textContent).toContain("working · 2m");
  const stuck = screen.getByTestId("task-2");
  expect(stuck.className).toContain("needs");
  expect(stuck.textContent).toContain("seems stuck · 18m");
  fireEvent.click(within(stuck).getByRole("button", { name: "Ask it to try another way" }));
  await flush();
  expect(b.paneAct).toHaveBeenCalledWith({ team: "shop", role: "builder-3", fix: "f1", button: "b1" });
  expect(b.boardGet).toHaveBeenCalledTimes(2); // it reads again after the press
  fireEvent.click(within(stuck).getByRole("button", { name: "Open terminal" }));
  expect(term).toHaveBeenCalledWith("builder-3");
});

test("a review card shows its branch, and Review changes opens that agent's review", async () => {
  const { review } = await open(BOARD([
    card("1", { status: "review", branch: { ahead: 2, files: 3, add: 42, del: 6, tests: { state: "ok", text: "Tests pass" }, notes: ["reviewer", "reviewer"] } }),
    card("2", { status: "review", role: "builder-2", branch: { ahead: 0, files: 0, add: 0, del: 0, tests: null, notes: [] } }),
    card("3", { status: "review", role: "builder-9", branch: null, note: "ready when you are" }),
  ]));
  const one = screen.getByTestId("task-1");
  expect(one.textContent).toContain("3 files · +42 −6");
  expect(one.textContent).toContain("Tests pass");
  expect(one.textContent).toContain("reviewer left 2 comments");
  fireEvent.click(within(one).getByRole("button", { name: "Review changes" }));
  expect(review).toHaveBeenCalledWith("shop", "builder-1");
  expect(within(screen.getByTestId("task-2")).queryByRole("button", { name: "Review changes" })).toBeNull();
  expect(screen.getByTestId("task-3").textContent).toContain("ready when you are");
  expect(screen.queryByText(/Nothing to review/)).toBeNull();
});

test("a card waiting for the person says so, and a fresh merge stands out", async () => {
  await open(BOARD([
    card("1", { role: null, needs_you: true, note: "Waiting for your answer on the flag" }),
    card("2", { status: "done", note: "merged into main", moved_s: 5 }),
    card("3", { status: "done", note: "merged into main", moved_s: 5000 }),
  ]));
  expect(screen.getByTestId("task-1").querySelector(".task-waits")?.textContent).toBe("Waiting for your answer on the flag");
  expect(screen.getByTestId("task-2").textContent).toContain("merged into main · just now");
  expect(screen.getByTestId("task-2").className).toContain("merged");
  expect(screen.getByTestId("task-3").className).not.toContain("merged");
});

test("no team offers New team, and a new refresh key reads again", async () => {
  const { neu, rerender, term, review } = await open(BOARD([]), { team: null });
  fireEvent.click(screen.getByRole("button", { name: "New team" }));
  expect(neu).toHaveBeenCalled();
  expect(b.boardGet).not.toHaveBeenCalled();
  rerender(<Board team="shop" pollMs={100000} onNewTeam={neu} onOpenTerminal={term} onReview={review} />);
  await flush();
  rerender(<Board team="shop" pollMs={100000} refreshKey={1} onNewTeam={neu} onOpenTerminal={term} onReview={review} />);
  await flush();
  expect(b.boardGet).toHaveBeenCalledTimes(2);
  expect(screen.getByText("No tasks yet")).toBeTruthy();
});
