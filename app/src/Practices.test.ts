import { expect, test } from "vitest";
import { agentCount } from "./Practices";

const role = (name: string, job = "") => ({ name, agent: "claude" as const, job, checkout: "branch" as const, parent: null, brief: "b" });

test("research pairs count a critic per researcher or investigator by name, never by a job's wording", () => {
  const roles = [role("lead"), role("researcher-1"), role("investigator"), role("builder-1", "Investigate and fix the flaky test")];
  expect(agentCount(roles, {})).toBe(4);
  expect(agentCount(roles, { "research-pairs": true })).toBe(6);
  expect(agentCount(roles, { "research-pairs": true, steward: true })).toBe(7);
});
