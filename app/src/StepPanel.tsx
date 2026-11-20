import { useEffect, useRef, type ReactNode } from "react";
import type { AgentKind } from "./bridge";
import { indicator, type StepId } from "./newTeamSteps";

export type StepPanelProps = {
  steps: StepId[]; at: StepId; answers: Partial<Record<StepId, string>>;
  title: string; hint?: string; announce?: string;
  onGo: (id: StepId) => void; // a done step in the indicator was clicked
  footer: ReactNode; children: ReactNode;
};
/** What each step receives from NewTeam to draw its panel. */
export type PanelProps = Omit<StepPanelProps, "title" | "hint" | "footer" | "children">;
/** Which agent kinds are installed (null until the service has said). */
export type Clis = Partial<Record<AgentKind, boolean>>;

/** True while a click must be ignored: for `ms` after the step changes, so a double-click can't press the next panel. */
export function useClickGuard(at: StepId, ms = 300): () => boolean {
  const seen = useRef({ at, t: Date.now() });
  if (seen.current.at !== at) seen.current = { at, t: Date.now() };
  return () => Date.now() - seen.current.t < ms;
}

/** One step of New team: the step list, a heading that takes focus, the question's body and a footer that stays put. */
export default function StepPanel({ steps, at, answers, title, hint, announce, onGo, footer, children }: StepPanelProps) {
  const heading = useRef<HTMLHeadingElement>(null);
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // A step that focuses its own default choice (Team's "Same as last time") keeps it.
    if (!body.current?.contains(document.activeElement)) heading.current?.focus();
  }, [at]);
  const items = indicator(steps, at, answers);
  return (
    <div className="newteam">
      <div className="panel">
        <header>
          <nav aria-label="Setup steps">
            <ol className="steps">
              {items.map((s, i) => (
                <li key={s.id} className={s.state}>
                  {i > 0 && <span className="sep" aria-hidden="true">·</span>}
                  {s.state === "done" ? <button type="button" onClick={() => onGo(s.id)}>✓ {s.text}</button>
                    : <span aria-current={s.state === "current" ? "step" : undefined}>{s.text}</span>}
                </li>
              ))}
            </ol>
          </nav>
          <h1 tabIndex={-1} ref={heading} id="step-title">{title}</h1>
          {hint && <p>{hint}</p>}
        </header>
        <div className="body step" key={at} ref={body}>{children}</div>
        <footer role="contentinfo">{footer}</footer>
      </div>
      <p className="sr-only" aria-live="polite">{announce ?? title}</p>
    </div>
  );
}
