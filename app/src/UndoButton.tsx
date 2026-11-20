import { useState } from "react";
import { activityUndo, type ActivityRow } from "./bridge";

/** A row's Undo or Stop. Where the brain gives a warning, it asks first: an agent is never ended on one click. */
export function UndoButton({ row, onDone, onNote, className = "" }: {
  row: ActivityRow;
  onDone: () => void;
  onNote: (note: string) => void;
  className?: string;
}) {
  const [asking, setAsking] = useState(false);
  const [pending, setPending] = useState(false);
  const act = async () => {
    if (pending) return;
    setPending(true);
    setAsking(false);
    const r = await activityUndo(row.id!).catch((e) => ({ ok: false, detail: String(e) }));
    onNote(r.ok ? "" : r.detail);
    setPending(false);
    onDone();
  };
  if (asking)
    return (
      <span className="undo-ask">
        <span>{row.undo!.confirm}</span>
        <button type="button" onClick={() => setAsking(false)}>Keep it running</button>
        <button type="button" onClick={act}>{row.undo!.label} it</button>
      </span>
    );
  return (
    <button type="button" className={className} disabled={pending} onClick={() => (row.undo!.confirm ? setAsking(true) : act())}>{row.undo!.label}</button>
  );
}
