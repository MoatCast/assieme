import type { Practices, Preset, ProjectCheck, RoleDraft, Settings } from "./bridge";

/** New team, one question per step: which steps show, the indicator, and the copy that depends on data. */
export type StepId = "tools" | "agent" | "where" | "project" | "task" | "team" | "start";
const ORDER: StepId[] = ["tools", "agent", "where", "project", "task", "team", "start"];
export const STEP_LABEL: Record<StepId, string> = {
  tools: "Developer tools", agent: "Agent", where: "Where", project: "Project", task: "Task", team: "Team", start: "Start",
};

export type Needs = { tools: boolean; agent: boolean; remote: boolean };
export const stepsShown = (n: Needs): StepId[] =>
  ORDER.filter((s) => (s === "tools" ? n.tools : s === "agent" ? n.agent : s === "where" ? n.remote : true));

export const nextStep = (steps: StepId[], at: StepId): StepId => steps[Math.min(steps.indexOf(at) + 1, steps.length - 1)];
export const prevStep = (steps: StepId[], at: StepId): StepId | null => steps[steps.indexOf(at) - 1] ?? null;

export type IndicatorItem = { id: StepId; text: string; state: "done" | "current" | "later" };
export function indicator(steps: StepId[], at: StepId, answers: Partial<Record<StepId, string>>): IndicatorItem[] {
  const here = steps.indexOf(at);
  return steps.map((id, i) => i < here
    ? { id, text: answers[id] || STEP_LABEL[id], state: "done" }
    : { id, text: STEP_LABEL[id], state: i === here ? "current" : "later" });
}

/** Project moves on by itself only for a folder that needs no setup, no install and has no warning. */
export const folderMovesOn = (c: ProjectCheck | null) =>
  !!c && c.ok && !c.setup && !c.tools && !c.too_large && !c.uncommitted;

/** "planner, 3 builders, reviewer, tester": each name without its number, counted in first-seen order. */
export function teamSummary(roles: RoleDraft[]): string {
  const counts = new Map<string, number>();
  for (const r of roles) {
    const base = r.name.replace(/-\d+$/, "");
    counts.set(base, (counts.get(base) ?? 0) + 1);
  }
  return [...counts].map(([b, n]) => (n > 1 ? `${n} ${b}s` : b)).join(", ");
}

const NOTES: Record<string, string> = {
  "one-agent": "Works through the task on its own.",
  "builder-reviewer": "One builds; a second checks every change before you see it.",
  full: "A planner splits the work for 3 builders, a reviewer and a tester.",
};
export const presetNote = (p: Preset, last: boolean) =>
  last ? `${teamSummary(p.roles)} · your last team, ${p.id}` : NOTES[p.id] ?? teamSummary(p.roles);

export function taskAnswer(task: string) {
  const t = task.trim();
  return !t ? "No task" : t.length > 28 ? `${t.slice(0, 28)}…` : t;
}

const PHRASE: Record<string, string> = {
  messages: "short messages", safety: "safe commands", reviews: "careful reviews", stopping: "clear stops",
  machine: "a light load on your Mac",
};
const and = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

/** Start's "Before they start" line: the rules each agent gets, and any group turned off. */
export function rulesLine(practices: Practices, value: Record<string, boolean>) {
  const on = practices.groups.filter((g) => value[g.id] !== false);
  const off = practices.groups.filter((g) => value[g.id] === false);
  const n = on.reduce((sum, g) => sum + g.rules.filter((r) => value[r.id] !== false).length, 0);
  const offNote = off.length ? ` (${and(off.map((g) => g.title))} off)` : "";
  return `Each agent gets ${n} team rules${offNote}: ${and(on.map((g) => PHRASE[g.id] ?? g.title.toLowerCase()))}.`;
}

/** Start's "While they work" line: it follows Settings › Fixes. */
export function whileTheyWork(fixes: Pick<Settings["fixes"], "nudge" | "compact" | "tell"> | null) {
  const start = "When an agent loops, repeats a refused command or runs low on memory, Assieme";
  return fixes && fixes.nudge === "auto" && fixes.compact === "auto" && fixes.tell === "auto"
    ? `${start} fixes it on its own and lists the result. If a fix doesn’t work, it pauses the agent and asks you.`
    : `${start} tells you and offers a fix. Change this in Settings › Fixes.`;
}
