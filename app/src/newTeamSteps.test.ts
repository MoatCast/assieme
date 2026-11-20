import { expect, test } from "vitest";
import type { Practices, RoleDraft } from "./bridge";
import { folderMovesOn, indicator, nextStep, presetNote, prevStep, rulesLine, stepsShown, taskAnswer, teamSummary, whileTheyWork } from "./newTeamSteps";

const role = (name: string) => ({ name, agent: "claude", job: "", checkout: "branch", parent: null, brief: "" }) as RoleDraft;

test("stepsShown keeps the optional steps only when needed", () => {
  expect(stepsShown({ tools: false, agent: false, remote: false })).toEqual(["project", "task", "team", "start"]);
  expect(stepsShown({ tools: true, agent: true, remote: false })).toEqual(["tools", "agent", "project", "task", "team", "start"]);
  expect(stepsShown({ tools: false, agent: false, remote: true })[0]).toBe("where");
});

test("nextStep and prevStep", () => {
  const s = stepsShown({ tools: false, agent: false, remote: false });
  expect(nextStep(s, "task")).toBe("team");
  expect(nextStep(s, "start")).toBe("start");
  expect(prevStep(s, "project")).toBeNull();
  expect(prevStep(s, "team")).toBe("task");
});

test("indicator marks done, current and later steps", () => {
  const s = stepsShown({ tools: false, agent: false, remote: false });
  expect(indicator(s, "team", { project: "checkout-service", task: "Rate limiting on login" })).toEqual([
    { id: "project", text: "checkout-service", state: "done" },
    { id: "task", text: "Rate limiting on login", state: "done" },
    { id: "team", text: "Team", state: "current" },
    { id: "start", text: "Start", state: "later" },
  ]);
  expect(indicator(s, "task", {})[0]).toEqual({ id: "project", text: "Project", state: "done" });
});

test("folderMovesOn only for a clean, passing folder", () => {
  expect(folderMovesOn({ ok: true })).toBe(true);
  for (const c of [null, { ok: true, uncommitted: 3 }, { ok: false, setup: true }, { ok: false, tools: true }, { ok: false, too_large: true }])
    expect(folderMovesOn(c)).toBe(false);
});

test("teamSummary counts each kind", () => {
  expect(teamSummary(["planner", "builder-1", "builder-2", "builder-3", "reviewer", "tester"].map(role))).toBe("planner, 3 builders, reviewer, tester");
  expect(teamSummary(["builder", "reviewer"].map(role))).toBe("builder, reviewer");
});

test("presetNote says what each team does", () => {
  expect(presetNote({ id: "one-agent", label: "", roles: [] }, false)).toBe("Works through the task on its own.");
  expect(presetNote({ id: "builder-reviewer", label: "", roles: [] }, false)).toBe("One builds; a second checks every change before you see it.");
  expect(presetNote({ id: "full", label: "", roles: [] }, false)).toBe("A planner splits the work for 3 builders, a reviewer and a tester.");
  expect(presetNote({ id: "checkout-service", label: "", roles: ["planner", "builder-1", "builder-2", "reviewer"].map(role) }, true))
    .toBe("planner, 2 builders, reviewer · your last team, checkout-service");
});

test("taskAnswer", () => {
  expect(taskAnswer("   ")).toBe("No task");
  expect(taskAnswer("a".repeat(40))).toBe("a".repeat(28) + "…");
  expect(taskAnswer("Short")).toBe("Short");
});

const group = (id: string, title: string, n: number) =>
  ({ id, title, summary: "", detail: "", rules: Array.from({ length: n }, (_, i) => ({ id: `${id}-${i}`, title: "", detail: "", text: "" })) });
const PR: Practices = {
  groups: [group("messages", "Short messages", 3), group("safety", "Safe commands", 3), group("reviews", "Careful reviews", 3),
    group("stopping", "Clear stops", 3), group("machine", "Light load", 2)],
  basics: [], bigger: [],
};

test("rulesLine counts the rules on and names the groups", () => {
  expect(rulesLine(PR, {})).toBe("Each agent gets 14 team rules: short messages, safe commands, careful reviews, clear stops and a light load on your Mac.");
  expect(rulesLine(PR, { reviews: false })).toBe("Each agent gets 11 team rules (Careful reviews off): short messages, safe commands, clear stops and a light load on your Mac.");
  expect(rulesLine(PR, { "messages-0": false })).toMatch(/^Each agent gets 13 team rules:/);
});

test("whileTheyWork follows Settings › Fixes", () => {
  expect(whileTheyWork({ nudge: "auto", compact: "auto", tell: "auto" })).toBe(
    "When an agent loops, repeats a refused command or runs low on memory, Assieme fixes it on its own and lists the result. If a fix doesn’t work, it pauses the agent and asks you.");
  const ask = "When an agent loops, repeats a refused command or runs low on memory, Assieme tells you and offers a fix. Change this in Settings › Fixes.";
  expect(whileTheyWork({ nudge: "auto", compact: "ask", tell: "auto" })).toBe(ask);
  expect(whileTheyWork(null)).toBe(ask);
});
