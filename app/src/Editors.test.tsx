import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { RoleDraft } from "./bridge";
import { KINDS, TABLE } from "./editorFixtures";

const b = vi.hoisted(() => ({ improveBrief: vi.fn(), ruleDraft: vi.fn() }));
vi.mock("./bridge", async (orig) => ({ ...(await orig<typeof import("./bridge")>()), ...b }));

import BriefEditor from "./BriefEditor";
import PermissionsEditor from "./PermissionsEditor";

const flush = () => act(async () => {});
const builder: RoleDraft = { name: "builder-1", agent: "claude", job: "builds", checkout: "branch", parent: "planner",
  brief: "Builder brief.", kind: "builder", level: "safe", owns: [], rules: [] };
const planner: RoleDraft = { ...builder, name: "planner", parent: null, kind: "planner", checkout: "read-only", level: "read-only" };
const INSTALLED = [["claude", "Claude Code"], ["codex", "Codex"]] as const;

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

function brief(role = builder) {
  const onSave = vi.fn();
  render(<BriefEditor role={role} roles={[planner, role]} project="/Users/me/shop" installed={INSTALLED} kinds={KINDS}
    table={TABLE} asker="Claude Code" onSave={onSave} onCancel={vi.fn()} />);
  return onSave;
}

test("a role chip sets checkout, level and the template brief, but keeps what the person wrote", () => {
  const onSave = brief();
  fireEvent.click(screen.getByText("Reviewer"));
  fireEvent.click(screen.getByText("Save brief"));
  expect(onSave).toHaveBeenLastCalledWith(expect.objectContaining({
    kind: "reviewer", checkout: "read-only", level: "read-only", brief: "Reviewer brief.", job: "reviews" }));
  cleanup();
  const again = brief({ ...builder, brief: "My own words." });
  fireEvent.click(screen.getByText("Tester"));
  fireEvent.click(screen.getByText("Save brief"));
  expect(again).toHaveBeenLastCalledWith(expect.objectContaining({ kind: "tester", brief: "My own words." }));
});

test("owned files: added, checked and removed", () => {
  const onSave = brief();
  fireEvent.click(screen.getByText("+ Add"));
  const box = screen.getByLabelText("A file it owns, from the project folder");
  fireEvent.change(box, { target: { value: "../secrets" } });
  fireEvent.keyDown(box, { key: "Enter" });
  expect(screen.getByRole("alert").textContent).toMatch(/isn’t a path inside the project/);
  fireEvent.change(box, { target: { value: "./payments/retry.py" } });
  fireEvent.keyDown(box, { key: "Enter" });
  fireEvent.click(screen.getByText("Save brief"));
  expect(onSave).toHaveBeenLastCalledWith(expect.objectContaining({ owns: ["payments/retry.py"] }));
  fireEvent.click(screen.getByLabelText("builder-1 no longer owns payments/retry.py"));
  fireEvent.click(screen.getByText("Save brief"));
  expect(onSave).toHaveBeenLastCalledWith(expect.objectContaining({ owns: [] }));
});

test("reports to never offers itself or anyone below it", () => {
  brief({ ...builder, name: "lead", parent: null });
  const options = [...(screen.getByLabelText("Reports to") as HTMLSelectElement).options].map((o) => o.value);
  expect(options).toEqual(["", "planner"]);
});

test("Improve this brief fills the box, with Undo; nothing saved until Save brief", async () => {
  b.improveBrief.mockResolvedValue({ brief: "Sharper brief.\n" });
  const onSave = brief();
  fireEvent.click(screen.getByText("Improve this brief with Claude"));
  await flush();
  expect(b.improveBrief).toHaveBeenCalledWith("/Users/me/shop", expect.objectContaining({ name: "builder-1", brief: "Builder brief." }));
  expect((screen.getByLabelText("Brief for builder-1") as HTMLTextAreaElement).value).toBe("Sharper brief.\n");
  expect(onSave).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Undo"));
  expect((screen.getByLabelText("Brief for builder-1") as HTMLTextAreaElement).value).toBe("Builder brief.");
});

test("Improve failing says why and keeps the brief", async () => {
  b.improveBrief.mockRejectedValue(new Error("suggest: Couldn't improve the brief: no answer within 3 minutes."));
  brief();
  fireEvent.click(screen.getByText("Improve this brief with Claude"));
  await flush();
  expect(screen.getByRole("alert").textContent).toBe("Couldn't improve the brief: no answer within 3 minutes.");
  expect((screen.getByLabelText("Brief for builder-1") as HTMLTextAreaElement).value).toBe("Builder brief.");
});

test("Change opens the permissions; Save there comes back to the brief with the new level", () => {
  const onSave = brief();
  expect(screen.getByText("Safe: edit its files, run tests, commit locally.")).toBeTruthy();
  fireEvent.click(screen.getByText("Change"));
  fireEvent.click(screen.getByRole("radio", { name: /Trusted/ }));
  fireEvent.click(screen.getByText("Save"));
  expect(screen.getByText("Trusted: also install packages and go online. Never push.")).toBeTruthy();
  fireEvent.click(screen.getByText("Save brief"));
  expect(onSave).toHaveBeenLastCalledWith(expect.objectContaining({ level: "trusted" }));
});

function perms(role = builder) {
  const onSave = vi.fn();
  render(<PermissionsEditor role={role} roles={[planner, role]} project="/Users/me/shop" table={TABLE} asker="Claude Code"
    onSave={onSave} onCancel={vi.fn()} />);
  return onSave;
}

test("the level's lists, with Safe recommended for a builder", () => {
  perms();
  expect(screen.getByRole("radio", { name: /^Safe, recommended/ }).getAttribute("aria-checked")).toBe("true");
  expect(screen.getByText("Edit any file in its own worktree")).toBeTruthy();
  expect(screen.getByText("Message").textContent).toBe("Message planner");
  expect(screen.getByText("you merge in Review")).toBeTruthy();
});

test("an item's note says what it covers for this agent, and Always allow is this agent's own", () => {
  perms();
  expect(screen.getByText("claude note")).toBeTruthy();
  expect(screen.getByText(/Allow once, Always allow for this agent and Block/)).toBeTruthy();
  cleanup();
  perms({ ...builder, agent: "codex" });
  expect(screen.getByText("codex note")).toBeTruthy();
});

test("a rule in words is shown as the program rule before Add adds it", async () => {
  b.ruleDraft.mockResolvedValue({ decision: "block", program: ["build/cloud/run.sh"], words: "Don’t run the cloud launcher" });
  const onSave = perms();
  fireEvent.change(screen.getByLabelText("Add a rule"), { target: { value: "Don’t run the cloud launcher" } });
  fireEvent.click(screen.getByText("Add"));
  await flush();
  expect(b.ruleDraft).toHaveBeenCalledWith("/Users/me/shop", "Don’t run the cloud launcher");
  expect(screen.getByText("block program: build/cloud/run.sh")).toBeTruthy();
  expect(onSave).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Add"));  // the person's click, after seeing what it becomes
  expect(screen.getByLabelText("Remove the rule Don’t run the cloud launcher")).toBeTruthy();
  fireEvent.click(screen.getByText("Save"));
  expect(onSave).toHaveBeenCalledWith({ level: "safe", rules: [{ decision: "block", program: ["build/cloud/run.sh"], words: "Don’t run the cloud launcher" }] });
});

test("changing the words after the check asks again", async () => {
  b.ruleDraft.mockResolvedValue({ decision: "allow", program: ["make", "lint"], words: "lint" });
  perms();
  const box = screen.getByLabelText("Add a rule");
  fireEvent.change(box, { target: { value: "lint" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await flush();
  fireEvent.change(box, { target: { value: "lint and format" } });
  expect(screen.queryByText("allow program: make lint")).toBeNull();
  fireEvent.click(screen.getByText("Add"));
  await flush();
  expect(b.ruleDraft).toHaveBeenCalledTimes(2);
});

test("a refused rule says why", async () => {
  b.ruleDraft.mockRejectedValue(new Error("suggest: Couldn't turn that into a rule: Pushing stays blocked at every level. You merge in Review."));
  perms();
  fireEvent.change(screen.getByLabelText("Add a rule"), { target: { value: "let it push" } });
  fireEvent.click(screen.getByText("Add"));
  await flush();
  expect(screen.getByText(/Pushing stays blocked at every level/)).toBeTruthy();
});
