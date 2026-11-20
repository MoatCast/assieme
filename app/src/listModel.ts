// The list's rules (spec "The list", "Which state goes where"): which agent sits under Needs you, Working or Done, in
// what words, and the three-line heading. Pure, so every rule is a unit test; AgentList only draws what this returns.
import type { ActivityRow, Pane, ReviewWaiting, Starting, TeamMap } from "./bridge";

export type Tone = "needs" | "assieme" | "fixed" | "mute" | "ink";
export type Row = {
  role: string;
  dot: "needs" | "working" | "assieme" | "done" | "stale";
  line: string; tone: Tone;
  right: string;            // "12 min" (needs you), "4 min" (fixing), "" otherwise
  spinner: boolean;          // Assieme checking
  review: number | null;     // commits to review
  spoken: string;            // aria-label, e.g. "planner, needs you, asking: flag or main?, 12 minutes"
  waited: number;            // seconds the person has kept it waiting (needs you), for ordering and the first selection
};
export type Group = { name: string; summary: string; fixing: number; open: boolean; rows: Row[] };
export type ListModel = {
  verdict: string; counts: string;
  assieme: string | null;    // "Assieme fixed 2 problems today ›"
  needs: Row[]; working: (Row | Group)[]; done: (Row | Group)[];
  stale: boolean;
};

export const isGroup = (x: Row | Group): x is Group => "rows" in x;

/** A starting agent has settled when its state is none of these (as the Team tab read it). */
const UNSETTLED = ["needs-you", "not-reporting", "exited"];

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);
const minutes = (s: number | null) => (s === null ? "" : s < 60 ? "now" : `${Math.round(s / 60)} min`);

/** Minutes since a "HH:MM" today. */
function since(hhmm: string, now: Date) {
  const [h, m] = hhmm.split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return null;
  const at = new Date(now);
  at.setHours(h, m, 0, 0);
  return Math.max(0, Math.round((now.getTime() - at.getTime()) / 60000));
}

/** "Ran 3 h 12 min" */
export function ran(min: number) {
  const h = Math.floor(min / 60);
  return h ? `Ran ${h} h ${min % 60} min` : `Ran ${min} min`;
}

/** The question in a few words: its first sentence, cut at 40 characters. */
function short(q: string) {
  const first = q.split(/(?<=[?.!])\s/)[0].trim();
  return first.length > 40 ? `${first.slice(0, 39).trimEnd()}…` : first;
}

function needsLine(p: Pane): string | null {
  if (p.fix?.budget) return "Paused at the daily limit";
  if (p.paused_by_fix || p.fix?.pause) return "Paused: still stuck after a fix";
  if (p.state === "asking") return p.fix ? `Asking: ${lowerFirst(short(p.fix.text))}` : "Asking you";
  if (p.state === "stuck") return p.fix?.text || p.detail || "Seems stuck";
  return null;
}

export function listModel(map: TeamMap, waiting: ReviewWaiting[], starting: Starting[], fixedRecently: Record<string, string>,
  now: Date = new Date()): ListModel {
  const stale = !!map.unreachable;
  const checking = new Map<string, ActivityRow>();
  for (const r of map.done) if (r.agent && r.outcome === "checking" && !checking.has(r.agent)) checking.set(r.agent, r);
  const review = new Map(waiting.filter((w) => w.team === map.team).map((w) => [w.role, w.ahead]));
  const row = (role: string, dot: Row["dot"], line: string, tone: Tone, section: string, extra: Partial<Row> = {}): Row => {
    const r: Row = { role, dot: stale ? "stale" : dot, line, tone, right: "", spinner: false, review: null, spoken: "", waited: 0, ...extra };
    const wait = section === "needs you" && r.right && r.right !== "now" ? `, ${r.right.replace("min", "minutes")}` : "";
    r.spoken = `${role}, ${section}, ${lowerFirst(line)}${wait}`;
    return r;
  };

  const needs: Row[] = [], working: Row[] = [], done: Row[] = [];
  const here = starting.filter((s) => s.team === map.team);
  const startingRoles = new Set<string>();
  for (const s of here) {
    for (const r of s.roles) {
      if (!s.done && !s.error) {
        startingRoles.add(r.role);
      } else if (s.error || UNSETTLED.includes(r.state)) {
        const live = map.panes.some((p) => p.role === r.role && p.state !== "exited");
        if (live && !s.error) continue;
        startingRoles.add(r.role);
        needs.push(row(r.role, "needs", `Couldn't start: ${s.error || r.detail || "it didn't come up"}`, "needs", "needs you"));
      }
    }
  }
  for (const p of map.panes) {
    if (p.state === "exited" || startingRoles.has(p.role)) continue;
    const fix = checking.get(p.role);
    if (p.state === "stuck" && fix && !p.paused_by_fix) {
      const m = since(fix.at, now);
      working.push(row(p.role, "assieme", "Assieme is checking its fix", "assieme", "working",
        { spinner: true, right: m === null ? "" : `${m} min` }));
      continue;
    }
    const need = needsLine(p);
    if (need) {
      needs.push(row(p.role, "needs", need, "needs", "needs you", { right: minutes(p.since_s), waited: p.since_s ?? 0 }));
      continue;
    }
    const fixedAt = fixedRecently[p.role];
    const fixed = fixedAt ? { line: `✓ Fixed by Assieme · ${fixedAt}`, tone: "fixed" as Tone } : null;
    if (p.state === "working") {
      working.push(row(p.role, "working", fixed?.line ?? (p.detail || "Working"), fixed?.tone ?? "mute", "working"));
    } else {
      const n = review.get(p.role) ?? null;
      const line = fixed?.line ?? (n ? plural(n, "commit") + " to review" : p.detail || "Finished");
      done.push(row(p.role, "done", line, fixed?.tone ?? (n ? "ink" : "mute"), "done", { review: n || null }));
    }
  }
  for (const s of here) for (const r of s.roles)
    if (!s.done && !s.error && !needs.some((x) => x.role === r.role)) working.push(row(r.role, "working", "Starting…", "mute", "working"));
  needs.sort((a, b) => b.waited - a.waited);

  const toReview = done.filter((r) => r.review).length;
  const spinners = working.filter((r) => r.spinner).length;
  const fixing = map.fixes_today?.checking ?? spinners;
  const fixedToday = map.fixes_today?.fixed ?? 0;
  const n = needs.length;
  const verdict = map.stopped ? "The team has stopped" : n === 0 ? "No agent needs you" : n === 1 ? "1 agent needs you" : `${n} agents need you`;
  const counts = map.stopped
    ? [map.run && ran(map.run.minutes), map.run && `Assieme fixed ${plural(map.run.fixed, "problem")}`,
       map.run && plural(map.run.tasks_done, "task") + " done"].filter(Boolean).join(" · ")
    : [working.length && `${working.length} working`, toReview && `${toReview} to review`,
       fixing && `${fixing} ${fixing === 1 ? "fix" : "fixes"} being checked`].filter(Boolean).join(" · ");
  const assieme = map.stopped ? null
    : fixedToday ? `Assieme fixed ${plural(fixedToday, "problem")} today ›`
    : fixing ? `Assieme is fixing ${plural(fixing, "problem")} ›`
    : "Assieme is watching your team ›";

  return { verdict, counts, assieme, needs, working: grouped(map, working, "working"), done: grouped(map, done, "done"), stale };
}

/** With groups, rows nest by group; a group's summary leads with Assieme's work, and one it is fixing opens. */
function grouped(map: TeamMap, rows: Row[], section: "working" | "done"): (Row | Group)[] {
  if (!map.groups) return rows;
  const out: (Row | Group)[] = [];
  const taken = new Set<string>();
  for (const g of map.groups) {
    const mine = rows.filter((r) => g.roles.includes(r.role));
    if (!mine.length) continue;
    mine.forEach((r) => taken.add(r.role));
    const fixing = mine.filter((r) => r.spinner).length;
    const review = mine.filter((r) => r.review).length;
    const rest = section === "working" ? `${mine.length} working`
      : [review && `${review} to review`, mine.length - review && `${mine.length - review} done`].filter(Boolean).join(" · ");
    out.push({ name: g.name, summary: [fixing && `Assieme fixing ${fixing}`, rest].filter(Boolean).join(" · "), fixing, open: fixing > 0, rows: mine });
  }
  return [...out, ...rows.filter((r) => !taken.has(r.role))];
}
