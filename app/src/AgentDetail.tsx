import { useEffect, useState } from "react";
import { agentDetail, type AgentDetail as Detail } from "./bridge";
import { kindName } from "./kinds";
import { FixButtons } from "./Team";

const HEADS: Record<string, string> = { asking: "Asking you", stuck: "Seems stuck", waiting: "Has work waiting" };
const times = (n: number) => (n === 1 ? "once" : `${n} times`);

function forHow(s: number | null) {
  if (s === null || s < 60) return "";
  const m = Math.floor(s / 60);
  return m < 120 ? ` for ${m} minutes` : ` for ${Math.floor(m / 60)} hours`;
}

/** "Ran pytest, failed" with the program in the mono face, as on Paper. */
function Recent({ text }: { text: string }) {
  const m = /^(Ran|Edited|Took a note from) (.+?)(, failed)?$/.exec(text);
  if (!m) return <>{text}</>;
  return <>{m[1]} <code>{m[2]}</code>{m[3]}</>;
}

/** One agent: what's wrong, how full its memory is, what it's been doing and who wakes it. */
export default function AgentDetail({ team, role, pollMs = 5000, onClose, onOpenTerminal, onChanged }: {
  team: string;
  role: string;
  pollMs?: number;
  onClose: () => void;
  onOpenTerminal: (role: string) => void;
  onChanged: () => void;
}) {
  const [d, setD] = useState<Detail | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const got = await agentDetail(team, role);
        if (!stopped) setD(got);
      } catch {
        // the last answer stays on screen; the map behind it says Reconnecting
      }
      if (!stopped) timer = setTimeout(poll, pollMs);
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [team, role, pollMs]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="dim" onClick={onClose} />
      <div className="sheet" role="dialog" aria-label={`Agent ${role}`}>
        <div className="sheet-head">
          <div>
            <h2 className="mono">{role}</h2>
            {d && <p>{[kindName(d.cli), d.job].filter(Boolean).join(" · ")}</p>}
          </div>
          <button type="button" className="sheet-close" aria-label="Close" onClick={onClose}>
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M5 5l10 10M15 5L5 15" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        {d?.fix && (
          <div className="detail-fix">
            <div className="detail-fix-head"><span className="dot" />{(HEADS[d.state] ?? "Could use a hand") + forHow(d.since_s)}</div>
            <p>{d.state === "asking" ? d.fix.text : d.detail || d.fix.text}</p>
            {d.command && <pre className="card-code">{d.command}</pre>}
            <FixButtons team={team} pane={{ role, fix: d.fix, reachable: d.reachable }} onChanged={onChanged} onOpenTerminal={onOpenTerminal} />
          </div>
        )}
        {d?.memory && (
          <section>
            <div className="meter-head"><b>Memory</b><span className={d.memory.amber ? "amber" : ""}>{d.memory.pct}% full</span></div>
            <div className="meter"><i className={d.memory.amber ? "amber" : ""} style={{ width: `${d.memory.pct}%` }} /></div>
            {d.memory.amber && <p className="fine">Once it’s done, Assieme can free up memory so it doesn’t slow down.</p>}
          </section>
        )}
        {d && d.recent.length > 0 && (
          <section>
            <b>What it’s been doing</b>
            <div className="recent">
              {d.recent.map((r, i) => <div key={i}><span>{r.at}</span><span><Recent text={r.text} /></span></div>)}
            </div>
          </section>
        )}
        {d && (
          <section>
            <b>Who wakes it</b>
            {d.wakers.length > 0 ? (
              <>
                <div className="wakers">
                  {d.wakers.map((w) => <span key={w.role}><code>{w.role}</code> {times(w.count)}</span>)}
                </div>
                <p className="fine">
                  All in the last hour. {d.empty_wakes ? `${d.empty_wakes} of today’s did nothing.` : "None of today’s were empty wake-ups."}
                </p>
              </>
            ) : <p className="fine">Nothing woke it in the last hour.</p>}
          </section>
        )}
      </div>
    </>
  );
}
