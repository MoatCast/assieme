// One fix's buttons, on the panel's card, in agent settings and on the Board. A button works once.
// Answers are outlined, in the agent's order, so none reads as Assieme's recommendation; a reply is a ghost button.
import { useState, type ReactNode } from "react";
import { paneAct, type Pane, type PaneButton } from "./bridge";

export default function FixButtons({ team, pane, onChanged, onOpenTerminal, more, onSent }: {
  team: string;
  pane: Pick<Pane, "role" | "fix" | "reachable">;
  onChanged: () => void;
  onOpenTerminal: (role: string) => void;
  /** Shown in place of "Open terminal". */
  more?: ReactNode;
  /** An answer went through: the panel says what was sent. */
  onSent?: (label: string) => void;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [pressed, setPressed] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const fix = pane.fix;
  const press = async (b: PaneButton) => {
    if (b.kind === "reply") return onOpenTerminal(pane.role);
    if (!fix || pressed === fix.id || pending) return;
    setPending(b.id);
    try {
      const r = await paneAct({ team, role: pane.role, fix: fix.id, button: b.id });
      setNote(r.ok ? "" : r.detail);
      if (r.ok) {
        setPressed(fix.id);
        if (b.kind === "answer") onSent?.(b.label);
      }
      onChanged();
    } catch (e) {
      setNote(String(e));
    } finally {
      setPending(null);
    }
  };
  const buttons = fix?.buttons ?? [];
  const label = (b: PaneButton) => (b.kind !== "reply" ? b.label : b.id === "hint" ? "✎ Give it a hint" : "✎ Write a reply");
  const cls = (b: PaneButton) => (b.kind !== "reply" ? "btn-outline" : b.id === "hint" ? "btn-primary" : "btn-ghost");
  return (
    <>
      {note && <p className="card-note">{note}</p>}
      <div className="card-buttons">
        {buttons.map((b) => (
          <button key={b.id} type="button" disabled={pending === b.id || (b.kind !== "reply" && pressed === fix?.id)}
            className={cls(b)} onClick={() => press(b)}>{label(b)}</button>
        ))}
        {more ?? (pane.reachable !== false && !buttons.some((b) => b.kind === "reply") && (
          <button type="button" className="btn-ghost" onClick={() => onOpenTerminal(pane.role)}>Open terminal</button>
        ))}
      </div>
    </>
  );
}
