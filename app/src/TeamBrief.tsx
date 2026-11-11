import { useEffect, useRef, useState } from "react";
import { briefGet, briefRemove, briefSave, fieldOf, type TeamBriefData } from "./bridge";

/** "today 14:02", or the date for an older one. */
export function when(ms: number, now = Date.now()) {
  const d = new Date(ms);
  return d.toDateString() === new Date(now).toDateString()
    ? `today ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
    : d.toLocaleDateString([], { day: "numeric", month: "short" });
}

/** The brief every agent in the team reads, from Paper "v2 — Team: team brief". Nothing changes until Save. */
export default function TeamBrief({ team, onClose }: { team: string; onClose: () => void }) {
  const [brief, setBrief] = useState<TeamBriefData | null>(null);
  const [goal, setGoal] = useState("");
  const [rules, setRules] = useState("");
  const [removed, setRemoved] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const saving = useRef(false);

  useEffect(() => {
    void briefGet(team).then((b) => { setBrief(b); setGoal(b.goal); setRules(b.rules); }).catch((e) => setError(fieldOf(e).message));
  }, [team]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const save = async () => {
    if (saving.current) return;
    saving.current = true;
    setPending(true);
    setError("");
    try {
      await briefSave(team, goal, rules);
      for (const id of removed) await briefRemove(team, id);
      onClose();
    } catch (e) {
      setError(fieldOf(e).message);
    } finally {
      saving.current = false;
      setPending(false);
    }
  };
  const decisions = (brief?.decisions ?? []).filter((d) => !removed.includes(d.id));

  return (
    <>
      <div className="dim" onClick={onClose} />
      <div className="sheet" role="dialog" aria-label="Team brief" style={{ gap: 20 }}>
        <div className="sheet-head">
          <div>
            <h2>Team brief</h2>
            <p>Every agent in {team} reads this.</p>
          </div>
          <button type="button" className="sheet-close" aria-label="Close" onClick={onClose}>
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M5 5l10 10M15 5L5 15" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <label className="field">
          <span>Goal</span>
          <textarea rows={3} value={goal} disabled={!brief} onChange={(e) => setGoal(e.target.value)} />
        </label>
        <label className="field">
          <span>Rules</span>
          <textarea rows={4} value={rules} disabled={!brief} onChange={(e) => setRules(e.target.value)} />
        </label>
        <div className="field">
          <div className="decisions-head">
            <div className="label">Decisions</div>
            <span>Your answers land here, so nobody asks twice</span>
          </div>
          <div className="decisions">
            {decisions.map((d) => (
              <div key={d.id} className="decision">
                <div>
                  <p>{d.q}</p>
                  <b>{d.a}</b>
                  <small>Asked by <code>{d.role}</code> · {when(d.ms)}</small>
                </div>
                <button type="button" className="link" aria-label={`Remove: ${d.q}`} onClick={() => setRemoved([...removed, d.id])}>Remove</button>
              </div>
            ))}
            {brief && !decisions.length && <p className="none">None yet.</p>}
          </div>
        </div>
        <div className="sheet-foot">
          {error && <p className="error">{error}</p>}
          <p>Agents read the brief when they start and before they ask you anything.</p>
          <div className="sheet-actions">
            <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
            <button type="button" className="btn-primary" disabled={pending || !brief} onClick={save}>{pending ? "Saving…" : "Save"}</button>
          </div>
        </div>
      </div>
    </>
  );
}
