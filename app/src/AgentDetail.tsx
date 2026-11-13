import { useEffect, useState } from "react";
import { agentDetail, agentRestart, agentSetTools, agentTools, type AgentDetail as Detail, type AgentTools,
  type ToolChoice } from "./bridge";
import { kindName } from "./kinds";
import { FixButtons } from "./Team";
import ToolPicker, { toolSummary } from "./ToolPicker";

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

/** "2 of 3 on. You turned off gsap-timeline and 6 more." from Paper "v2 — Tools: agent details, changed". */
function toolsLine(t: AgentTools, v: ToolChoice) {
  const off = t.items.filter((i) => !i.locked && v.tools_off.includes(i.id)).map((i) => (i.id === "skill:*" ? "all skills" : i.name));
  const more = off.length > 1 ? ` and ${off.length - 1} more` : "";
  return `${toolSummary(t.items, v)} on.${off.length ? ` You turned off ${off[0]}${more}.` : ""}`;
}

/** Skills and servers (step 32): what this agent gets, changed here; a change applies when it next starts. */
function ToolsSection({ team, role, onChanged }: { team: string; role: string; onChanged: () => void }) {
  const [t, setT] = useState<AgentTools | null>(null);
  const [picking, setPicking] = useState(false);
  const [changed, setChanged] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    Promise.resolve(agentTools(team, role)).then((got) => setT(got ?? null), () => setT(null));
  }, [team, role]);
  if (!t) return null;
  if (!toolSummary(t.items, t.value)) {
    return t.notices.length ? <section><b>Skills and servers</b>{t.notices.map((n) => <p className="fine" key={n}>{n}</p>)}</section> : null;
  }
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));
  const done = async (v: ToolChoice) => {
    setPicking(false);
    setError("");
    try {
      await agentSetTools(team, role, v);
      setT({ ...t, value: v });
      setChanged(true);
    } catch (e) {
      fail(e);
    }
  };
  const restart = async () => {
    try {
      await agentRestart(team, role);
      setChanged(false);
      onChanged();
    } catch (e) {
      fail(e);
    }
  };
  return (
    <section className="detail-tools">
      <div className="meter-head"><b>Skills and servers</b>
        <button type="button" className="link-btn" onClick={() => setPicking(true)}>Change…</button></div>
      <p className="fine">{toolsLine(t, t.value)}</p>
      {changed && (
        <div className="detail-tools-next">
          <p>{`This applies when ${role} next starts. Restarting now stops what it is doing and picks up its conversation.`}</p>
          <div>
            <button type="button" className="btn-primary" onClick={restart}>Restart with these</button>
            <button type="button" className="btn-secondary" onClick={() => setChanged(false)}>Later</button>
          </div>
        </div>
      )}
      {error && <p className="fine error" role="alert">{error}</p>}
      {picking && (
        <ToolPicker items={t.items} value={t.value} suggested={t.suggested} roleName={role} roleKind={t.role_kind}
          kindName={kindName(t.agent)} onDone={done} onClose={() => setPicking(false)} />
      )}
    </section>
  );
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
        <ToolsSection team={team} role={role} onChanged={onChanged} />
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
