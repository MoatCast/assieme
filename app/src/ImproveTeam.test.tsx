import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const b = vi.hoisted(() => ({ listTeams: vi.fn(), teamImprove: vi.fn(), teamImproveApply: vi.fn(), activityUndo: vi.fn(), herdrImport: vi.fn() }));
vi.mock("./bridge", () => b);

import ImproveTeam from "./ImproveTeam";

const flush = () => act(async () => {});
const finding = (id: string, kind: "apply" | "review", extra = {}) =>
  ({ id, kind, title: `${id} title`, detail: `${id} detail`, restart: false, copy: null, draft: null, to: ["a"], ...extra });
const found = (name: string, agents = 2) => ({ name, kind: "found", agents });

beforeEach(() => {
  vi.resetAllMocks();
  b.listTeams.mockResolvedValue({ teams: [found("shop"), { name: "mine", kind: "assieme", agents: 3 }], found_agents: [] });
  b.teamImprove.mockResolvedValue({
    team: "shop", found: true, in_place: ["safe permissions"],
    findings: [finding("practices", "apply"), finding("folder:a", "review", { restart: true, draft: "running:shop" })],
  });
  b.teamImproveApply.mockImplementation(async (_t: string, ids: string[]) => ({ ok: true, applied: ids.map((id) => ({ id, entry: `e-${id}` })) }));
  b.activityUndo.mockResolvedValue({ ok: true, detail: "" });
});
afterEach(cleanup);

async function show(onDraft = vi.fn()) {
  render(<ImproveTeam onClose={vi.fn()} onDraft={onDraft} />);
  await flush();
  await flush();
}

test("one team started outside Assieme opens straight away, with what is in place", async () => {
  await show();
  expect(b.teamImprove).toHaveBeenCalledWith("shop");
  screen.getByText("shop · started outside Assieme");
  screen.getByText(/2 things would help/);
  screen.getByText("Already in place: safe permissions.");
});

test("Apply gives the rules, then Undo takes them back through Activity", async () => {
  await show();
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  await flush();
  expect(b.teamImproveApply).toHaveBeenCalledWith("shop", ["practices"]);
  screen.getByText("Applied");
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await flush();
  expect(b.activityUndo).toHaveBeenCalledWith("e-practices");
  screen.getByRole("button", { name: "Apply" });
});

test("the main button applies every Apply and opens the first Review; a restart fix opens in New team", async () => {
  const onDraft = vi.fn();
  b.herdrImport.mockResolvedValue({ id: "d1", roles: [] });
  await show(onDraft);
  fireEvent.click(screen.getByRole("button", { name: "Apply 1 and review 1" }));
  await flush();
  expect(b.teamImproveApply).toHaveBeenCalledWith("shop", ["practices"]);
  screen.getByText(/needs a restart/);
  fireEvent.click(screen.getByRole("button", { name: "Open in New team" }));
  await flush();
  expect(b.herdrImport).toHaveBeenCalledWith("running:shop");
  expect(onDraft).toHaveBeenCalledWith({ id: "d1", roles: [] });
});

test("opening a reviewer's fix in New team starts that reviewer read-only", async () => {
  const onDraft = vi.fn();
  b.teamImprove.mockResolvedValue({ team: "shop", found: true, in_place: [],
    findings: [finding("reviewer:rev", "review", { restart: true, draft: "running:shop" })] });
  b.herdrImport.mockResolvedValue({ id: "d1", roles: [{ name: "rev", checkout: "branch" }, { name: "b", checkout: "branch" }] });
  await show(onDraft);
  fireEvent.click(screen.getByRole("button", { name: "Review" }));
  fireEvent.click(screen.getByRole("button", { name: "Open in New team" }));
  await flush();
  expect(onDraft.mock.calls[0][0].roles).toEqual([{ name: "rev", checkout: "read-only" }, { name: "b", checkout: "branch" }]);
});

test("rules to paste for an agent Assieme can't reach", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText } });
  b.teamImprove.mockResolvedValue({ team: "shop", found: true, in_place: [],
    findings: [finding("practices", "review", { copy: "assieme: your owner added these.\n- Rule one" })] });
  await show();
  fireEvent.click(screen.getByRole("button", { name: "Review" }));
  screen.getByText("- Rule one");
  fireEvent.click(screen.getByRole("button", { name: "Copy the rules" }));
  await flush();
  expect(writeText).toHaveBeenCalledWith("assieme: your owner added these.\n- Rule one");
  screen.getByText("Copied");
});

test("several teams: choose one first", async () => {
  b.listTeams.mockResolvedValue({ teams: [found("shop"), found("docs", 1)], found_agents: [] });
  await show();
  expect(b.teamImprove).not.toHaveBeenCalled();
  screen.getByText("1 agent");
  fireEvent.click(screen.getAllByRole("button", { name: "Check" })[1]);
  await flush();
  expect(b.teamImprove).toHaveBeenCalledWith("docs");
});

test("nothing to improve", async () => {
  b.teamImprove.mockResolvedValue({ team: "shop", found: true, in_place: ["team practices"], findings: [] });
  await show();
  screen.getByRole("dialog", { name: "Nothing to improve" });
  screen.getByText("Already in place: team practices.");
});
