import { useEffect, useRef, useState } from "react";
import { openLink, paneAct, reviewWaiting, teamView, type ActivityRow, type Pane, type PaneButton, type ReviewWaiting, type TeamView } from "./bridge";
import { didText } from "./FixRow";
import { kindName } from "./kinds";
import { host, usePreviews } from "./Preview";
import Offline from "./Offline";
import Terminal from "./Terminal";
import "./Terminals.css";
import { AgentFace } from "./band";

const STATE: Record<Pane["state"], string> = {
  asking: "asking you", stuck: "seems stuck", waiting: "work waiting", working: "working", idle: "done", exited: "exited",
};

export function ago(s: number | null): string {
  if (s === null) return "";
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
}

const commits = (n: number) => `${n} commit${n === 1 ? "" : "s"}`;
const needsYou = (p: Pane) => p.state === "asking" || p.state === "stuck";

/** All terminals: every agent of one Assieme team, live, with what needs you on its tile. A tile selects its agent. */
export default function Terminals({ team, pollMs = 3000, onNewTeam, onReview, onSelect, checking = {} }: {
  team: string | null;
  pollMs?: number;
  onNewTeam: () => void;
  onReview: (team: string, role: string) => void;
  /** Selects the agent in the list: its panel takes typing. */
  onSelect: (role: string) => void;
  /** The fix Assieme is checking, by agent. */
  checking?: Record<string, ActivityRow>;
}) {
  const [waiting, setWaiting] = useState<ReviewWaiting[]>([]);
  const [view, setView] = useState<TeamView | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [pressed, setPressed] = useState<Set<string>>(new Set()); // fix ids already acted on: no second send
  const [offline, setOffline] = useState(false);
  const refresh = useRef<() => void>(() => {});
  const pv = usePreviews(pollMs, onReview);

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
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const w = await reviewWaiting();
        if (!stopped) setWaiting(w);
      } catch {
        // the view's own poll shows "Reconnecting…"
      }
      if (!stopped) timer = setTimeout(poll, pollMs);
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [pollMs]);

  const waitingList = waiting.length > 0 && (
    <div className="waiting">
      <h2>Waiting for review</h2>
      {waiting.map((w) => (
        <div key={`${w.team}/${w.role}`} className="waiting-row">
          <span className="waiting-role">{w.role}</span>
          <span className="waiting-where">{w.project} · {commits(w.ahead)}</span>
          <button type="button" className="secondary" onClick={() => onReview(w.team, w.role)}>Review changes</button>
        </div>
      ))}
    </div>
  );
  if (!team)
    return (
      <div className="terminals empty">
        <p>No team is running. Start one from New team.</p>
        <button type="button" className="primary" onClick={onNewTeam}>New team</button>
        {waitingList}
      </div>
    );
  if (!view) return <div className="terminals">{offline && <Offline team />}</div>;
  if (!view.panes.length)
    return (
      <div className="terminals empty">
        <p>These agents aren’t in terminals Assieme can reach.</p>
        {waitingList}
      </div>
    );

  const press = async (p: Pane, b: PaneButton) => {
    if (b.kind === "reply") return onSelect(p.role);
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

  /** Finished work: the agent isn't busy, and nothing more pressing is on its pane. */
  const ready = (p: Pane) =>
    !p.fix && !notes[p.role] && p.state !== "working" && !needsYou(p)
      ? waiting.find((w) => w.team === team && w.role === p.role) : undefined;

  /** Paper "v2 — Preview: states": finished work, and its preview, at the foot of a pane. */
  const finished = (p: Pane, w: ReviewWaiting | undefined, pr: ReturnType<typeof pv.of>) => (
    <div className="fix preview-foot">
      <span className={`fix-reason${pr?.state === "failed" ? " bad" : ""}`}>
        {!pr ? `${commits(w!.ahead)} ready to review`
          : pr.state === "running" ? <span className="mono">{host(pr)}</span>
          : pr.state === "failed" ? "The preview didn't start" : "Starting the preview…"}
      </span>
      {!pr && <button type="button" className="secondary" disabled={pv.busy} onClick={() => pv.start(team, p.role)}>Preview</button>}
      {pr && pr.state !== "failed" && <button type="button" className="quiet" disabled={pv.busy} onClick={() => pv.stop(team, p.role)}>Stop</button>}
      {pr?.state === "running" && <button type="button" className="secondary" onClick={() => pr.url && openLink(pr.url)}>Open preview</button>}
      {pr?.state === "failed" && <button type="button" className="secondary" onClick={() => onReview(team, p.role)}>See why</button>}
      {w && <button type="button" className="secondary" onClick={() => onReview(team, p.role)}>Review changes</button>}
    </div>
  );

  const card = (p: Pane) => {
    const fixing = p.state === "stuck" ? checking[p.role] : undefined;
    const needs = needsYou(p) && !fixing;
    return (
    <div key={p.role} data-testid={`pane-${p.role}`}
      className={`pane state-${p.state}${needs ? " needs" : ""}${fixing ? " assieme" : ""}`}>
      <div className="pane-head" onClick={() => onSelect(p.role)}>
        <AgentFace team={team} role={p.role} state={p.state} />
        <button type="button" className="pane-name">{p.role}</button>
        <span className="pane-cli">{kindName(p.agent)}</span>
        <span className="pane-state">{fixing ? "Assieme on it" : [STATE[p.state], p.state === "exited" ? "" : ago(p.since_s)].filter(Boolean).join(" · ")}</span>
      </div>
      <div className="pane-screen">
        <Terminal session={p.session ?? "assieme"} address={p.address} typing={false} />
      </div>
      {fixing && (
        <div className="fix assieme-foot">
          <span className="fix-reason">Assieme {didText(fixing)} at {fixing.at} · <b>Checking</b></span>
        </div>
      )}
      {!fixing && (p.fix || notes[p.role] || (p.state === "exited" && p.detail)) && (
        <div className="fix">
          <span className="fix-reason">{notes[p.role] || p.detail}</span>
          {p.fix?.buttons.map((b) => (
            <button key={b.id} type="button" disabled={pending === b.id || (b.kind !== "reply" && pressed.has(p.fix!.id))}
              className={b.kind === "reply" ? "btn-ghost" : "btn-outline"} aria-label={b.kind === "reply" ? "Write a reply" : undefined}
              onClick={() => press(p, b)}>{b.kind === "reply" ? "✎" : b.label}</button>
          ))}
        </div>
      )}
      {(ready(p) || pv.of(team, p.role)) && finished(p, ready(p), pv.of(team, p.role))}
    </div>
    );
  };

  return (
    <div className="terminals">
      {offline && <Offline team />}
      <div className="grid">{view.panes.map((p) => card(p))}</div>
      {pv.sheet}
    </div>
  );
}
