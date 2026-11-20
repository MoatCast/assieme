import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { RoleDraft } from "./bridge";
import StartStep from "./StartStep";
import { PRACTICES } from "./practicesFixture";

beforeEach(() => vi.useFakeTimers({ toFake: ["Date"] }));
afterEach(() => { cleanup(); vi.useRealTimers(); });
const r = (name: string, agent = "claude"): RoleDraft => ({ name, agent: agent as RoleDraft["agent"], job: "", checkout: "branch", parent: null, brief: "" });
const PANEL = { steps: ["project", "task", "team", "start"] as ("project" | "task" | "team" | "start")[], at: "start" as const, answers: {}, onGo: () => {} };
const AUTO = { nudge: "auto", compact: "auto", tell: "auto", quiet: "auto" } as const;

function step(p: { roles?: RoleDraft[]; value?: Record<string, boolean>; fixes?: typeof AUTO | null; pending?: boolean; error?: string; machine?: string | null } = {}) {
  const h = { onRules: vi.fn(), onEditAgents: vi.fn(), onBigger: vi.fn(), onBack: vi.fn(), onStart: vi.fn() };
  render(<StartStep panel={PANEL} practices={PRACTICES} value={p.value ?? {}} roles={p.roles ?? [r("builder"), r("reviewer", "codex")]}
    machine={p.machine ?? null} fixes={p.fixes === undefined ? AUTO : p.fixes} pending={p.pending ?? false} error={p.error ?? ""} {...h} />);
  return h;
}

test("the three rows show what Assieme does", () => {
  step();
  screen.getByRole("heading", { name: "What Assieme does for this team" });
  screen.getByText("It starts every agent with these and keeps watching while they work. All of it is on.");
  screen.getByText("Before they start");
  screen.getByText(/^Each agent gets 10 team rules:/);
  screen.getByText("While they work");
  screen.getByText(/Assieme fixes it on its own and lists the result/);
  screen.getByText("Always");
  screen.getByText("Each agent works on its own copy. No agent pushes; you merge. Permission prompts always come to you.");
  screen.getByText("2 agents on This Mac");
  screen.getByText("builder, reviewer · Claude Code and Codex");
  screen.getByText("You can change these any time in Settings.");
});

test("ask-first fixes say so and name Settings › Fixes", () => {
  step({ fixes: { ...AUTO, compact: "ask" } as never });
  screen.getByText(/Assieme tells you and offers a fix\. Change this in Settings › Fixes\./);
});

test("the team line names the machine", () => {
  step({ machine: "gpu-box" });
  screen.getByText("2 agents on gpu-box");
});

test("the steward line shows from 4 agents and adds one to Start", () => {
  step();
  expect(screen.queryByText(/A steward agent can settle routine questions/)).toBeNull();
  cleanup();
  const four = ["planner", "builder-1", "builder-2", "reviewer"].map((n) => r(n));
  const h = step({ roles: four });
  screen.getByText("A steward agent can settle routine questions, so only real decisions reach you.");
  fireEvent.click(screen.getByRole("button", { name: "Add a steward ›" }));
  expect(h.onBigger).toHaveBeenCalledWith("steward", true);
  cleanup();
  const g = step({ roles: four, value: { steward: true } });
  screen.getByText("Steward added");
  screen.getByRole("button", { name: "Start 5 agents" });
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  expect(g.onBigger).toHaveBeenCalledWith("steward", false);
});

test("the research line shows only with a researcher", () => {
  step();
  expect(screen.queryByText("Research pairs")).toBeNull();
  cleanup();
  const h = step({ roles: [r("researcher"), r("builder")] });
  fireEvent.click(screen.getByRole("button", { name: "Add Research pairs ›" }));
  expect(h.onBigger).toHaveBeenCalledWith("research-pairs", true);
});

test("Edit agents and Review team rules call their handlers", () => {
  const h = step();
  fireEvent.click(screen.getByRole("button", { name: "Edit agents ›" }));
  expect(h.onEditAgents).toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Review team rules ›" }));
  expect(h.onRules).toHaveBeenCalled();
});

test("See all 5 opens the always-on list", () => {
  step();
  fireEvent.click(screen.getByRole("button", { name: "See all 5 ›" }));
  expect(screen.getByRole("dialog", { name: "Always on" }).textContent).toContain("3Basic 3");
});

test("Start is pressed once, and is off while pending", async () => {
  const h = step();
  const start = screen.getByRole("button", { name: "Start 2 agents" });
  fireEvent.click(start);
  expect(h.onStart).toHaveBeenCalledTimes(1);
  cleanup();
  step({ pending: true, error: "not installed: codex." });
  expect(screen.getByRole("button", { name: "Start 2 agents" })).toBeDisabled();
  screen.getByText("not installed: codex.");
  await act(async () => {});
});

test("Back goes back", () => {
  const h = step();
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(h.onBack).toHaveBeenCalled();
});
