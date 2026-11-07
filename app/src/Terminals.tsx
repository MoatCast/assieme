import { useEffect, useRef, useState } from "react";
import { paneAct, teamView, type Pane, type PaneButton, type TeamView } from "./bridge";
import Terminal from "./Terminal";
import "./Terminals.css";

const STATE: Record<Pane["state"], string> = {
  asking: "asking you", stuck: "seems stuck", waiting: "work waiting", working: "working", idle: "idle", exited: "exited",
};
const CLI = { claude: "Claude Code", codex: "Codex" };

export function ago(s: number | null): string {
  if (s === null) return "";
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
}

const needsYou = (p: Pane) => p.state === "asking" || p.state === "stuck";

/** Terminals: every agent of one Assieme team, live, with what needs you on its pane. */
export default function Terminals({ team, pollMs = 3000, onNewTeam }: {
  team: string | null;
  pollMs?: number;
  onNewTeam: () => void;
}) {
  const [view, setView] = useState<TeamView | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [pressed, setPressed] = useState<Set<string>>(new Set()); // fix ids already acted on: no second send
  const [offline, setOffline] = useState(false);
  const refresh = useRef<() => void>(() => {});

  useEffect(() => {
    if (!team) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let busy = false;
    const poll = async () => {
      if (busy) return;
      busy = true;
      clearTimeout(timer);
      try {
        const v = await teamView(team);
        if (!stopped) {
          setView(v);
          setOffline(false);
        }
      } catch {
        if (!stopped) setOffline(true);
      }
      busy = false;
      if (!stopped) timer = setTimeout(poll, pollMs);
    };
    refresh.current = () => void poll();
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [team, pollMs]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setFocus(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!team)
    return (
      <div className="terminals empty">
        <p>No team is running. Start one from New team.</p>
        <button type="button" className="primary" onClick={onNewTeam}>New team</button>
      </div>
    );
  if (!view) return <div className="terminals">{offline && <div className="offline">Reconnecting…</div>}</div>;

  const press = async (p: Pane, b: PaneButton) => {
    if (b.kind === "reply") return setFocus(p.role);
    if (!p.fix || pressed.has(p.fix.id)) return;
    const fix = p.fix.id;
    setPending(b.id);
    try {
      const r = await paneAct({ team, role: p.role, fix, button: b.id });
      setNotes((n) => ({ ...n, [p.role]: r.ok ? "" : r.detail }));
      if (r.ok) setPressed((s) => new Set(s).add(fix));
      refresh.current();
    } catch (e) {
      setNotes((n) => ({ ...n, [p.role]: String(e) }));
    } finally {
      setPending(null);
    }
  };

  const card = (p: Pane, full: boolean) => (
    <div key={p.role} data-testid={`pane-${p.role}`}
      className={`pane state-${p.state}${needsYou(p) ? " needs" : ""}${full ? " full" : ""}`}>
      <div className="pane-head" onClick={() => !full && setFocus(p.role)}>
        <span className="dot" />
        <button type="button" className="pane-name">{p.role}</button>
        <span className="pane-cli">{CLI[p.agent]}</span>
        <span className="pane-state">{[STATE[p.state], p.state === "exited" ? "" : ago(p.since_s)].filter(Boolean).join(" · ")}</span>
      </div>
      <div className="pane-screen">
        <Terminal session="assieme" address={p.address} typing={full && p.live} />
      </div>
      {(p.fix || notes[p.role] || (p.state === "exited" && p.detail)) && (
        <div className="fix">
          <span className="fix-reason">{notes[p.role] || p.detail}</span>
          {p.fix?.buttons.map((b) => (
            <button key={b.id} type="button" disabled={pending === b.id || (b.kind !== "reply" && pressed.has(p.fix!.id))}
              className={b.kind === "reply" ? "quiet" : b === p.fix!.buttons[0] ? "primary" : "secondary"}
              onClick={() => press(p, b)}>{b.label}</button>
          ))}
        </div>
      )}
    </div>
  );

  const focused = view.panes.find((p) => p.role === focus);
  return (
    <div className="terminals">
      {offline && <div className="offline">Reconnecting…</div>}
      {view.strip.length > 0 && (
        <div className="strip">
          <span className="strip-count"><span className="dot" />{view.strip.length} need you</span>
          <span className="strip-lines">{view.strip.map((s) => <span key={s.role}>{s.text}</span>)}</span>
          <button type="button" className="link" onClick={() => setFocus(view.strip[0].role)}>Review</button>
        </div>
      )}
      {focused ? (
        <div className="focus">
          <button type="button" className="back" onClick={() => setFocus(null)}>‹ All agents</button>
          {card(focused, true)}
        </div>
      ) : (
        <div className="grid">{view.panes.map((p) => card(p, false))}</div>
      )}
    </div>
  );
}
