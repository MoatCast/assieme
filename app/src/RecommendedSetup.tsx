import { useState } from "react";
import type { Practices } from "./bridge";

/** The footer's line on pushing, for both steps: it follows the Safe by default practice. */
export const footerNote = (safe: boolean) =>
  safe ? "Agents work on their own copy and can’t git push. They’re told not to deploy." : "Agents work on their own copy and can git push.";
const rules = (n: number) => (n === 1 ? "1 rule" : `${n} rules`);
const agents = (n: number) => (n === 1 ? "Start 1 agent" : `Start ${n} agents`);

/** New team, step 2: the built-in practices, one switch per group, then Start. */
export default function RecommendedSetup({ practices, roleCount, value, onChange, onBack, onStart, pending, error }: {
  practices: Practices;
  roleCount: number;
  value: Record<string, boolean>;
  onChange: (v: Record<string, boolean>) => void;
  onBack: () => void;
  onStart: () => void;
  pending: boolean;
  error: string;
}) {
  const [basics, setBasics] = useState(false);
  return (
    <div className="newteam">
      <div className="panel">
        <header>
          <span className="muted small">New team · step 2 of 2</span>
          <h1>Recommended setup</h1>
          <p>Habits that kept a real agent team fast and safe for weeks. They’re all on. Turn off anything you don’t want.</p>
        </header>
        <div className="body">
          <div className="agents">
            {practices.groups.map((g) => (
              <label className="practice" key={g.id}>
                <span className="grow">
                  <b>{g.title}</b>
                  <small>{g.summary}</small>
                </span>
                <span className="muted small">{rules(g.rules.length)}</span>
                <input type="checkbox" role="switch" aria-label={g.title} className="toggle"
                  checked={value[g.id] !== false} onChange={(e) => onChange({ ...value, [g.id]: e.target.checked })} />
              </label>
            ))}
          </div>
          <div className="basics">
            <span className="grow">Plus <b>{practices.basics.length} basics that are always on</b>.</span>
            <button type="button" className="link" onClick={() => setBasics(!basics)}>See all</button>
          </div>
          {basics && (
            <ul className="basic-list">
              {practices.basics.map((x) => <li key={x.title}><b>{x.title}</b> {x.detail}</li>)}
            </ul>
          )}
        </div>
        <footer role="contentinfo">
          <span className="safe">{footerNote(value.safety !== false)}</span>
          {error && <span className="error">{error}</span>}
          <button type="button" className="quiet" onClick={onBack}>Back</button>
          <button type="button" className="primary" disabled={pending} onClick={onStart}>{agents(roleCount)}</button>
        </footer>
      </div>
    </div>
  );
}
