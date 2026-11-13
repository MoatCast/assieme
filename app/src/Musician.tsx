import { useEffect, useState } from "react";
import { HEIGHT, LETTERS, SPRITES, WIDTH, type Instrument } from "./musicians.gen";
import { useMusicianMode } from "./musicianMode";
import "./Musician.css";

export type Pose = "play" | "rest" | "hand" | "grey";

/** Working plays; asking or stuck raises a hand; stopped is grey; any other state, known or not yet, rests. */
export function poseFor(state: string): Pose {
  if (state === "working") return "play";
  if (state === "asking" || state === "stuck") return "hand";
  if (state === "exited") return "grey";
  return "rest";
}

const NAMES: Record<Instrument, string> = { piano: "piano", reviewer: "score", violin: "violin", cello: "cello", bass: "bass",
  timpani: "timpani", flute: "flute", clarinet: "clarinet", trumpet: "trumpet" };
const WORDS: Record<string, string> = { working: "working", asking: "needs you", stuck: "needs you", idle: "idle",
  waiting: "has work waiting", exited: "stopped" };

/** "builder-2, violin, needs you": what a screen reader hears. The state words stay on screen beside the picture too. */
export const musicianLabel = (role: string, instrument: Instrument, state: string) =>
  `${role}, ${NAMES[instrument] ?? instrument}, ${WORDS[state] ?? state}`;

function useReducedMotion() {
  const query = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  const [reduced, setReduced] = useState(!!query?.matches);
  useEffect(() => {
    if (!query?.addEventListener) return;
    const on = (e: MediaQueryListEvent) => setReduced(e.matches);
    query.addEventListener("change", on);
    return () => query.removeEventListener("change", on);
  }, [query]);
  return reduced;
}

/** One rect per run of the same letter in a row, in that letter's colour token. */
export function Frame({ grid, colour, className }: { grid: string[]; colour: (k: string) => string; className?: string }) {
  const rects = [];
  for (let y = 0; y < grid.length; y++) {
    const row = grid[y];
    for (let x = 0; x < row.length;) {
      const k = row[x];
      let end = x + 1;
      while (end < row.length && row[end] === k) end++;
      if (k !== ".") rects.push(<rect key={`${x}.${y}`} x={x} y={y} width={end - x} height={1} fill={colour(k)} />);
      x = end;
    }
  }
  return <g className={className}>{rects}</g>;
}

type Props = { instrument: Instrument; state: string; role: string; size?: 1 | 2 | 3; palette?: 0 | 1 };

/** Step 31: an agent as its musician (Paper "Musicians (step 31)"). Drawn on whole pixels at 1×, 2× or 3×, never smoothed. */
export default function Musician({ instrument, state, role, size = 2, palette = 0 }: Props) {
  const reduced = useReducedMotion();
  const mode = useMusicianMode();
  const label = musicianLabel(role, instrument, state);
  if (mode === "off") return <span className="dot" role="img" aria-label={label} />; // the dot each place drew before
  const sprite = SPRITES[instrument] ?? SPRITES.violin;
  const pose = poseFor(state);
  const swap: Record<string, string> = palette === 1 ? { M: "C", C: "M" } : {};
  const colour = pose === "grey" ? () => "var(--quiet-musician-grey)" : (k: string) => `var(${LETTERS[swap[k] ?? k]})`;
  const playing = pose === "play" && !reduced && mode === "animated"; // Still, or Reduce Motion: first frame
  return (
    <span className={`musician${playing ? " playing" : ""}`} role="img" aria-label={label}>
      <svg width={WIDTH * size} height={HEIGHT * size} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} shapeRendering="crispEdges"
        aria-hidden="true">
        {pose === "play" ? (
          <>
            <Frame grid={sprite.play[0]} colour={colour} className="f1" />
            {playing && <Frame grid={sprite.play[1]} colour={colour} className="f2" />}
          </>
        ) : <Frame grid={pose === "hand" ? sprite.hand : sprite.rest} colour={colour} />}
      </svg>
    </span>
  );
}
