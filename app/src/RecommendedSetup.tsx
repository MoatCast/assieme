import { useState } from "react";
import type { Practices, RoleDraft } from "./bridge";
import { AlwaysOnSheet, BiggerTeams, Chevron, GroupSheet, agentCount, ruleCount, type Choice } from "./Practices";

/** The footer's line on pushing, for both steps: it follows the Safe by default practice. */
export const footerNote = (safe: boolean) =>
  safe ? "Agents work on their own copy and can’t git push. They’re told not to deploy." : "Agents work on their own copy and can git push.";
const agents = (n: number) => (n === 1 ? "Start 1 agent" : `Start ${n} agents`);

/** New team, step 2: the built-in practices, a switch per group and per rule, the presets for bigger teams, then Start.
 *  Paper "v2 — New team: recommended setup". */
export default function RecommendedSetup({ practices, roles, value, onChange, onBack, onStart, pending, error }: {
  practices: Practices;
  roles: RoleDraft[];
  value: Choice;
  onChange: (v: Choice) => void;
  onBack: () => void;
  onStart: () => void;
  pending: boolean;
  error: string;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [basics, setBasics] = useState(false);
  const set = (key: string, on: boolean) => onChange({ ...value, [key]: on });
  const group = practices.groups.find((g) => g.id === open);
  const anyBigger = practices.bigger.some((b) => value[b.id] === true);
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
              <div className="practice" key={g.id}>
                <span className="grow">
                  <b>{g.title}</b>
                  <small>{g.summary}</small>
                </span>
                <span className="muted small">{ruleCount(g.rules.length)}</span>
                <input type="checkbox" role="switch" aria-label={g.title} className="toggle"
                  checked={value[g.id] !== false} onChange={(e) => set(g.id, e.target.checked)} />
                <Chevron title={g.title} onClick={() => setOpen(g.id)} />
              </div>
            ))}
          </div>
          <div className="basics">
            <span className="grow">Plus <b>{practices.basics.length} basics that are always on</b>, like never answering an agent’s permission prompt for it.</span>
            <button type="button" className="link" onClick={() => setBasics(true)}>See all</button>
          </div>
          <BiggerTeams bigger={practices.bigger} value={value} note={anyBigger ? "on" : "off"} onChange={set} />
        </div>
        <footer role="contentinfo">
          <span className="safe">You can change these any time in Settings.</span>
          {error && <span className="error">{error}</span>}
          <button type="button" className="quiet" onClick={onBack}>Back</button>
          <button type="button" className="primary" disabled={pending} onClick={onStart}>{agents(agentCount(roles, value))}</button>
        </footer>
      </div>
      {group && <GroupSheet group={group} value={value} onChange={set} onClose={() => setOpen(null)} />}
      {basics && <AlwaysOnSheet basics={practices.basics} onClose={() => setBasics(false)} />}
    </div>
  );
}
