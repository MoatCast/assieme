import { useEffect, useState } from "react";
import { type Band, teamBand } from "./bridge";
import Musician from "./Musician";
import { INSTRUMENTS, type Instrument } from "./musicians.gen";

type Seat = { musician: Instrument; palette: 0 | 1 };
type Draft = { kind?: string | null; musician?: string | null };

const BY_KIND: Record<string, Instrument> = { planner: "piano", reviewer: "reviewer", tester: "timpani" };
const STRINGS: Instrument[] = ["violin", "cello", "bass"];
const WINDS: Instrument[] = ["flute", "clarinet", "trumpet"];
const known = (m: unknown): m is Instrument => INSTRUMENTS.includes(m as Instrument);

/** The brain's rule (assieme/musicians.py), for New team's rows before anything is saved. A test keeps the two in step. */
export function assignMusicians(roles: Draft[]): Seat[] {
  let builders = 0;
  let custom = 0;
  return roles.map((r) => {
    let rule: Instrument;
    let palette: 0 | 1 = 0;
    if (r.kind === "builder") {
      rule = STRINGS[builders % 3];
      palette = builders >= 3 ? 1 : 0;
      builders += 1;
    } else if (r.kind && BY_KIND[r.kind]) {
      rule = BY_KIND[r.kind];
    } else {
      rule = WINDS[custom % 3];
      custom += 1;
    }
    return { musician: known(r.musician) ? r.musician : rule, palette };
  });
}

/** An agent no saved team describes (one Assieme found running): its instrument guessed from its name. */
export function guessSeat(role: string): Seat {
  const kind = Object.keys(BY_KIND).find((k) => role.startsWith(k)) ?? (role.startsWith("builder") ? "builder" : null);
  if (kind !== "builder") return assignMusicians([{ kind }])[0];
  const n = Number(/(\d+)$/.exec(role)?.[1] ?? "1");
  return { musician: STRINGS[(Math.max(n, 1) - 1) % 3], palette: n > 3 ? 1 : 0 };
}

const cache = new Map<string, { band: Band; at: number }>();
const FRESH_MS = 15000;
const CHANGED = "assieme-band";
const asking = new Map<string, Promise<Band>>(); // one question per team at a time, however many faces wait for it

/** After an instrument changes: every face of that team (or of every team) asks again. */
export function forgetBand(team?: string) {
  if (team) cache.delete(team);
  else cache.clear();
  asking.clear();
  window.dispatchEvent(new Event(CHANGED));
}

/** Each role's instrument for a team, asked of the brain at most every 15 seconds and shared by every place. */
export function useBand(team: string | null | undefined): Band {
  const [band, setBand] = useState<Band>(() => (team && cache.get(team)?.band) || {});
  const [asked, setAsked] = useState(0);
  useEffect(() => {
    const again = () => setAsked((n) => n + 1);
    window.addEventListener(CHANGED, again);
    return () => window.removeEventListener(CHANGED, again);
  }, []);
  useEffect(() => {
    if (!team) return;
    const hit = cache.get(team);
    if (hit) setBand(hit.band);
    if (hit && Date.now() - hit.at < FRESH_MS) return;
    let live = true;
    let q = asking.get(team);
    if (!q) {
      q = Promise.resolve()
        .then(() => teamBand(team))
        .then((got) => {
          cache.set(team, { band: got ?? {}, at: Date.now() });
          return got ?? {};
        })
        .finally(() => asking.delete(team));
      asking.set(team, q);
    }
    q.then((got) => live && setBand(got)).catch(() => {}); // no band: every face falls back to its name's guess
    return () => {
      live = false;
    };
  }, [team, asked]);
  return band;
}

/** One agent's face wherever it appears: its saved instrument, else the guess from its name. */
export function AgentFace({ team, role, state, size = 1, noDot = false }:
  { team?: string | null; role: string; state: string; size?: 1 | 2 | 3; noDot?: boolean }) {
  const band = useBand(team);
  const seat = band[role] ?? guessSeat(role);
  return <Musician instrument={seat.musician} palette={seat.palette} state={state} role={role} size={size} noDot={noDot} />;
}
