import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { ProjectCheck } from "./bridge";

const b = vi.hoisted(() => ({ pickFolder: vi.fn(), pickNewFolder: vi.fn(), folderCreate: vi.fn(), folderPreview: vi.fn(), folderSave: vi.fn() }));
vi.mock("./bridge", async (orig) => ({ ...(await orig<typeof import("./bridge")>()), ...b }));

import ProjectStep from "./ProjectStep";

const flush = () => act(async () => {});
const PANEL = { steps: ["project", "task", "team", "start"] as const, at: "project" as const, answers: {}, onGo: () => {} };
const SETUP = { ok: false, setup: true, reason: "This folder has no saved version yet." };
const TOOLS = { ok: false, tools: true, reason: "This needs Apple’s developer tools first." };

beforeEach(() => {
  vi.clearAllMocks();
  b.pickFolder.mockResolvedValue("/Users/me/shop");
  b.folderPreview.mockResolvedValue({ files: 214, sample: ["a"], left_out: [], first: true, empty: false });
  b.folderSave.mockResolvedValue({ ok: true });
});
afterEach(cleanup);

function step(p: { project?: string; check?: ProjectCheck | null; error?: string; recent?: string[]; machine?: string | null } = {}) {
  const h = { onUse: vi.fn(), onRecheck: vi.fn(), onInstallTools: vi.fn(), onContinueAnyway: vi.fn(), onCancel: vi.fn() };
  const el = (q: typeof p) => (
    <ProjectStep panel={{ ...PANEL, steps: [...PANEL.steps] }} machine={q.machine ?? null} recent={q.recent ?? []}
      project={q.project ?? ""} check={q.check ?? null} error={q.error ?? ""} {...h} />
  );
  const { rerender } = render(el(p));
  return { ...h, rerender: (q: typeof p) => rerender(el(q)) };
}

test("choosing a folder hands it over to be checked", async () => {
  const h = step();
  screen.getByRole("heading", { name: "Which project should the team work on?" });
  screen.getByText("Choosing a folder moves to the next step.");
  fireEvent.click(screen.getByRole("button", { name: "Choose a folder…" }));
  await flush();
  expect(h.onUse).toHaveBeenCalledWith("/Users/me/shop");
});

test("going Back to Project with a good folder stays there", async () => {
  const h = step({ project: "/Users/me/shop", check: { ok: true } });
  await flush();
  expect(h.onUse).not.toHaveBeenCalled();
  expect(h.onContinueAnyway).not.toHaveBeenCalled();
  screen.getByText("~/shop");
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(h.onContinueAnyway).toHaveBeenCalledTimes(1);
});

test("a recent project is a button that uses that folder", () => {
  const h = step({ recent: ["/Users/me/recipes", "/Users/me/shop"] });
  fireEvent.click(screen.getByRole("button", { name: /recipes/ }));
  expect(h.onUse).toHaveBeenCalledWith("/Users/me/recipes");
  screen.getByText("~/shop");
});

test("developer tools: the install button, then a recheck that offers Continue (no move on by itself)", () => {
  const h = step({ project: "/Users/me/shop", check: TOOLS });
  expect(screen.getByRole("alert")).toHaveTextContent("Assieme needs Apple’s developer tools to keep versions of your project. macOS asks you to confirm.");
  fireEvent.click(screen.getByRole("button", { name: "Install developer tools" }));
  expect(h.onInstallTools).toHaveBeenCalled();
  h.rerender({ project: "/Users/me/shop", check: { ok: true } });
  expect(h.onContinueAnyway).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(h.onContinueAnyway).toHaveBeenCalled();
});

test("an installer that can't open says so", () => {
  step({ project: "/Users/me/shop", check: TOOLS, error: "xcode-select: not found" });
  screen.getByText("xcode-select: not found");
});

test("a folder with no saved version shows the block, and Save and continue rechecks", async () => {
  const h = step({ project: "/Users/me/shop", check: SETUP });
  await flush();
  screen.getByText("This folder has no saved version yet");
  fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
  await flush();
  expect(b.folderSave).toHaveBeenCalledWith("/Users/me/shop");
  expect(h.onRecheck).toHaveBeenCalled();
});

test("uncommitted files hold the step with Save 3 files and Continue without them", async () => {
  const h = step({ project: "/Users/me/shop", check: { ok: true, uncommitted: 3 } });
  screen.getByText("3 files changed since the last saved version. Agents won’t see these changes.");
  expect(b.folderPreview).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Continue without them" }));
  expect(h.onContinueAnyway).toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Save 3 files" }));
  await flush();
  expect(b.folderSave).toHaveBeenCalledWith("/Users/me/shop");
  expect(h.onRecheck).toHaveBeenCalled();
  h.rerender({ project: "/Users/me/shop", check: { ok: true, uncommitted: 1 } });
  screen.getByText("1 file changed since the last saved version. Agents won’t see these changes.");
});

test("too large offers Choose another folder", async () => {
  const h = step({ project: "/Users/me", check: { ok: false, too_large: true, reason: "This folder is very large." } });
  screen.getByText("This folder is very large.");
  expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Choose another folder" }));
  await flush();
  expect(h.onUse).toHaveBeenCalledWith("/Users/me/shop");
});

test("a check or Start error shows under the folder, and Choose another… tries again", async () => {
  const h = step({ project: "/Users/me/shop", error: "service went away" });
  screen.getByText("service went away");
  fireEvent.click(screen.getByRole("button", { name: "Choose another…" }));
  await flush();
  expect(h.onUse).toHaveBeenCalledWith("/Users/me/shop");
});

test("while the folder is checked it says so", () => {
  step({ project: "/Users/me/shop" });
  screen.getByText("Checking this folder…");
  expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
});

test("a picker that fails says so", async () => {
  b.pickFolder.mockRejectedValue(new Error("dialog refused"));
  step();
  fireEvent.click(screen.getByRole("button", { name: "Choose a folder…" }));
  await flush();
  screen.getByText("dialog refused");
});

test("New folder creates it and uses it", async () => {
  b.pickNewFolder.mockResolvedValue("/Users/me/recipes");
  b.folderCreate.mockResolvedValue({ path: "/Users/me/recipes" });
  const h = step();
  fireEvent.click(screen.getByRole("button", { name: "New folder…" }));
  await flush();
  expect(b.folderCreate).toHaveBeenCalledWith("/Users/me/recipes");
  expect(h.onUse).toHaveBeenCalledWith("/Users/me/recipes");
});

test("New folder: a cancelled picker does nothing; a refusal shows next to the buttons", async () => {
  b.pickNewFolder.mockResolvedValue(null);
  const h = step();
  fireEvent.click(screen.getByRole("button", { name: "New folder…" }));
  await flush();
  expect(b.folderCreate).not.toHaveBeenCalled();
  b.pickNewFolder.mockResolvedValue("/Users/me/shop");
  b.folderCreate.mockRejectedValue("That folder already exists.");
  fireEvent.click(screen.getByRole("button", { name: "New folder…" }));
  await flush();
  screen.getByText("That folder already exists.");
  expect(h.onUse).not.toHaveBeenCalled();
});

test("on a remote machine the folder is typed", () => {
  const h = step({ machine: "devbox" });
  const box = screen.getByLabelText("Project folder");
  fireEvent.change(box, { target: { value: "~/shop" } });
  fireEvent.keyDown(box, { key: "Enter" });
  expect(h.onUse).toHaveBeenCalledWith("~/shop");
  expect(screen.queryByRole("button", { name: "Choose a folder…" })).toBeNull();
});

test("Cancel cancels", () => {
  const h = step();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(h.onCancel).toHaveBeenCalled();
});
