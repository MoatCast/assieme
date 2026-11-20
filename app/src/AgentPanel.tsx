// The panel for one agent (Paper "Main window": agent selected, Assieme fixing, paused, failed start, finished): its
// card, its live terminal, what it did, what Assieme did, and its memory and who wakes it.
import { useEffect, useRef, useState } from "react";
import { agentDetail, agentRemove, agentRestart, openLink, type ActivityRow, type AgentDetail as Detail, type Pane,
  type ReviewWaiting } from "./bridge";
import AgentDetail, { MemorySection, Recent, WakersSection } from "./AgentDetail";
import FixButtons from "./FixButtons";
import FixRow, { AssiemeMark, didText } from "./FixRow";
import { kindName } from "./kinds";
import { tokens } from "./listModel";
import { host, usePreviews } from "./Preview";
import Terminal from "./Terminal";
import "./AgentPanel.css";

const hhmm = (d: Date) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
const mins = (s: number | null) => (s === null || s < 60 ? "" : `${Math.round(s / 60)} min`);
const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);
const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const SAYS: Partial<Record<Pane["state"], string>> = { asking: "Asking you", stuck: "Seems stuck", waiting: "Has work waiting" };

/** "HH:MM" minus some minutes. */
function before(at: string, minutes: number) {
  const [h, m] = at.split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return at;
  const d = new Date(2000, 0, 1, h, m - minutes);
  return hhmm(d);
}

/** "Stuck 9 min: the same test failed 4 times (38K tokens)." */
function problem(row: ActivityRow) {
  const m = row.measured;
  if (!m) return `${row.title}.`;
  const what = m.what ? (row.action === "nudge" && m.minutes != null ? lowerFirst(m.what) : capital(m.what)) : "";
  const head = row.action === "nudge" && m.minutes != null ? `Stuck ${m.minutes} min${what ? ": " : ""}` : "";
  return `${head}${what}${m.tokens ? ` (${tokens(m.tokens)} tokens)` : ""}.`;
}

function AssiemeCard({ row, onReply }: { row: ActivityRow; onReply: () => void }) {
  const [shown, setShown] = useState(false);
  const noticed = row.measured?.minutes != null ? before(row.at, row.measured.minutes) : null;
  return (
    <div className="panel-card assieme" data-testid="card" data-card>
      <div className="card-who assieme"><AssiemeMark /><span>Assieme is on it · since {row.at}</span></div>
      <div className="card-big">{problem(row)}</div>
      <div className="card-steps">
        {noticed && <><span className="step"><b className="tick">✓</b><span>Noticed {noticed}</span></span><i className="step-line" /></>}
        <span className="step"><b className="tick">✓</b><span>{capital(didText(row))} {row.at}</span></span>
        <i className="step-line" />
        <span className="step-now">◌ Checking{row.check_until ? ` until ${row.check_until}` : ""}</span>
        <i className="step-line soft" />
        <span className="step-next">Fixed, or paused for you</span>
      </div>
      {shown && row.sent && <pre className="card-sent">{row.sent}</pre>}
      <div className="card-buttons">
        {row.sent && !shown && <button type="button" className="link-accent" onClick={() => setShown(true)}>Show the message Assieme sent ›</button>}
        <span className="grow" />
        <button type="button" className="btn-ghost on-tint" onClick={onReply}>✎ Write a reply</button>
      </div>
    </div>
  );
}

export default function AgentPanel({ team, pane, fixes, waiters, failed, review, full = false, onReview, onChanged, onFullSize }: {
  team: string;
  pane: Pane;
  /** Assieme's fix rows for this agent, newest first. */
  fixes: ActivityRow[];
  /** The agents it holds up while it waits for the person. */
  waiters: string[];
  /** Its start failed: why. */
  failed: { reason: string } | null;
  review: ReviewWaiting | null;
  full?: boolean;
  onReview: (team: string, role: string) => void;
  onChanged: () => void;
  onFullSize: (full: boolean) => void;
}) {
  const [d, setD] = useState<Detail | null>(null);
  const [settings, setSettings] = useState(false);
  const [sent, setSent] = useState<{ label: string; at: string; state: Pane["state"] } | null>(null);
  const [note, setNote] = useState("");
  const term = useRef<HTMLDivElement>(null);
  const pv = usePreviews(3000, onReview);
  const role = pane.role;

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const got = await agentDetail(team, role);
        if (!stopped) setD(got ?? null);
      } catch {
        // the last answer stays; the list says when the brain is away
      }
      if (!stopped) timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [team, role]);

  // "Sent …" stays until the agent moves on.
  useEffect(() => {
    if (sent && pane.state !== sent.state) setSent(null);
  }, [pane.state, sent]);

  const reply = () => term.current?.focus();
  const act = async (call: () => Promise<{ ok: boolean; detail: string }>) => {
    try {
      const r = await call();
      setNote(r.ok ? "" : r.detail);
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    }
    onChanged();
  };

  const checking = fixes.find((r) => r.outcome === "checking");
  const p = pv.of(team, role);
  const card = (() => {
    if (sent) return <p className="panel-sent" role="status">Sent ‘{sent.label}’ to {role} at {sent.at}</p>;
    if (failed)
      return (
        <div className="panel-card needs" data-testid="card" data-card>
          <div className="card-who needs"><i className="card-dot" /><span>Couldn't start</span></div>
          <div className="card-big">{failed.reason}</div>
          <p className="card-sub">{/sign/i.test(failed.reason)
            ? `Sign in from ${role}'s terminal below, then try again. The rest of the team keeps working.`
            : "The rest of the team keeps working."}</p>
          {note && <p className="card-note">{note}</p>}
          <div className="card-buttons">
            <button type="button" className="btn-primary" onClick={() => act(() => agentRestart(team, role))}>Try again</button>
            <button type="button" className="btn-outline" onClick={() => act(() => agentRemove(team, role))}>Remove agent</button>
          </div>
        </div>
      );
    if (pane.fix?.pause)
      return (
        <div className="panel-card needs" data-testid="card" data-card>
          <div className="card-who needs"><i className="card-dot" /><span>Paused by Assieme at {pane.fix.pause.at} · Still stuck</span></div>
          <div className="card-big">{pane.fix.text}</div>
          <p className="card-sub">{[pane.fix.detail, "Assieme paused it so it stops using tokens; its work is kept. Resume lets it try again on its own."].filter(Boolean).join(" ")}</p>
          <FixButtons team={team} pane={pane} onChanged={onChanged} onOpenTerminal={reply} />
        </div>
      );
    if (pane.state === "stuck" && checking) return <AssiemeCard row={checking} onReply={reply} />;
    if (pane.fix) {
      const asking = pane.state === "asking" && !pane.fix.budget;
      const who = pane.fix.budget ? "Paused at the daily limit"
        : [SAYS[pane.state] ?? "Could use a hand", mins(pane.since_s), waiters.length ? `holding up ${waiters.join(" and ")}` : ""].filter(Boolean).join(" · ");
      return (
        <div className="panel-card needs" data-testid="card" data-card>
          <div className="card-who needs"><i className="card-dot" /><span>{who}</span></div>
          <div className="card-big">{asking ? `“${pane.fix.text}”` : pane.fix.budget ? pane.fix.text : pane.detail || pane.fix.text}</div>
          {asking && pane.detail && <p className="card-sub">{pane.detail}</p>}
          {pane.fix.budget && pane.fix.detail && <p className="card-sub">{pane.fix.detail}</p>}
          {pane.command && <pre className="card-code">{pane.command}</pre>}
          {pane.reachable === false && <p className="card-sub">It isn’t in a terminal Assieme can reach, so this one’s yours to do there.</p>}
          <FixButtons team={team} pane={pane} onChanged={onChanged} onOpenTerminal={reply}
            onSent={(label) => setSent({ label, at: hhmm(new Date()), state: pane.state })} />
        </div>
      );
    }
    if ((review?.ahead || p) && pane.state !== "working") {
      const n = review?.ahead ?? 0;
      const ago = mins(pane.since_s);
      return (
        <div className="panel-card finished" data-testid="card" data-card>
          <div className="card-who finished"><i className="card-dot" />
            <span>{[ago ? `Finished ${ago} ago` : "Finished", p?.state === "running" ? `preview running at ${host(p)}` : ""].filter(Boolean).join(" · ")}</span>
          </div>
          {n > 0 && <div className="card-big">{n === 1 ? "1 commit is ready to review." : `${n} commits are ready to review.`}</div>}
          <div className="card-buttons">
            {n > 0 && <button type="button" className="btn-primary" onClick={() => onReview(team, role)}>Review changes</button>}
            {!p && <button type="button" className="btn-outline" disabled={pv.busy} onClick={() => pv.start(team, role)}>Preview</button>}
            {p?.state === "running" && <button type="button" className="btn-outline" onClick={() => p.url && openLink(p.url)}>Open preview</button>}
            {p?.state === "failed" && <button type="button" className="btn-outline" onClick={() => onReview(team, role)}>See why</button>}
            {p && p.state !== "failed" && <button type="button" className="btn-ghost" disabled={pv.busy} onClick={() => pv.stop(team, role)}>■ Stop preview</button>}
          </div>
        </div>
      );
    }
    return null;
  })();

  return (
    <div className="agent-panel">
      <div className="panel-head">
        <div className="panel-who">
          <h2>{role}</h2>
          {d && <p>{[kindName(d.cli), d.job].filter(Boolean).join(" · ")}</p>}
        </div>
        <button type="button" className="panel-more" aria-label={`More for ${role}`} onClick={() => setSettings(true)}>•••</button>
      </div>
      {card}
      <div className="panel-lower">
        <div className="panel-term" ref={term} data-terminal tabIndex={-1}
          onFocus={(e) => e.target === e.currentTarget && e.currentTarget.querySelector<HTMLElement>("textarea")?.focus()}>
          <div className="panel-term-bar">
            <span className="grow">Terminal</span>
            <span className="panel-term-hint">⌘L list</span>
            <button type="button" onClick={() => onFullSize(!full)}>{full ? "Show the list" : "Open full size ↗"}</button>
          </div>
          {pane.address && <Terminal session={pane.session ?? "assieme"} address={pane.address} typing={pane.live} />}
        </div>
        {!full && (
          <div className="panel-side">
            <section data-testid="agent-did">
              <h3>What {role} did</h3>
              {(d?.recent ?? []).map((r, i) => <div className="did-row" key={i}><span className="did-at">{r.at}</span><span><Recent text={r.text} /></span></div>)}
              {d && !d.recent.length && <p className="fine">No actions yet.</p>}
            </section>
            <section className="assieme-did" data-testid="assieme-did">
              <h3>What Assieme did</h3>
              {fixes.map((r, i) => <FixRow key={r.id ?? i} row={r} compact onChanged={onChanged} />)}
              {!fixes.length && <p className="fine">No fixes for {role} today.</p>}
            </section>
            {d && <div className="panel-detail"><MemorySection memory={d.memory} /><WakersSection d={d} /></div>}
          </div>
        )}
      </div>
      {settings && <AgentDetail team={team} role={role} only="settings" onClose={() => setSettings(false)} onOpenTerminal={() => { setSettings(false); reply(); }} onChanged={onChanged} />}
    </div>
  );
}
