import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { ReviewData } from "./bridge";

const b = vi.hoisted(() => ({
  reviewGet: vi.fn(), reviewMerge: vi.fn(), reviewDiscard: vi.fn(), reviewAsk: vi.fn(), reviewNote: vi.fn(),
  reviewEditor: vi.fn(), reviewPullRequest: vi.fn(), openLink: vi.fn(),
}));
vi.mock("./bridge", () => b);

import Review, { Notice } from "./Review";

const DIFF = `diff --git a/retry.py b/retry.py
index 1111111..2222222 100644
--- a/retry.py
+++ b/retry.py
@@ -12,3 +12,4 @@
 import random
 import time
+def backoff():
 x = 1
@@ -30,2 +31,2 @@ def call():
 y = 2
-time.sleep(1)
+time.sleep(backoff())
diff --git a/config.py b/config.py
index 3333333..4444444 100644
--- a/config.py
+++ b/config.py
@@ -1,1 +1,1 @@
-a = 1
+a = 2
`;
const OK = [
  { id: "tests", state: "ok", text: "Tests pass, 212 of 212", detail: "" },
  { id: "conflicts", state: "ok", text: "No conflicts with main", detail: "" },
  { id: "own-files", state: "ok", text: "Only touched its own files", detail: "" },
];
const DATA: ReviewData = {
  team: "shop", role: "builder-1", project: "checkout-service", base: "main", branch: "assieme/shop/builder-1", tip: "abc", ahead: 3,
  files: [{ path: "retry.py", add: 2, del: 1, binary: false, missing: false }, { path: "config.py", add: 1, del: 1, binary: false, missing: false }],
  diff: DIFF, too_big: false, checks: OK as ReviewData["checks"],
  notes: [
    { id: "n1", author: "reviewer", file: "retry.py", line: 14, text: "cap at 6s?" },
    { id: "n2", author: "reviewer", file: "retry.py", line: 88, text: "logs nothing" },
  ],
  agent: { live: true, working: false, address: "t1" },
};
const withChecks = (over: Partial<ReviewData>) => ({ ...DATA, ...over });
const flush = () => act(async () => {});
const onBack = vi.fn();
const onMerged = vi.fn();
const onDiscarded = vi.fn();
const open = async () => {
  render(<Review team="shop" role="builder-1" onBack={onBack} onMerged={onMerged} onDiscarded={onDiscarded} />);
  await flush();
};
const click = async (name: string | RegExp) => {
  fireEvent.click(screen.getByRole("button", { name }));
  await flush();
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  b.reviewGet.mockResolvedValue(DATA);
  b.reviewMerge.mockResolvedValue({ role: "builder-1", base: "main", problem: null, undo: "u1" });
  b.reviewDiscard.mockResolvedValue({ role: "builder-1", problem: null });
  b.reviewAsk.mockResolvedValue({ restarted: false });
  b.reviewNote.mockResolvedValue({ ok: true });
  b.reviewEditor.mockResolvedValue({ ok: true });
  b.reviewPullRequest.mockResolvedValue({ url: "https://example.test/pull/7" });
  b.openLink.mockResolvedValue(undefined);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

test("shows the agent, its real branch, checks and files", async () => {
  await open();
  expect(screen.getByRole("heading", { name: "builder-1" })).toBeInTheDocument();
  expect(screen.getByText("assieme/shop/builder-1")).toBeInTheDocument();
  expect(screen.getByText("Tests pass, 212 of 212")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /config\.py/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Merge into main" })).toBeEnabled();
});

test("draws the first file's diff with the unchanged lines between hunks", async () => {
  await open();
  expect(screen.getByText("def backoff():")).toBeInTheDocument();
  expect(screen.getByText("15 unchanged lines")).toBeInTheDocument();
  expect(screen.queryByText("a = 2")).toBeNull();
});

test("choosing a file shows its diff", async () => {
  await open();
  await click(/config\.py/);
  expect(screen.getByText("a = 2")).toBeInTheDocument();
  expect(screen.queryByText("def backoff():")).toBeNull();
});

test("a note sits under its line; one whose line has gone sits at the top", async () => {
  await open();
  const row = screen.getByText("def backoff():").closest("tr")!;
  expect(within(row.nextElementSibling as HTMLElement).getByText("cap at 6s?")).toBeInTheDocument();
  expect(screen.getByText(/isn't in this diff any more/)).toBeInTheDocument();
  expect(screen.getByText("logs nothing").closest(".note")!.parentElement).toHaveClass("diff-area");
});

test("Ignore calls the brain", async () => {
  await open();
  fireEvent.click(screen.getAllByRole("button", { name: "Ignore" })[0]);
  await flush();
  expect(b.reviewNote).toHaveBeenCalledWith("shop", "builder-1", "n2", "ignore");
});

test("Ask to fix sends and confirms", async () => {
  await open();
  fireEvent.click(screen.getAllByRole("button", { name: "Ask builder-1 to fix it" })[1]);
  await flush();
  expect(b.reviewNote).toHaveBeenCalledWith("shop", "builder-1", "n1", "fix");
  expect(screen.getByText("Sent the note to builder-1.")).toBeInTheDocument();
});

test("merging with good checks needs no confirmation", async () => {
  await open();
  await click("Merge into main");
  expect(b.reviewMerge).toHaveBeenCalledWith("shop", "builder-1", "abc", false);
  expect(onMerged).toHaveBeenCalledWith({ role: "builder-1", base: "main", problem: null, undo: "u1" });
});

test("failing tests and a working agent ask first", async () => {
  b.reviewGet.mockResolvedValue(withChecks({
    agent: { live: true, working: true, address: "t1" },
    checks: [{ id: "tests", state: "bad", text: "Tests fail, 3 of 212", detail: "E boom" }, OK[1], OK[2]] as ReviewData["checks"],
  }));
  await open();
  expect(screen.getByText("E boom")).toBeInTheDocument();
  await click("Merge into main");
  expect(b.reviewMerge).not.toHaveBeenCalled();
  expect(screen.getByText("The tests fail on this branch")).toBeInTheDocument();
  expect(screen.getByText("Merging also stops builder-1, which is still working.")).toBeInTheDocument();
  await click("Merge anyway");
  expect(b.reviewMerge).toHaveBeenCalledWith("shop", "builder-1", "abc", true);
});

test("Not yet closes the question without merging", async () => {
  b.reviewGet.mockResolvedValue(withChecks({ agent: { live: true, working: true, address: "t1" } }));
  await open();
  await click("Merge into main");
  await click("Not yet");
  expect(b.reviewMerge).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("conflicts and uncommitted changes turn Merge off", async () => {
  b.reviewGet.mockResolvedValue(withChecks({ checks: [OK[0], { id: "conflicts", state: "bad", text: "Conflicts with main in a.py", detail: "" }, OK[2]] as ReviewData["checks"] }));
  await open();
  expect(screen.getByRole("button", { name: "Merge into main" })).toBeDisabled();
  cleanup();
  b.reviewGet.mockResolvedValue(withChecks({ checks: [...OK, { id: "uncommitted", state: "bad", text: "Has uncommitted changes", detail: "" }] as ReviewData["checks"] }));
  await open();
  expect(screen.getByRole("button", { name: "Merge into main" })).toBeDisabled();
});

test("a refused merge shows the brain's words and keeps the screen", async () => {
  b.reviewMerge.mockRejectedValue("builder-1 added commits since you looked. Look again first.");
  await open();
  await click("Merge into main");
  expect(screen.getByText(/Look again first/)).toBeInTheDocument();
  expect(onMerged).not.toHaveBeenCalled();
});

test("Discard asks first and says what goes", async () => {
  await open();
  await click("Discard");
  expect(screen.getByText(/removes its 3 commits and its worktree/)).toBeInTheDocument();
  await click("Keep it");
  expect(b.reviewDiscard).not.toHaveBeenCalled();
  await click("Discard");
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Discard" }));
  await flush();
  expect(b.reviewDiscard).toHaveBeenCalledWith("shop", "builder-1", "abc");
  expect(onDiscarded).toHaveBeenCalledWith("Discarded builder-1's work.");
});

test("Ask for changes sends the words and needs some", async () => {
  await open();
  await click("Ask for changes");
  expect(screen.getByRole("button", { name: "Send to builder-1" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("What should builder-1 change?"), { target: { value: "cap at 6s" } });
  await click("Send to builder-1");
  expect(b.reviewAsk).toHaveBeenCalledWith("shop", "builder-1", "cap at 6s");
  expect(screen.getByText("Sent to builder-1.")).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("Escape closes a question", async () => {
  await open();
  await click("Ask for changes");
  fireEvent.keyDown(window, { key: "Escape" });
  await flush();
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("Open pull request opens its address", async () => {
  await open();
  await click("Open pull request");
  expect(b.openLink).toHaveBeenCalledWith("https://example.test/pull/7");
});

test("a pushed branch with nowhere to open it says so", async () => {
  b.reviewPullRequest.mockResolvedValue({ url: null, detail: "The branch is pushed. Open the pull request on your git host." });
  await open();
  await click("Open pull request");
  expect(b.openLink).not.toHaveBeenCalled();
  expect(screen.getByText(/on your git host/)).toBeInTheDocument();
});

test("Open in editor goes to the first changed line, and a deleted file can't open", async () => {
  await open();
  await click("Open in editor");
  expect(b.reviewEditor).toHaveBeenCalledWith("shop", "builder-1", "retry.py", 14);
  cleanup();
  b.reviewGet.mockResolvedValue(withChecks({ files: [{ path: "retry.py", add: 0, del: 2, binary: false, missing: true }] }));
  await open();
  expect(screen.getByRole("button", { name: "Open in editor" })).toBeDisabled();
});

test("a binary file and a diff too big say so", async () => {
  b.reviewGet.mockResolvedValue(withChecks({ files: [{ path: "x.bin", add: 0, del: 0, binary: true, missing: false }], diff: "", notes: [] }));
  await open();
  expect(screen.getByText(/binary file/)).toBeInTheDocument();
  cleanup();
  b.reviewGet.mockResolvedValue(withChecks({ too_big: true, diff: "", notes: [] }));
  await open();
  expect(screen.getByText(/too big to show/)).toBeInTheDocument();
});

test("a branch that has gone says so, with the way back", async () => {
  b.reviewGet.mockRejectedValue("builder-1 has no branch to review.");
  await open();
  expect(screen.getByText("builder-1 has no branch to review.")).toBeInTheDocument();
  await click("Terminals");
  expect(onBack).toHaveBeenCalled();
});

test("a failed first load clears when the brain answers; a later blip keeps the view", async () => {
  b.reviewGet.mockRejectedValueOnce("offline");
  await open();
  expect(screen.getByText("offline")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.queryByText("offline")).toBeNull();
  b.reviewGet.mockRejectedValueOnce("blip");
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.queryByText("blip")).toBeNull();
  expect(screen.getByRole("heading", { name: "builder-1" })).toBeInTheDocument();
});

test("keeps polling for test results while open", async () => {
  await open();
  expect(b.reviewGet).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(b.reviewGet).toHaveBeenCalledTimes(2);
});

test("a file whose name has a space shows its diff", async () => {
  const diff = `diff --git a/my file.py b/my file.py
index 1111111..2222222 100644
--- a/my file.py\t
+++ b/my file.py\t
@@ -1,1 +1,1 @@
-a = 1
+a = 2
`;
  b.reviewGet.mockResolvedValue(withChecks({ diff, notes: [], files: [{ path: "my file.py", add: 1, del: 1, binary: false, missing: false }] }));
  await open();
  expect(screen.getByText("a = 2")).toBeInTheDocument();
});

test("note buttons wait while an action runs", async () => {
  let finish = () => {};
  b.reviewNote.mockReturnValue(new Promise((r) => { finish = () => r({ ok: true }); }));
  await open();
  fireEvent.click(screen.getAllByRole("button", { name: "Ask builder-1 to fix it" })[0]);
  await flush();
  for (const button of screen.getAllByRole("button", { name: "Ask builder-1 to fix it" })) expect(button).toBeDisabled();
  finish();
  await flush();
});

test("the first look asks for a fresh test run, later polls don't", async () => {
  await open();
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(b.reviewGet.mock.calls.map((c) => c[2])).toEqual([true, false]);
});

test("re-rendering doesn't restart a notice's wait", async () => {
  const done = vi.fn();
  const view = render(<Notice ms={1000} onDone={done}><b>Hello</b></Notice>);
  await act(async () => { await vi.advanceTimersByTimeAsync(600); });
  view.rerender(<Notice ms={1000} onDone={done}><b>Hello</b></Notice>);
  await act(async () => { await vi.advanceTimersByTimeAsync(600); });
  expect(done).toHaveBeenCalled();
});

test("a notice goes by itself", async () => {
  const done = vi.fn();
  render(<Notice ms={1000} onDone={done}>Hello</Notice>);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(done).toHaveBeenCalled();
});

test("the back link names where you came from", async () => {
  render(<Review team="shop" role="builder-1" from="Team" onBack={onBack} onMerged={onMerged} onDiscarded={onDiscarded} />);
  await act(async () => {});
  await click("Team");
  expect(onBack).toHaveBeenCalled();
});
