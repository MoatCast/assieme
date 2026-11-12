import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { BoardCard, BoardData } from "./bridge";

const b = vi.hoisted(() => ({ boardGet: vi.fn(), boardMove: vi.fn(), boardRemove: vi.fn(), boardAdd: vi.fn(), paneAct: vi.fn(), openLink: vi.fn(), previewList: vi.fn((): Promise<unknown[]> => Promise.resolve([])), previewStart: vi.fn(), previewStop: vi.fn(), }));
vi.mock("./bridge", () => b);
vi.mock("./Terminal", () => ({ default: () => <div /> }));

import Board, { NewTask } from "./Board";

const card = (id: string, over: Partial<BoardCard> = {}): BoardCard => ({
  id, title: `task ${id}`, role: "builder-1", status: "todo", by: "planner", note: "", needs_you: false, moved_s: 0, ...over,
});
const BOARD = (cards: BoardCard[], over: Partial<BoardData> = {}): BoardData => ({ team: "shop", title: "Retry with backoff", cards, spend: null, ...over });

const flush = () => act(async () => {});
const open = async (data: BoardData, props: Partial<Parameters<typeof Board>[0]> = {}) => {
  b.boardGet.mockResolvedValue(data);
  const calls = { term: vi.fn(), review: vi.fn(), neu: vi.fn(), task: vi.fn() };
  const view = render(<Board team="shop" pollMs={100000} onNewTeam={calls.neu} onNewTask={calls.task} onOpenTerminal={calls.term} onReview={calls.review} {...props} />);
  await flush();
  return { ...calls, ...view };
};
const column = (name: string) => within(screen.getByRole("region", { name }));

beforeEach(() => {
  vi.resetAllMocks();
  b.paneAct.mockResolvedValue({ ok: true, detail: "" });
  b.previewList.mockResolvedValue([]);
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
  const { neu, rerender, term, review, task } = await open(BOARD([]), { team: null });
  fireEvent.click(screen.getByRole("button", { name: "New team" }));
  expect(neu).toHaveBeenCalled();
  expect(b.boardGet).not.toHaveBeenCalled();
  rerender(<Board team="shop" pollMs={100000} onNewTeam={neu} onNewTask={task} onOpenTerminal={term} onReview={review} />);
  await flush();
  rerender(<Board team="shop" pollMs={100000} refreshKey={1} onNewTeam={neu} onNewTask={task} onOpenTerminal={term} onReview={review} />);
  await flush();
  expect(b.boardGet).toHaveBeenCalledTimes(2);
  expect(screen.getByText("No tasks yet")).toBeTruthy();
  fireEvent.click(column("To do").getByRole("button", { name: "New task" }));
  expect(task).toHaveBeenCalled();
});

test("a card's menu moves it to another column, or removes it", async () => {
  b.boardMove.mockResolvedValue({});
  b.boardRemove.mockResolvedValue({ ok: true });
  await open(BOARD([card("1", { status: "doing" })]));
  expect(screen.queryByRole("menu")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "More for task 1" }));
  const menu = within(screen.getByRole("menu"));
  expect(menu.getAllByRole("menuitemradio").map((i) => i.textContent)).toEqual(["To do", "Doing", "Review", "Done"]);
  expect((menu.getByRole("menuitemradio", { name: "Doing" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(menu.getByRole("menuitemradio", { name: "Review" }));
  await flush();
  expect(b.boardMove).toHaveBeenCalledWith("shop", "1", "review");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(b.boardGet).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "More for task 1" }));
  fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
  expect(screen.queryByRole("menu")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "More for task 1" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
  await flush();
  expect(b.boardRemove).toHaveBeenCalledWith("shop", "1");
});

test("a move the brain refuses says why on the card", async () => {
  b.boardMove.mockRejectedValue(new Error("No task 1 on this board."));
  await open(BOARD([card("1")]));
  fireEvent.click(screen.getByRole("button", { name: "More for task 1" }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: "Done" }));
  await flush();
  expect(screen.getByTestId("task-1").textContent).toContain("No task 1 on this board.");
});

test("New task adds a card, for nobody or for an agent", async () => {
  b.boardAdd.mockResolvedValue({ card: card("9"), told: true });
  const close = vi.fn();
  render(<NewTask team="shop" roles={["planner", "builder-1"]} onClose={close} />);
  const add = screen.getByRole("button", { name: "Add task" }) as HTMLButtonElement;
  expect(add.disabled).toBe(true); // nothing to add yet
  fireEvent.change(screen.getByLabelText("What needs doing"), { target: { value: "Add retry metrics" } });
  expect(screen.getByText(/Nobody is told/)).toBeTruthy();
  fireEvent.click(add);
  await flush();
  expect(b.boardAdd).toHaveBeenCalledWith("shop", "Add retry metrics", null);
  expect(close).toHaveBeenCalledWith(true, undefined);
  fireEvent.change(screen.getByLabelText(/Give it to/), { target: { value: "builder-1" } });
  expect(screen.getByText(/is told about it now/).textContent).toContain("builder-1");
  b.boardAdd.mockResolvedValue({ card: card("10"), told: false });
  fireEvent.click(add);
  await flush();
  expect(b.boardAdd).toHaveBeenLastCalledWith("shop", "Add retry metrics", "builder-1");
  expect(close).toHaveBeenLastCalledWith(true, "builder-1 isn’t running, so it wasn’t told about the new task.");
});

test("New task keeps the sheet open with the reason when adding fails, and Escape closes it", async () => {
  b.boardAdd.mockRejectedValue(new Error("This board is full. Remove some finished cards first."));
  const close = vi.fn();
  render(<NewTask team="shop" roles={[]} onClose={close} />);
  fireEvent.change(screen.getByLabelText("What needs doing"), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: "Add task" }));
  await flush();
  expect(screen.getByText(/This board is full/)).toBeTruthy();
  expect(close).not.toHaveBeenCalled();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(close).toHaveBeenCalledWith(false);
});

const PREVIEW = (role: string, more: object = {}) => ({ team: "shop", role, project: "checkout", state: "running", command: "npm run dev",
  url: "http://localhost:5175", port: 5175, reason: "", output: "", fix: null, ...more });

test("a review card previews its branch: start, then open and stop, or see why it didn't start", async () => {
  const branch = { ahead: 2, files: 1, add: 4, del: 2, tests: null, notes: [] };
  b.previewStart.mockResolvedValue(PREVIEW("builder-1", { state: "starting", url: null }));
  const { review } = await open(BOARD([card("1", { status: "review", branch }), card("2", { status: "review", role: "builder-2", branch }),
    card("3", { status: "review", role: "builder-3", branch })]));
  b.previewList.mockResolvedValue([PREVIEW("builder-2"), PREVIEW("builder-3", { state: "failed", url: null, reason: "npm run dev stopped." })]);
  fireEvent.click(within(screen.getByTestId("task-1")).getByRole("button", { name: "Preview" }));
  await flush();
  expect(b.previewStart).toHaveBeenCalledWith("shop", "builder-1", null, false, null);
  await flush();
  const two = screen.getByTestId("task-2");
  expect(two.textContent).toContain("Preview at localhost:5175");
  fireEvent.click(within(two).getByRole("button", { name: "Open preview" }));
  expect(b.openLink).toHaveBeenCalledWith("http://localhost:5175");
  fireEvent.click(within(two).getByRole("button", { name: "Stop" }));
  await flush();
  expect(b.previewStop).toHaveBeenCalledWith("shop", "builder-2");
  const three = screen.getByTestId("task-3");
  expect(three.textContent).toContain("The preview didn't start");
  fireEvent.click(within(three).getByRole("button", { name: "See why" }));
  expect(review).toHaveBeenCalledWith("shop", "builder-3");
});
