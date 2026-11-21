import { useEffect, useRef, useState } from "react";
import "./Explain.css";

type Term = "team-rules" | "copy" | "sealed-off" | "permissions" | "plan-limits";

const QUESTION: Record<Term, string> = {
  "team-rules": "What are team rules?",
  copy: "What is a copy?",
  "sealed-off": "What does sealed off mean?",
  permissions: "What are permissions?",
  "plan-limits": "What are plan limits?",
};

// From the power-tools spec § 5.
export const EXPLAIN: Record<Term, string> = {
  "team-rules": "Habits Assieme adds to each agent's instructions, so messages stay short and work stays safe. You can turn groups off for this team.",
  copy: "Each agent works in its own copy of your project, so agents never edit the same files. You choose what gets merged.",
  "sealed-off": "The agent runs in a container that sees only its copy. It needs Docker.",
  permissions: "What an agent may do without asking you. Pushing is always blocked.",
  "plan-limits": "Your Claude or ChatGPT plan allows a certain amount of work every few hours. Agents wait when it runs out.",
};

/** A "?" after a hard term: one or two sentences in place, with no link out. Escape or a click outside closes it. */
export default function Explain({ term }: { term: Term }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation(); // closes the text, not the sheet around it
      setOpen(false);
      button.current?.focus();
    };
    const down = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("keydown", key, true);
    document.addEventListener("mousedown", down);
    return () => {
      document.removeEventListener("keydown", key, true);
      document.removeEventListener("mousedown", down);
    };
  }, [open]);
  return (
    <span className="explain" ref={box}>
      <button type="button" ref={button} className="explain-q" aria-label={QUESTION[term]} aria-expanded={open}
        onClick={() => setOpen(!open)}>?</button>
      {open && <span className="explain-text" role="note">{EXPLAIN[term]}</span>}
    </span>
  );
}
