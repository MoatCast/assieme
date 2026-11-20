import { expect, test } from "vitest";
import { failedStart, listModel, type Group, type Row } from "./listModel";
import { MAP, Q, fixRow, pane, starting, waitingFor } from "./windowFixtures";

const AT_1426 = new Date(2026, 10, 20, 14, 26);
const model = (...a: Parameters<typeof listModel>) => listModel(a[0], a[1], a[2], a[3], a[4] ?? AT_1426);

test("an asking agent needs you, with how long it has waited", () => {
  const m = model(MAP({ panes: [pane("planner", { state: "asking", since_s: 720, fix: Q("Flag or main?") })] }), [], [], {});
  expect(m.needs[0]).toMatchObject({ role: "planner", dot: "needs", line: "Asking: flag or main?", tone: "needs", right: "12 min" });
});

test("spoken name", () => {
  const m = model(MAP({ panes: [pane("planner", { state: "asking", since_s: 720, fix: Q("Flag or main?") })] }), [], [], {});
  expect(m.needs[0].spoken).toBe("planner, needs you, asking: flag or main?, 12 minutes");
});

test("a stuck agent with a fix being checked is under Working, in Assieme's words", () => {
  const m = model(MAP({ panes: [pane("builder-3", { state: "stuck" })], done: [fixRow("builder-3", "checking", "14:22")] }), [], [], {});
  expect(m.needs).toEqual([]);
  expect(m.working[0]).toMatchObject({ role: "builder-3", dot: "assieme", line: "Assieme is checking its fix", tone: "assieme", spinner: true, right: "4 min" });
});

test("a stuck agent with no automatic fix pending needs you, with the fix's words", () => {
  const fix = { id: "f", text: "Nudge it to try another way", buttons: [] };
  const m = model(MAP({ panes: [pane("a", { state: "stuck", fix, since_s: 300 })], done: [fixRow("a", "fixed", "13:00")] }), [], [], {});
  expect(m.needs[0]).toMatchObject({ role: "a", line: "Nudge it to try another way", right: "5 min" });
});

test("work to review stays under Done and is counted", () => {
  const m = model(MAP({ panes: [pane("tester", { state: "waiting" })] }), [waitingFor("tester", 3)], [], {});
  expect(m.needs).toEqual([]);
  expect(m.done[0]).toMatchObject({ line: "3 commits to review", tone: "ink", review: 3, dot: "done" });
  expect(m.counts).toContain("1 to review");
});

test("another team's work to review is not counted here", () => {
  const m = model(MAP({ panes: [pane("tester", { state: "waiting" })] }), [waitingFor("tester", 3, "other")], [], {});
  expect(m.counts).not.toContain("to review");
});

test("an idle agent is under Done", () => {
  const m = model(MAP({ panes: [pane("a", { state: "idle", detail: "" })] }), [], [], {});
  expect(m.done[0]).toMatchObject({ role: "a", dot: "done", tone: "mute", review: null });
});

test("verdict variants", () => {
  const ask = (r: string) => pane(r, { state: "asking", fix: Q("Go?") });
  expect(model(MAP({ panes: [ask("a")] }), [], [], {}).verdict).toBe("1 agent needs you");
  expect(model(MAP({ panes: [ask("a"), ask("b")] }), [], [], {}).verdict).toBe("2 agents need you");
  expect(model(MAP({ panes: [pane("a")] }), [], [], {}).verdict).toBe("No agent needs you");
  expect(model(MAP({ stopped: true, panes: [pane("a", { state: "exited" })] }), [], [], {}).verdict).toBe("The team has stopped");
});

test("counts leave out zeros and say fixes in the plural", () => {
  const m = model(MAP({
    panes: [pane("a"), pane("b", { state: "stuck" }), pane("c", { state: "stuck" })],
    done: [fixRow("b", "checking", "14:22"), fixRow("c", "checking", "14:20")], fixes_today: { fixed: 0, checking: 2 },
  }), [], [], {});
  expect(m.counts).toBe("3 working · 2 fixes being checked");
  expect(model(MAP({ panes: [pane("a")] }), [], [], {}).counts).toBe("1 working");
});

test("Assieme's line says what it fixed today, or that it is watching", () => {
  expect(model(MAP({ panes: [pane("a")], fixes_today: { fixed: 2, checking: 0 } }), [], [], {}).assieme).toBe("Assieme fixed 2 problems today ›");
  expect(model(MAP({ panes: [pane("a")], fixes_today: { fixed: 1, checking: 0 } }), [], [], {}).assieme).toBe("Assieme fixed 1 problem today ›");
  expect(model(MAP({ panes: [pane("a")] }), [], [], {}).assieme).toBe("Assieme is watching your team ›");
});

test("a fix that worked in the last minutes shows on the row", () => {
  const m = model(MAP({ panes: [pane("builder-3")] }), [], [], { "builder-3": "14:02" });
  expect(m.working[0]).toMatchObject({ line: "✓ Fixed by Assieme · 14:02", tone: "fixed" });
});

test("an agent being added shows as starting under Working", () => {
  const m = model(MAP({ panes: [pane("a")] }), [], [starting([{ role: "b", state: "starting" }])], {});
  expect(m.working.map((r) => (r as Row).role)).toEqual(["a", "b"]);
  expect(m.working[1]).toMatchObject({ line: "Starting…", tone: "mute" });
});

test("a failed start needs you", () => {
  const s = starting([{ role: "builder-4", state: "needs-you", detail: "Codex sign-in" }], { done: true });
  const m = model(MAP({ panes: [pane("a")] }), [], [s], {});
  expect(m.needs[0]).toMatchObject({ role: "builder-4", line: "Couldn't start: Codex sign-in", tone: "needs" });
});

test("another team's start is not in this list", () => {
  const m = model(MAP({ panes: [pane("a")] }), [], [starting([{ role: "b", state: "starting" }], { team: "other" })], {});
  expect(m.working).toHaveLength(1);
});

test("an agent paused after a fix needs you", () => {
  const fix = { id: "fixpause:shop/a", text: "Still stuck", buttons: [], pause: { at: "14:32", of: "f1" } };
  const m = model(MAP({ panes: [pane("a", { state: "idle", fix, paused_by_fix: true })] }), [], [], {});
  expect(m.needs[0]).toMatchObject({ role: "a", line: "Paused: still stuck after a fix" });
});

test("an agent paused at the daily limit needs you", () => {
  const fix = { id: "b", text: "Paused", buttons: [], budget: "*" };
  const m = model(MAP({ panes: [pane("a", { state: "working", fix })] }), [], [], {});
  expect(m.needs[0]).toMatchObject({ role: "a", line: "Paused at the daily limit" });
});

test("exited agents and empty sections are left out", () => {
  const m = model(MAP({ panes: [pane("a", { state: "exited" }), pane("b")] }), [], [], {});
  expect(m.working.map((r) => (r as Row).role)).toEqual(["b"]);
  expect(m.needs).toEqual([]);
  expect(m.done).toEqual([]);
});

test("groups nest under Working and Done, leading with Assieme's work", () => {
  const groups = [
    { name: "frontend", roles: ["f1", "f2", "f3", "f4"], states: {}, need_you: 0, edges: [] },
    { name: "backend", roles: ["b1", "b2"], states: {}, need_you: 0, edges: [] },
  ];
  const m = model(MAP({
    groups,
    panes: [pane("f1", { state: "stuck" }), pane("f2"), pane("f3"), pane("f4"), pane("b1"), pane("b2", { state: "idle" })],
    done: [fixRow("f1", "checking", "14:22")],
  }), [], [], {});
  const [front, back] = m.working as Group[];
  expect(front).toMatchObject({ name: "frontend", summary: "Assieme fixing 1 · 4 working", fixing: 1, open: true });
  expect(front.rows).toHaveLength(4);
  expect(back).toMatchObject({ name: "backend", summary: "1 working", open: false });
  expect((m.done[0] as Group)).toMatchObject({ name: "backend", summary: "1 done" });
});

test("an unreachable machine keeps every row, with hollow dots", () => {
  const m = model(MAP({ unreachable: { name: "Mac mini" } as never, panes: [pane("a"), pane("b", { state: "asking", fix: Q("Go?") })] }), [], [], {});
  expect(m.stale).toBe(true);
  expect([...m.needs, ...(m.working as Row[])].map((r) => r.dot)).toEqual(["stale", "stale"]);
});

test("a stopped team summarises its run", () => {
  const m = model(MAP({ stopped: true, panes: [], run: { minutes: 192, fixed: 4, tasks_done: 3, stopped_at: "12:40" } }), [], [], {});
  expect(m.counts).toBe("Ran 3 h 12 min · Assieme fixed 4 problems · 3 tasks done");
  expect(m.assieme).toBeNull();
});

test("needs-you rows are ordered by how long they have waited", () => {
  const m = model(MAP({ panes: [pane("a", { state: "asking", since_s: 60, fix: Q("A?") }), pane("b", { state: "asking", since_s: 900, fix: Q("B?") })] }), [], [], {});
  expect(m.needs.map((r) => r.role)).toEqual(["b", "a"]);
});

test("a start that failed part-way leaves the agents that came up alone", () => {
  const s = starting([{ role: "a", state: "ok" }, { role: "b", state: "needs-you" }], { done: true, error: "boom" });
  const panes = [pane("a")];
  const m = model(MAP({ panes }), [], [s], {});
  expect(m.needs.map((r) => r.role)).toEqual(["b"]);
  expect(m.working.map((r) => (r as Row).role)).toEqual(["a"]);
  expect(failedStart([s], "shop", "a", panes)).toBeNull();
  expect(failedStart([s], "shop", "b", panes)).toEqual({ reason: "boom" });
});
