import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Preview } from "./bridge";

const b = vi.hoisted(() => ({ openLink: vi.fn(), previewList: vi.fn(), previewStart: vi.fn(), previewStop: vi.fn() }));
vi.mock("./bridge", () => b);

import { PreviewGroup, usePreviews } from "./Preview";

const flush = () => act(async () => {});
const P = (more: Partial<Preview> = {}): Preview => ({ team: "shop", role: "builder-1", project: "checkout", state: "running",
  command: "npm run dev", url: "http://localhost:5174", port: 5174, reason: "", output: "", fix: null, ...more });

function Harness() {
  const pv = usePreviews(1000);
  return <div className="review"><PreviewGroup team="shop" role="builder-1" pv={pv} />{pv.sheet}</div>;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  b.previewList.mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

test("no command found: the sheet asks once, and the typed command starts it", async () => {
  b.previewStart.mockResolvedValueOnce({ team: "shop", role: "builder-1", project: "checkout", state: "needs_command" });
  b.previewStart.mockResolvedValueOnce(P({ state: "starting", url: null }));
  render(<Harness />);
  await flush();
  expect(screen.getByText("See builder-1's branch running before you merge it.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Start preview" }));
  await flush();
  expect(screen.getByRole("dialog", { name: "How does checkout start its dev server?" })).toBeTruthy();
  expect(screen.getByText(/couldn't find one in the project/)).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: " python3 -m http.server $PORT " } });
  const starts = screen.getAllByRole("button", { name: "Start preview" });
  fireEvent.click(starts[starts.length - 1]);
  await flush();
  expect(b.previewStart).toHaveBeenLastCalledWith("shop", "builder-1", "python3 -m http.server $PORT", false);
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("a preview started here opens once when it runs; Open preview opens it again and Stop stops it", async () => {
  b.previewStart.mockResolvedValue(P({ state: "starting", url: null }));
  b.previewList.mockResolvedValue([]);
  render(<Harness />);
  await flush();
  b.previewList.mockResolvedValue([P({ state: "starting", url: null })]);
  fireEvent.click(screen.getByRole("button", { name: "Start preview" }));
  await flush();
  expect(screen.getByText("npm run dev")).toBeTruthy();
  expect(b.openLink).not.toHaveBeenCalled();
  b.previewList.mockResolvedValue([P()]);
  await act(async () => { vi.advanceTimersByTime(1000); });
  await act(async () => { vi.advanceTimersByTime(1000); });
  expect(b.openLink).toHaveBeenCalledTimes(1);
  expect(screen.getByText("localhost:5174")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Open preview" }));
  expect(b.openLink).toHaveBeenCalledTimes(2);
  b.previewStop.mockResolvedValue({ stopped: true });
  fireEvent.click(screen.getByRole("button", { name: "Stop" }));
  await flush();
  expect(b.previewStop).toHaveBeenCalledWith("shop", "builder-1");
});

test("a preview that didn't start says why, shows its last lines, and names what Install and try again runs", async () => {
  b.previewList.mockResolvedValue([P({ state: "failed", url: null, fix: "install", install: "npm install",
    reason: "npm run dev stopped: vite isn't installed in builder-1's worktree.", output: "sh: vite: command not found" })]);
  b.previewStart.mockResolvedValue(P({ state: "starting", url: null }));
  render(<Harness />);
  await flush();
  expect(screen.getByText("The preview didn't start")).toBeTruthy();
  expect(screen.getByText("sh: vite: command not found")).toBeTruthy();
  expect(screen.getByText(/in builder-1's worktree, which downloads its packages/).textContent).toContain("Runs npm install");
  fireEvent.click(screen.getByRole("button", { name: "Install and try again" }));
  await flush();
  expect(b.previewStart).toHaveBeenCalledWith("shop", "builder-1", null, true);
  fireEvent.click(screen.getByRole("button", { name: "Change the start command" }));
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("npm run dev");
});

test("a refusal is shown, not swallowed", async () => {
  b.previewStart.mockRejectedValue(new Error("builder-1 has no branch to review."));
  render(<Harness />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Start preview" }));
  await flush();
  expect(screen.getByRole("alert").textContent).toContain("builder-1 has no branch to review.");
});
