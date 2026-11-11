import { useEffect, useRef, useState } from "react";
import { budgetSet, type Budget } from "./bridge";
import { tokens } from "./Team";

const ALL = "*";
const shown = (n: number | null) => (n ? String(n / 1e6) : "");

/** Daily limits for one team and its agents, in millions of tokens. Paper "v2 — Team: daily limits sheet". */
export default function DailyLimits({ team, budget, onClose }: { team: string; budget: Budget; onClose: () => void }) {
  const [typed, setTyped] = useState<Record<string, string>>(() =>
    Object.fromEntries([[ALL, shown(budget.limit)], ...budget.agents.map((a) => [a.role, shown(a.limit)])]));
  const [mode, setMode] = useState(budget.at_limit);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const saving = useRef(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const set = (key: string, value: string) => setTyped((t) => ({ ...t, [key]: value }));
  const save = async () => {
    if (saving.current) return;
    const limits: Record<string, number> = {};
    for (const [key, text] of Object.entries(typed)) {
      if (!text.trim()) continue;
      const millions = Number(text.replace(",", "."));
      if (!Number.isFinite(millions) || millions <= 0) return setError("A limit is a number above zero, in millions of tokens. Leave it empty for no limit.");
      limits[key] = Math.round(millions * 1e6);
    }
    saving.current = true;
    setPending(true);
    setError("");
    try {
      await budgetSet(team, limits, mode);
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      saving.current = false;
      setPending(false);
    }
  };

  return (
    <>
      <div className="dim" onClick={onClose} />
      <div className="sheet" role="dialog" aria-label="Daily limits" style={{ gap: 20 }}>
        <div className="sheet-head">
          <div>
            <h2>Daily limits</h2>
            <p>How much {team} may use in a day. It starts again at midnight.</p>
          </div>
          <button type="button" className="sheet-close" aria-label="Close" onClick={onClose}>
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M5 5l10 10M15 5L5 15" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <label className="field">
          <span>The whole team</span>
          <div className="limit-team">
            <input className="mono" inputMode="decimal" aria-label="The whole team, in millions of tokens a day" value={typed[ALL]} placeholder="No limit" onChange={(e) => set(ALL, e.target.value)} />
            <span>million tokens a day · {tokens(budget.used)} used today</span>
          </div>
        </label>
        <div className="field limit-agents">
          <div>
            <div className="label">One agent</div>
            <p>Leave empty for no limit of its own.</p>
          </div>
          {budget.agents.map((a) => (
            <div className="limit-row field" key={a.role}>
              <span className="mono">{a.role}</span>
              <small>{tokens(a.used)} today</small>
              <input className="mono" inputMode="decimal" aria-label={`${a.role}, in millions of tokens a day`} value={typed[a.role] ?? ""} placeholder="No limit" onChange={(e) => set(a.role, e.target.value)} />
              <span>M</span>
            </div>
          ))}
        </div>
        <div className="field">
          <div className="label">When a limit is reached</div>
          <div className="pills">
            <button type="button" className={mode === "pause" ? "on" : ""} aria-pressed={mode === "pause"} onClick={() => setMode("pause")}>Pause and ask me</button>
            <button type="button" className={mode === "tell" ? "on" : ""} aria-pressed={mode === "tell"} onClick={() => setMode("tell")}>Just tell me</button>
          </div>
        </div>
        <div className="sheet-foot">
          {error && <p className="error">{error}</p>}
          <p>Tokens are counted from each agent’s own log, so the count is exact but can be a minute or two behind. It is not a bill: what you pay depends on your plan.</p>
          <div className="sheet-actions">
            <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
            <button type="button" className="btn-primary" disabled={pending} onClick={save}>{pending ? "Saving…" : "Save"}</button>
          </div>
        </div>
      </div>
    </>
  );
}
