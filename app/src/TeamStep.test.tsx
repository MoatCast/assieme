import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { AgentKind, Preset, RoleDraft } from "./bridge";

const b = vi.hoisted(() => ({ suggestTeam: vi.fn() }));
vi.mock("./bridge", async (orig) => ({ ...(await orig<typeof import("./bridge")>()), ...b }));

import TeamStep from "./TeamStep";

const flush = () => act(async () => {});
const r = (name: string): RoleDraft => ({ name, agent: "claude", job: "", checkout: "branch", parent: null, brief: "" });
const PRESETS: Preset[] = [
  { id: "one-agent", label: "One agent", roles: [r("builder")] },
  { id: "builder-reviewer", label: "Builder and reviewer", roles: [r("builder"), r("reviewer")] },
  { id: "full", label: "Full team", roles: ["planner", "builder-1", "builder-2", "builder-3", "reviewer", "tester"].map(r) },
];
const LAST: Preset = { id: "checkout-service", label: "Same as last time", roles: ["planner", "builder-1", "builder-2", "reviewer"].map(r) };
const PANEL = { steps: ["project", "task", "team", "start"] as ("project" | "task" | "team" | "start")[], at: "team" as const, answers: {}, onGo: () => {} };

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ toFake: ["Date"] });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });
const later = () => vi.setSystemTime(new Date(Date.now() + 400));

function step(p: { last?: Preset | null; installed?: AgentKind[]; task?: string } = {}) {
  const h = { onPick: vi.fn(), onSuggested: vi.fn(), onBringOver: vi.fn(), onInstall: vi.fn(), onBack: vi.fn() };
  render(<TeamStep panel={PANEL} presets={PRESETS} last={p.last ?? null} installed={p.installed ?? ["claude"]}
    task={p.task ?? "Add rate limiting"} project="/Users/me/shop" machine={null} {...h} />);
  later();
  return h;
}
const option = (name: RegExp | string) => screen.getByRole("button", { name });

test("Same as last time comes first and has focus", () => {
  step({ last: LAST });
  screen.getByRole("heading", { name: "Who should work on it?" });
  screen.getByText("Choosing a team moves to the next step. You can change any agent before you start.");
  const buttons = screen.getAllByRole("button").filter((x) => x.classList.contains("option"));
  expect(buttons[0]).toHaveTextContent("Same as last time");
  expect(buttons[0]).toHaveFocus();
  screen.getByText("planner, 2 builders, reviewer · your last team, checkout-service");
});

test("each option names its agents and count", () => {
  step();
  expect(option(/One agent/)).toHaveTextContent("Works through the task on its own.1 agent");
  expect(option(/Builder and reviewer/)).toHaveTextContent("2 agents");
  expect(option(/Full team/)).toHaveTextContent("A planner splits the work for 3 builders, a reviewer and a tester.6 agents");
  expect(option(/Help me choose/)).toHaveTextContent("Assieme reads your task and suggests a team for you to confirm.Not sure?");
  expect(screen.queryByRole("radio")).toBeNull();
});

test("picking an option calls onPick once", () => {
  const h = step();
  fireEvent.click(option(/Full team/));
  expect(h.onPick).toHaveBeenCalledTimes(1);
  expect(h.onPick).toHaveBeenCalledWith(PRESETS[2]);
});


test("Help me choose reads the task and needs Use this team", async () => {
  b.suggestTeam.mockResolvedValue({ roles: [r("builder"), r("reviewer")], why: "A small change with a check." });
  const h = step();
  fireEvent.click(option(/Help me choose/));
  expect(screen.queryByText("How big is the change?")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Suggest a team" }));
  screen.getByText("Asking Claude Code…");
  await flush();
  expect(b.suggestTeam).toHaveBeenCalledWith({ project: "/Users/me/shop", size: "", review: "every", budget: "lean", task: "Add rate limiting" });
  screen.getByText("A small change with a check.");
  screen.getByText("builder, reviewer");
  expect(h.onSuggested).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Use this team" }));
  expect(h.onSuggested).toHaveBeenCalledWith({ roles: [r("builder"), r("reviewer")], why: "A small change with a check." });
});

test("without a task Help me choose asks the size question", async () => {
  b.suggestTeam.mockResolvedValue({ roles: [r("solo")], why: "x" });
  step({ task: "" });
  fireEvent.click(option(/Help me choose/));
  screen.getByText("How big is the change?");
  fireEvent.click(screen.getByRole("button", { name: "Suggest a team" }));
  await flush();
  expect(b.suggestTeam).toHaveBeenCalledWith({ project: "/Users/me/shop", size: "feature", review: "every", budget: "lean" });
});

test("a suggestion that fails says so, and Back returns to the options", async () => {
  b.suggestTeam.mockRejectedValue("suggest: Couldn't get a suggestion: Please run /login");
  step();
  fireEvent.click(option(/Help me choose/));
  fireEvent.click(screen.getByRole("button", { name: "Suggest a team" }));
  await flush();
  screen.getByText("Couldn't get a suggestion: Please run /login");
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  option(/Full team/);
});

test("a cancelled suggestion never arrives", async () => {
  let answer: (v: unknown) => void = () => {};
  b.suggestTeam.mockReturnValue(new Promise((res) => (answer = res)));
  const h = step();
  fireEvent.click(option(/Help me choose/));
  fireEvent.click(screen.getByRole("button", { name: "Suggest a team" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  answer({ roles: [r("solo")], why: "late" });
  await flush();
  expect(screen.queryByText("late")).toBeNull();
  expect(h.onSuggested).not.toHaveBeenCalled();
  option(/Full team/);
});

test("no agents installed offers to install one", () => {
  const h = step({ installed: [] });
  screen.getByText("Install an agent to continue");
  expect(screen.queryByRole("button", { name: /Full team/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Install Codex" }));
  expect(h.onInstall).toHaveBeenCalledWith("codex");
  screen.getByRole("button", { name: "Install Claude Code" });
});

test("Bring your team over opens the import", () => {
  const h = step();
  screen.getByText("Moving from another tool?");
  fireEvent.click(screen.getByRole("button", { name: "Bring your team over ›" }));
  expect(h.onBringOver).toHaveBeenCalled();
});

test("Back goes back", () => {
  const h = step();
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(h.onBack).toHaveBeenCalled();
});
