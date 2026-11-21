// Plan limits (Paper "v3 — Main window: command palette, plan limits", node "Plan use"): one bar per agent kind with
// data, slate while there is room, the waste amber once the limit is reached. No data gives no bar, never "0% used".
import type { Limit } from "./bridge";
import "./PlanBars.css";

const NAMES: Record<string, string> = { claude: "Claude", codex: "Codex" };

/** "15:00": local time of an epoch in seconds. */
export const hhmm = (s: number) => new Date(s * 1000).toTimeString().slice(0, 5);

export default function PlanBars({ limits }: { limits: Record<string, Limit> }) {
  const shown = Object.entries(limits).filter(([, l]) => l.reached || l.used !== null);
  if (!shown.length) return null;
  return (
    <div className="plan-bars">
      {shown.map(([kind, l]) => {
        const back = l.resets_at ? `Back at ${hhmm(l.resets_at)}` : "Limit reached";
        return (
          <div key={kind} className={l.reached ? "plan-bar reached" : "plan-bar"} title={l.resets_at ? `Resets at ${hhmm(l.resets_at)}` : undefined}>
            <span className="plan-name">{NAMES[kind] ?? kind}</span>
            <i className="plan-track" aria-hidden="true"><i style={{ width: l.reached ? "100%" : `${Math.min(100, l.used ?? 0)}%` }} /></i>
            <span className="plan-text">{l.reached ? back : `${Math.round(l.used ?? 0)}% used`}</span>
          </div>
        );
      })}
    </div>
  );
}
