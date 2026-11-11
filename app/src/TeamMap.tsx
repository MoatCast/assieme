import { useLayoutEffect, useRef, useState } from "react";
import dagre from "@dagrejs/dagre";
import type { MapEdge, MapGroup, MapNode, Pane } from "./bridge";
import { ago } from "./Terminals";

const WORDS: Record<Pane["state"], string> = {
  asking: "asking you", stuck: "seems stuck", waiting: "work waiting", working: "working", idle: "idle", exited: "done",
};
const NODE = { width: 150, height: 54 };
const GROUP = { width: 270, height: 74 };
const GROUP_OPEN = { width: 400, height: 170 };
export const needs = (s: Pane["state"]) => s === "asking" || s === "stuck" || s === "waiting";

type Box = { id: string; width: number; height: number };
type Link = { from: string; to: string };
export type Placed = {
  boxes: Record<string, { x: number; y: number; width: number; height: number }>;
  lines: Record<string, { points: { x: number; y: number }[]; labelAt: { x: number; y: number } }>;
  width: number;
  height: number;
};

/** Where everything goes, from dagre. `shape` links ("reports to") place agents before any wake-up and aren't drawn. */
export function layout(boxes: Box[], links: Link[], shape: Link[] = [], rankdir: "TB" | "LR" = "TB", ranksep = 90): Placed {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir, nodesep: 70, ranksep, marginx: 24, marginy: 24 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const b of boxes) g.setNode(b.id, { width: b.width, height: b.height });
  for (const l of [...shape, ...links]) if (l.from !== l.to && g.hasNode(l.from) && g.hasNode(l.to)) g.setEdge(l.from, l.to);
  dagre.layout(g);
  const placed: Placed = { boxes: {}, lines: {}, width: g.graph().width ?? 0, height: g.graph().height ?? 0 };
  for (const b of boxes) {
    const n = g.node(b.id);
    placed.boxes[b.id] = { x: n.x - b.width / 2, y: n.y - b.height / 2, width: b.width, height: b.height };
  }
  for (const l of links) {
    const e = g.hasEdge(l.from, l.to) ? g.edge(l.from, l.to) : null;
    if (e?.points?.length) placed.lines[`${l.from}>${l.to}`] = { points: e.points, labelAt: e.points[Math.floor(e.points.length / 2)] };
  }
  return placed;
}

const path = (pts: { x: number; y: number }[]) => pts.map((p, i) => `${i ? "L" : "M"}${p.x} ${p.y}`).join(" ");

function Arrow({ pts, color, width }: { pts: { x: number; y: number }[]; color: string; width: number }) {
  const [a, b] = pts.slice(-2);
  const t = Math.atan2(b.y - a.y, b.x - a.x);
  const s = 5 + width;
  const wing = (d: number) => `${b.x - s * Math.cos(t + d)},${b.y - s * Math.sin(t + d)}`;
  return (
    <>
      <path d={path(pts)} fill="none" stroke={color} strokeWidth={width} strokeLinecap="round" />
      <polygon points={`${b.x},${b.y} ${wing(0.5)} ${wing(-0.5)}`} fill={color} />
    </>
  );
}

function fitScale(el: HTMLDivElement | null, width: number, height: number) {
  if (!el || !el.clientWidth || !width) return 1;
  return Math.min(1, el.clientWidth / width, el.clientHeight / height);
}

function Canvas({ placed, children, svg }: { placed: Placed; children: React.ReactNode; svg: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  useLayoutEffect(() => {
    const update = () => setScale(fitScale(ref.current, placed.width, placed.height));
    update();
    if (typeof ResizeObserver === "undefined" || !ref.current) return;
    const ro = new ResizeObserver(update);
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, [placed.width, placed.height]);
  return (
    <div className="map-fit" ref={ref}>
      <div className="map-canvas" style={{ width: placed.width, height: placed.height, transform: `scale(${scale})` }}>
        <svg width={placed.width} height={placed.height} aria-hidden="true">{svg}</svg>
        {children}
      </div>
    </div>
  );
}

function Callout({ at, perHour }: { at: { x: number; y: number }; perHour: number }) {
  return (
    <div className="edge-note" style={{ left: at.x - 150, top: at.y - 26 }}>
      <b>{perHour} wake-ups an hour</b>
      <span>most did nothing</span>
    </div>
  );
}

/** The map of one team, or of one opened group: who wakes whom. */
export function AgentsMap({ nodes, edges, waits, onPick }: {
  nodes: MapNode[];
  edges: MapEdge[];
  waits: { from: string; to: string }[];
  onPick: (role: string) => void;
}) {
  const roles = new Set(nodes.map((n) => n.role));
  const shown = edges.filter((e) => roles.has(e.from) && roles.has(e.to));
  const placed = layout(nodes.map((n) => ({ id: n.role, ...NODE })), shown,
    nodes.filter((n) => n.parent && roles.has(n.parent)).map((n) => ({ from: n.parent!, to: n.role })));
  const dashed = waits.filter((w) => placed.boxes[w.from] && placed.boxes[w.to]);
  const curves = dashed.map((w) => {
    const a = placed.boxes[w.from], b = placed.boxes[w.to];
    const from = { x: a.x + a.width, y: a.y + a.height / 2 }, to = { x: b.x + b.width, y: b.y + b.height / 2 };
    const c = { x: Math.max(from.x, to.x) + 70, y: (from.y + to.y) / 2 };
    return { key: `${w.from}>${w.to}`, d: `M${from.x} ${from.y} Q${c.x} ${c.y} ${to.x} ${to.y}`, label: { x: (from.x + to.x + 2 * c.x) / 4, y: c.y }, to: w.to };
  });
  const svg = (
    <>
      {shown.map((e) => {
        const line = placed.lines[`${e.from}>${e.to}`];
        return line ? (
          <g key={`${e.from}>${e.to}`} data-testid={`edge-${e.from}-${e.to}`}>
            <Arrow pts={line.points} color={e.noisy ? "var(--quiet-waste)" : "var(--quiet-edge)"} width={e.noisy ? 2 : 1.5} />
            {!e.noisy && <text x={line.labelAt.x + 8} y={line.labelAt.y + 4} fontSize="12" fill="var(--quiet-mute)" fontFamily="system-ui">{e.wakes}</text>}
          </g>
        ) : null;
      })}
      {curves.map((c) => (
        <path key={c.key} data-testid={`wait-${c.key}`} d={c.d} fill="none" stroke="var(--quiet-needs-you)" strokeWidth="1.2" strokeDasharray="4 3" />
      ))}
    </>
  );
  return (
    <Canvas placed={placed} svg={svg}>
      {nodes.map((n) => {
        const b = placed.boxes[n.role];
        return (
          <button key={n.role} type="button" className={`node ${needs(n.state) ? "needs" : ""} ${n.paused ? "paused" : `state-${n.state}`}`} data-testid={`node-${n.role}`}
            style={{ left: b.x, top: b.y, width: b.width, height: b.height }} onClick={() => onPick(n.role)}>
            <span className="dot" />
            <span className="node-text">
              <span className="node-name">{n.role}</span>
              <span className="node-state">{[n.paused ? "paused" : WORDS[n.state], n.state === "exited" ? "" : ago(n.since_s)].filter(Boolean).join(" · ")}</span>
            </span>
          </button>
        );
      })}
      {shown.filter((e) => e.noisy && placed.lines[`${e.from}>${e.to}`]).map((e) => (
        <Callout key={`${e.from}>${e.to}`} at={placed.lines[`${e.from}>${e.to}`].labelAt} perHour={e.noisy!.per_hour} />
      ))}
      {curves.map((c) => (
        <span key={c.key} className="wait-chip" style={{ left: c.label.x - 60, top: c.label.y - 12 }}>waiting on {c.to}</span>
      ))}
    </Canvas>
  );
}

function summary(g: MapGroup) {
  const states = Object.values(g.states);
  const working = states.filter((s) => s === "working").length;
  if (working === states.length) return "all working";
  const rest = states.length - working - g.need_you;
  return [working && `${working} working`, rest && `${rest} idle or done`].filter(Boolean).join(", ");
}

/** The map of a big team: groups, with who wakes whom between them. */
export function GroupsMap({ groups, onOpen }: { groups: MapGroup[]; onOpen: (name: string) => void }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const size = (g: MapGroup) => (open.has(g.name) ? GROUP_OPEN : GROUP);
  const links = groups.flatMap((g) => g.edges.map((e) => ({ from: g.name, to: e.to })));
  // With no wake-ups yet, nothing places the groups: two columns, or a long row shrinks them past reading.
  const columns = links.length ? [] : groups.slice(2).map((g, i) => ({ from: groups[i].name, to: g.name }));
  const placed = layout(groups.map((g) => ({ id: g.name, ...size(g) })), links, columns, "TB", 56);
  const svg = groups.flatMap((g) => g.edges.map((e) => {
    const line = placed.lines[`${g.name}>${e.to}`];
    return line ? (
      <g key={`${g.name}>${e.to}`} data-testid={`edge-${g.name}-${e.to}`}>
        <Arrow pts={line.points} color={e.noisy ? "var(--quiet-waste)" : "var(--quiet-edge)"} width={e.noisy ? 4 : 1.5} />
        <text x={line.labelAt.x + 8} y={line.labelAt.y + 4} fontSize="12" fill="var(--quiet-mute)" fontFamily="system-ui">{e.wakes}</text>
      </g>
    ) : null;
  }));
  const toggle = (name: string) => setOpen((s) => {
    const n = new Set(s);
    if (n.has(name)) n.delete(name); else n.add(name);
    return n;
  });
  return (
    <Canvas placed={placed} svg={svg}>
      {groups.map((g) => {
        const b = placed.boxes[g.name];
        const isOpen = open.has(g.name);
        const noisy = g.edges.find((e) => e.noisy);
        return (
          <div key={g.name} className={`group-card ${g.need_you ? "needs" : ""}`} data-testid={`group-${g.name}`}
            style={{ left: b.x, top: b.y, width: b.width }}>
            <div className="group-head">
              <b>{g.name}</b>
              <small>{g.roles.length === 1 ? "1 agent" : `${g.roles.length} agents`}</small>
              <span className="grow" />
              {g.need_you > 0 && <span className="group-need">{g.need_you} need{g.need_you === 1 ? "s" : ""} you</span>}
              <button type="button" className="chevron" aria-label={`${isOpen ? "Close" : "Show"} ${g.name}`} aria-expanded={isOpen} onClick={() => toggle(g.name)}>
                <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                  <path d={isOpen ? "M3 7.5l3-3 3 3" : "M3 4.5l3 3 3-3"} fill="none" stroke="var(--quiet-slate)" strokeWidth="1.4" strokeLinecap="round" />
                </svg>
              </button>
            </div>
            {isOpen ? (
              <>
                <div className="group-chips">
                  {g.roles.map((r) => (
                    <span key={r} className={`chip state-${g.states[r]} ${needs(g.states[r]) ? "needs" : ""}`}><span className="dot" />{r}</span>
                  ))}
                </div>
                <button type="button" className="group-open" onClick={() => onOpen(g.name)}>Open the group to see who wakes whom inside it.</button>
              </>
            ) : (
              <div className="group-dots">
                {g.roles.map((r) => <span key={r} className={`dot state-${g.states[r]}`} />)}
                <span className={noisy ? "group-sum noisy" : "group-sum"}>
                  {noisy ? `wakes ${noisy.to} ${noisy.noisy!.per_hour} times an hour` : summary(g)}
                </span>
              </div>
            )}
          </div>
        );
      })}
    </Canvas>
  );
}
