import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { BringOverFound, TeamDraft } from "./bridge";

const b = vi.hoisted(() => ({
  bringOverFind: vi.fn(), bringOverWorkspaces: vi.fn(), bringOverImport: vi.fn(), herdrImport: vi.fn(),
  pickScript: vi.fn(), pickFolder: vi.fn(),
}));
vi.mock("./bridge", async (orig) => ({ ...(await orig<typeof import("./bridge")>()), ...b }));

import BringOver from "./BringOver";

const DRAFT: TeamDraft = { id: "d1", project: "/Users/me/shop", task: "", roles: [], practices: {}, why: "", from: "bringover" };
const FOUND: BringOverFound = {
  projects: [
    { id: "conductor:/p/shop", tool: "conductor", label: "checkout-service", summary: "3 workspaces · Claude Code, Codex · 2 with unmerged work", count: 3, running: null },
    { id: "claude-squad:/p/ml", tool: "claude-squad", label: "ml-pipeline", summary: "2 sessions · Claude Code", count: 2, running: null },
    { id: "herdr:running:shop-api", tool: "herdr", label: "shop-api", summary: "Running in herdr · 9 agents", count: 9,
      running: "Still running in herdr: stop it there first." },
    { id: "other:/p/billing", tool: "other", label: "billing-web", summary: "2 workspaces · Codex", count: 2, running: null },
  ],
  unreadable: [], problems: [], looked_for: ["Conductor", "Vibe Kanban", "Claude Squad", "herdr"],
};
const WORKSPACES = {
  label: "checkout-service", tool: "Conductor",
  rows: [
    { name: "tokyo", branch: "feature/retry-backoff", ticked: true, becomes: "Becomes builder-1 · Claude Code, Opus · 4 commits not merged", note: "" },
    { name: "lisbon", branch: "fix/tax-rounding", ticked: true, becomes: "Becomes builder-2 · Codex · 1 commit not merged", note: "" },
    { name: "osaka", branch: "chore/deps", ticked: false, becomes: "Becomes builder-3 · Claude Code · nothing new yet",
      note: "Left out: nothing new on its branch. Tick it to bring it anyway." },
  ],
  scripts: [
    { which: "setup" as const, line: "npm ci", becomes: "runs in each new agent’s copy" },
    { which: "run" as const, line: "npm run dev", becomes: "becomes the live preview" },
  ],
};
const flush = () => act(async () => {});

beforeEach(() => {
  vi.clearAllMocks();
  b.bringOverFind.mockResolvedValue(FOUND);
  b.bringOverWorkspaces.mockResolvedValue(WORKSPACES);
  b.bringOverImport.mockResolvedValue(DRAFT);
  b.herdrImport.mockResolvedValue({ ...DRAFT, from: "herdr" });
});
afterEach(cleanup);

async function open() {
  const onImported = vi.fn();
  const onClose = vi.fn();
  render(<BringOver onClose={onClose} onImported={onImported} />);
  await flush();
  return { onImported, onClose };
}

test("lists what other tools left, grouped by tool, and says when a team still runs there", async () => {
  await open();
  for (const h of ["From Conductor", "From Claude Squad", "From herdr", "Other agent work found"]) expect(screen.getByText(h)).toBeTruthy();
  expect(screen.getByText("from its branches and agent history")).toBeTruthy();
  expect(screen.getByText("3 workspaces · Claude Code, Codex · 2 with unmerged work")).toBeTruthy();
  expect(screen.getAllByText("Still running in herdr: stop it there first.")).toHaveLength(1);
  expect(screen.getAllByText("Bring over")).toHaveLength(4);
});

test("a herdr team goes straight to New team, as before", async () => {
  const { onImported } = await open();
  fireEvent.click(screen.getAllByText("Bring over")[2]);
  await flush();
  expect(b.herdrImport).toHaveBeenCalledWith("running:shop-api");
  expect(onImported).toHaveBeenCalled();
});

test("what it becomes: ticks follow the person, builders renumber, scripts asked once, and Back returns", async () => {
  const { onImported } = await open();
  fireEvent.click(screen.getAllByText("Bring over")[0]);
  await flush();
  expect(screen.getByText("Bring over checkout-service")).toBeTruthy();
  expect(screen.getByText("Scripts from conductor.json")).toBeTruthy();
  expect(screen.getByText("Left out: nothing new on its branch. Tick it to bring it anyway.")).toBeTruthy();
  expect(screen.getByText("Bring over 2 agents")).toBeTruthy();
  fireEvent.click(screen.getAllByRole("checkbox")[0]); // untick tokyo: lisbon becomes builder-1
  expect(screen.getByText("Becomes builder-1 · Codex · 1 commit not merged")).toBeTruthy();
  fireEvent.click(screen.getAllByRole("checkbox")[2]); // tick osaka
  expect(screen.getByText("Becomes builder-2 · Claude Code · nothing new yet")).toBeTruthy();
  expect(screen.queryByLabelText("Run this before a copy is archived")).toBeNull(); // no archive script here
  fireEvent.click(screen.getByLabelText("Run this setup in each new agent’s copy"));
  fireEvent.click(screen.getByText("Bring over 2 agents"));
  await flush();
  expect(b.bringOverImport).toHaveBeenCalledWith("conductor:/p/shop", ["lisbon", "osaka"]);
  expect(onImported).toHaveBeenCalledWith({ ...DRAFT, allow_scripts: ["setup"] });
});

test("work from a tool Assieme doesn't know says where it was found, not a tool name", async () => {
  b.bringOverWorkspaces.mockResolvedValue({ ...WORKSPACES, label: "billing-web", tool: "another tool" });
  await open();
  fireEvent.click(screen.getAllByText("Bring over")[3]);
  await flush();
  expect(screen.getByText(/^Found from its branches and agent history\..*nothing in the tool it came from changes\.$/)).toBeTruthy();
});

test("Back returns to the list, and nothing ticked can't be brought over", async () => {
  await open();
  fireEvent.click(screen.getAllByText("Bring over")[0]);
  await flush();
  fireEvent.click(screen.getAllByRole("checkbox")[0]);
  fireEvent.click(screen.getAllByRole("checkbox")[1]);
  expect((screen.getByText("Bring over 0 agents") as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByText("Back"));
  expect(screen.getByText("Bring your team over")).toBeTruthy();
});

test("nothing found names the tools and offers a project folder, which is looked in", async () => {
  b.bringOverFind.mockResolvedValueOnce({ ...FOUND, projects: [] });
  b.pickFolder.mockResolvedValue("/Users/me/code/shop");
  await open();
  expect(screen.getByText(/No agent work from other tools on this Mac. Assieme looks for Conductor, Vibe Kanban, Claude Squad and herdr/)).toBeTruthy();
  expect(screen.getByText("Know where your team’s work is?")).toBeTruthy();
  fireEvent.click(screen.getByText("Choose a project folder…"));
  await flush();
  expect(b.bringOverFind).toHaveBeenLastCalledWith("/Users/me/code/shop");
  expect(screen.getByText("checkout-service")).toBeTruthy();
});

test("a launch script still imports through herdr's reader, and a failure says why", async () => {
  b.pickScript.mockResolvedValue("/Users/me/notes.txt");
  b.herdrImport.mockRejectedValue(new Error("No Claude Code or Codex agents were found in it."));
  const { onImported } = await open();
  fireEvent.click(screen.getByText("a launch script…"));
  await flush();
  expect(b.herdrImport).toHaveBeenCalledWith("script:/Users/me/notes.txt");
  expect(screen.getByRole("alert").textContent).toContain("No Claude Code or Codex agents");
  expect(onImported).not.toHaveBeenCalled();
});

test("a tool that couldn't be read says so, and Escape closes", async () => {
  b.bringOverFind.mockResolvedValue({ ...FOUND, problems: ["Couldn't read Claude Squad's sessions. Its worktrees are still listed under Other agent work."] });
  const { onClose } = await open();
  expect(screen.getByText(/Couldn't read Claude Squad's sessions/)).toBeTruthy();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(onClose).toHaveBeenCalled();
});
