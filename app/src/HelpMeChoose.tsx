import { useEffect, useRef, useState } from "react";
import { fieldOf, suggestTeam, type Suggestion } from "./bridge";
import { teamSummary } from "./newTeamSteps";
import { suggestTeamOn } from "./remote";

type Q = { key: "size" | "review" | "budget"; label: string; options: [string, string][] };
const QUESTIONS: Q[] = [
  { key: "size", label: "How big is the change?", options: [["small", "Small fix"], ["feature", "A feature"], ["large", "A large change"]] },
  { key: "review", label: "How much do you want to review?", options: [["every", "Every change"], ["end", "Just the end"]] },
  { key: "budget", label: "Budget", options: [["lean", "Keep it lean"], ["fast", "Speed matters more"]] },
];

/** Help me choose, inside the Team step: the person's own agent reads the task (or, with none, hears how big the change
 *  is) and suggests a team, which the person confirms with Use this team. */
export default function HelpMeChoose({ project, agent, task = "", onDone, onCancel, machine }: {
  /** Another machine (step 15): its own agent reads the project there. */
  machine?: string;
  project: string;
  agent: string;
  task?: string;
  onDone: (s: Suggestion) => void;
  onCancel: () => void;
}) {
  const [answers, setAnswers] = useState({ size: "feature", review: "every", budget: "lean" });
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const [got, setGot] = useState<Suggestion | null>(null);
  const gone = useRef(false); // set on Cancel or close: a late answer must not replace the person's agents
  useEffect(() => () => void (gone.current = true), []);
  const cancel = () => {
    gone.current = true;
    onCancel();
  };

  const ask = async () => {
    setAsking(true);
    setError("");
    try {
      const a = task.trim() ? { project, ...answers, size: "", task: task.trim() } : { project, ...answers };
      const s = await (machine ? suggestTeamOn(a, machine) : suggestTeam(a));
      if (!gone.current) setGot(s);
    } catch (e) {
      if (!gone.current) setError(fieldOf(e).message);
    } finally {
      if (!gone.current) setAsking(false);
    }
  };

  if (got)
    return (
      <div className="sheet" role="group" aria-label="Help me choose">
        <h2>Suggested team</h2>
        <p className="muted">{got.why}</p>
        <p><b>{teamSummary(got.roles)}</b></p>
        <div className="actions">
          <button type="button" className="quiet" onClick={() => setGot(null)}>Back</button>
          <button type="button" className="primary" onClick={() => onDone(got)}>Use this team</button>
        </div>
      </div>
    );
  return (
    <div className="sheet" role="group" aria-label="Help me choose">
      <h2>Help me choose</h2>
      {QUESTIONS.filter((q) => q.key !== "size" || !task.trim()).map((q) => (
        <div className="field" key={q.key}>
          <div className="label">{q.label}</div>
          <div className="chips">
            {q.options.map(([value, text]) => (
              <button key={value} type="button" className={answers[q.key] === value ? "chip on" : "chip"}
                aria-pressed={answers[q.key] === value} onClick={() => setAnswers({ ...answers, [q.key]: value })}>
                {text}
              </button>
            ))}
          </div>
        </div>
      ))}
      {error && <p className="error">{error}</p>}
      <div className="actions">
        {asking && <span className="muted">Asking {agent}…</span>}
        <button type="button" className="quiet" onClick={cancel}>Cancel</button>
        <button type="button" className="primary" disabled={asking} onClick={ask}>Suggest a team</button>
      </div>
    </div>
  );
}
