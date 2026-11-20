import { useState } from "react";
import type { Practices } from "./bridge";
import { AlwaysOnSheet, Chevron, GroupSheet, Shield, ruleCount, type Choice } from "./Practices";
import StepPanel, { type PanelProps } from "./StepPanel";

/** Start › Review team rules: a switch per group and per rule. Turning one off changes this team only.
 *  Paper "v3 — New team: Start › Review team rules". */
export default function TeamRules({ panel, practices, project, value, onChange, onDone }: {
  panel: PanelProps; practices: Practices; project: string; value: Choice; onChange: (v: Choice) => void; onDone: () => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [basics, setBasics] = useState(false);
  const set = (key: string, on: boolean) => onChange({ ...value, [key]: on });
  const group = practices.groups.find((g) => g.id === open);
  const name = project.split("/").filter(Boolean).pop() ?? project;
  return (
    <StepPanel {...panel} title={`Team rules for ${name}`}
      hint="All on. Each one came from a problem on a real team. Turning one off changes this team only." footer={<>
        <span className="safe">You can change these any time in Settings.</span>
        <button type="button" className="primary" onClick={onDone}>Done</button>
      </>}>
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
        <Shield />
        <span className="grow">Plus <b>{practices.basics.length} that are always on</b>, like never answering an agent’s permission prompt for it.</span>
        <button type="button" className="link" onClick={() => setBasics(true)}>See all</button>
      </div>
      {group && <GroupSheet group={group} value={value} onChange={set} onClose={() => setOpen(null)} />}
      {basics && <AlwaysOnSheet basics={practices.basics} onClose={() => setBasics(false)} />}
    </StepPanel>
  );
}
