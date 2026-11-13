import { useEffect, useState } from "react";
import { AgentFace, guessSeat, useBand } from "./band";
import type { MapGroup, MapNode } from "./bridge";
import { Frame } from "./Musician";
import { useMusicianMode } from "./musicianMode";
import { CONDUCTOR, type Instrument } from "./musicians.gen";
import "./Stage.css";

/** Step 31: the stage strip is hidden until the person shows it (Paper "Team, stage hidden (default)", "… stage shown"). */
export const STAGE_KEY = "assieme.stage";
const CHANGED = "assieme-stage";

type Seat = "front" | "middle" | "back";
/** Strings and piano in front, woodwinds and the reviewer in the middle, brass and timpani at the back. */
export const SEATS: Record<Instrument, Seat> = { piano: "front", violin: "front", cello: "front", bass: "front",
  reviewer: "middle", flute: "middle", clarinet: "middle", trumpet: "back", timpani: "back" };

const W = 380;
const H = 118; // the same for 3 agents or 30: the stage never pushes the map further down
const FACE = 48; // 3×
const ROW_Y: Record<Seat, number> = { back: 0, middle: 22, front: 58 };
const GAP = { left: 150, right: 228 }; // the conductor stands between, at the front

function savedShown() {
  try {
    return localStorage.getItem(STAGE_KEY) === "shown";
  } catch {
    return false;
  }
}

/** Whether the stage is shown: never with Musicians off. Follows this window and the others at once. */
export function useStageShown() {
  const mode = useMusicianMode();
  const [shown, setShown] = useState(savedShown);
  useEffect(() => {
    const follow = () => setShown(savedShown());
    const fromOther = (e: StorageEvent) => (e.key === STAGE_KEY || e.key === null) && follow();
    window.addEventListener(CHANGED, follow);
    window.addEventListener("storage", fromOther);
    return () => {
      window.removeEventListener(CHANGED, follow);
      window.removeEventListener("storage", fromOther);
    };
  }, []);
  return shown && mode !== "off";
}

function setShown(shown: boolean) {
  try {
    if (shown) localStorage.setItem(STAGE_KEY, "shown");
    else localStorage.removeItem(STAGE_KEY);
  } catch {
    // storage refused: the stage can't be kept shown
  }
  window.dispatchEvent(new Event(CHANGED));
}

/** The small round button after the Team heading. */
export function StageToggle() {
  const mode = useMusicianMode();
  const shown = useStageShown();
  if (mode === "off") return null;
  return (
    <button type="button" className={`stage-toggle${shown ? " on" : ""}`} aria-pressed={shown}
      aria-label={shown ? "Hide the stage" : "Show the stage"} title={shown ? "Hide the stage" : "Show the stage"}
      onClick={() => setShown(!shown)}>
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path d="M6 12V3.6L13 2v8.4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="4.3" cy="12" r="1.8" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="11.3" cy="10.4" r="1.8" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    </button>
  );
}

type Player = { role: string; state: string; seat: Seat; count?: number };

const needs = (s: string) => s === "asking" || s === "stuck";

/** Evenly across [from, to], each FACE wide. */
function spread(n: number, from: number, to: number) {
  if (!n) return [];
  const step = (to - from - FACE) / Math.max(n - 1, 1);
  return Array.from({ length: n }, (_, i) => Math.round(n === 1 ? (from + to - FACE) / 2 : from + i * Math.min(step, FACE + 24)));
}

function place(players: Player[]) {
  const at: { p: Player; x: number; y: number }[] = [];
  for (const seat of ["back", "middle", "front"] as Seat[]) {
    const row = players.filter((p) => p.seat === seat);
    if (seat === "front") {
      const left = row.slice(0, Math.ceil(row.length / 2));
      const right = row.slice(left.length);
      spread(left.length, 0, GAP.left).forEach((x, i) => at.push({ p: left[i], x, y: ROW_Y.front }));
      spread(right.length, GAP.right, W).forEach((x, i) => at.push({ p: right[i], x, y: ROW_Y.front }));
    } else {
      const width = row.length * FACE + (row.length - 1) * 16;
      row.forEach((p, i) => at.push({ p, x: Math.round((W - width) / 2) + i * (FACE + 16), y: ROW_Y[seat] }));
    }
  }
  return at;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** Step 31: the band on stage, you conducting from behind. Over twelve agents, one musician per group with its count.
 *  One image with one label: the musicians inside are hidden from screen readers. */
export default function Stage({ team, nodes, groups }: { team?: string; nodes: MapNode[]; groups: MapGroup[] }) {
  const band = useBand(team);
  const seatOf = (role: string) => SEATS[(band[role] ?? guessSeat(role)).musician];
  const byGroup = nodes.length > 12 && groups.length > 0;
  const players: Player[] = byGroup
    ? groups.map((g) => {
      const states = g.roles.map((r) => g.states[r]);
      const state = states.some(needs) ? "asking" : states.includes("working") ? "working" : "idle";
      return { role: g.roles[0], state, seat: "front" as Seat, count: g.roles.length };
    })
    : nodes.slice(0, 12).map((n) => ({ role: n.role, state: n.paused ? "idle" : n.state, seat: seatOf(n.role) }));
  const waiting = nodes.filter((n) => needs(n.state)).length;
  const label = [`The band: ${plural(nodes.length, "agent", "agents")}${byGroup ? ` in ${groups.length} groups` : ""}`,
    waiting ? `${waiting} ${waiting === 1 ? "needs" : "need"} you` : ""].filter(Boolean).join(", ");
  const at = place(players);
  const risers = (["back", "middle"] as Seat[]).map((seat) => {
    const xs = at.filter((a) => a.p.seat === seat).map((a) => a.x);
    return xs.length ? { seat, x: Math.min(...xs) - 4, w: Math.max(...xs) - Math.min(...xs) + FACE + 8 } : null;
  });
  return (
    <div className="stage" role="img" aria-label={label} style={{ width: W, height: H }}>
      <div aria-hidden="true">
        <i className="stage-floor" />
        {risers.map((r) => r && <i key={r.seat} className="stage-riser" style={{ left: r.x, top: ROW_Y[r.seat] + 54, width: r.w }} />)}
        {(["back", "middle", "front"] as Seat[]).map((seat) => (
          <div key={seat} data-seat={seat}>
            {at.filter((a) => a.p.seat === seat).map(({ p, x, y }) => (
              <div key={p.role} className="stage-player" style={{ left: x, top: y }}>
                <AgentFace team={team} role={p.role} state={p.state} size={3} />
                {p.count !== undefined && <span className={`stage-count${needs(p.state) ? " needs" : ""}`}>{`×${p.count}`}</span>}
              </div>
            ))}
          </div>
        ))}
        <span className="musician stage-you" style={{ left: (GAP.left + GAP.right) / 2 - 27, top: 61 }}>
          <svg width={54} height={51} viewBox="0 0 18 17" shapeRendering="crispEdges">
            <Frame grid={CONDUCTOR} colour={(k) => `var(--quiet-musician-${k.toLowerCase()})`} />
          </svg>
        </span>
      </div>
    </div>
  );
}
