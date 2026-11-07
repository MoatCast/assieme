import { useState } from "react";
import { fieldOf, suggestTeam, type Suggestion } from "./bridge";

type Q = { key: "size" | "review" | "budget"; label: string; options: [string, string][] };
const QUESTIONS: Q[] = [
  { key: "size", label: "How big is the change?", options: [["small", "Small fix"], ["feature", "A feature"], ["large", "A large change"]] },
  { key: "review", label: "How much do you want to review?", options: [["every", "Every change"], ["end", "Just the end"]] },
  { key: "budget", label: "Budget", options: [["lean", "Keep it lean"], ["fast", "Speed matters more"]] },
];

/** Help me choose: three questions, then the person's own agent suggests a team. */
export default function HelpMeChoose({ project, agent, onDone, onCancel }: {
  project: string;
  agent: "Claude Code" | "Codex";
  onDone: (s: Suggestion) => void;
  onCancel: () => void;
}) {
  const [answers, setAnswers] = useState({ size: "feature", review: "every", budget: "lean" });
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");

  const ask = async () => {
    setAsking(true);
    setError("");
    try {
      onDone(await suggestTeam({ project, ...answers }));
    } catch (e) {
      setError(fieldOf(e).message);
    } finally {
      setAsking(false);
    }
  };

  return (
    <div className="sheet" role="dialog" aria-label="Help me choose">
      <h2>Help me choose</h2>
      {QUESTIONS.map((q) => (
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
        <button type="button" className="quiet" onClick={onCancel}>Cancel</button>
        <button type="button" className="primary" disabled={asking} onClick={ask}>Suggest a team</button>
      </div>
    </div>
  );
}
