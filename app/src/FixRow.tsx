// One fix row everywhere (Team overview, agent panel, Activity, a terminal tile): time, the Assieme mark, the agent,
// the problem as measured → what Assieme did, and the outcome with an icon so colour is never the only signal.
import { useState } from "react";
import type { ActivityRow } from "./bridge";
import { UndoButton } from "./Activity";
import { tokens } from "./listModel";
import "./FixRow.css";

/** Assieme's mark: a 16 px accent square with a white "A". */
export function AssiemeMark({ size = 16 }: { size?: number }) {
  return <span className="assieme-mark" style={{ width: size, height: size }} role="img" aria-label="Assieme">A</span>;
}

const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/** "stuck 9 min, the same test failed 4 times (38K tokens)" */
function measuredText(row: ActivityRow) {
  const m = row.measured;
  if (!m) return "";
  const what = m.what ? lowerFirst(m.what) : "";
  const stuck = row.action === "nudge" && m.minutes != null ? `stuck ${m.minutes} min` : "";
  return [stuck, what].filter(Boolean).join(", ") + (m.tokens ? ` (${tokens(m.tokens)} tokens)` : "");
}

/** What Assieme did, said about the agent as "it": "Asked builder-3 to try another way" → "asked it to try another way". */
function didText(row: ActivityRow) {
  const a = row.agent;
  const t = a ? row.title.split(`${a}'s`).join("its").split(`${a}’s`).join("its").split(a).join("it") : row.title;
  return lowerFirst(t);
}

export function OutcomeTag({ row }: { row: ActivityRow }) {
  if (!row.outcome) return null;
  const text = row.outcome === "checking" ? `◌ Checking${row.check_until ? ` · until ${row.check_until}` : ""}`
    : row.outcome === "fixed" ? "✓ Fixed" : "! Still stuck";
  return <span className={`outcome-tag ${row.outcome}`}>{text}</span>;
}

export default function FixRow({ row, compact = false, onChanged }: { row: ActivityRow; compact?: boolean; onChanged?: () => void }) {
  const [note, setNote] = useState("");
  const measured = measuredText(row);
  return (
    <div className={compact ? "fix-row compact" : "fix-row"}>
      <span className="fix-at">{row.at}</span>
      <AssiemeMark />
      <span className="fix-text" data-testid="fix-text">
        {row.agent && <code>{row.agent}</code>}{row.agent && " · "}
        {note || (measured ? `${measured} → ${didText(row)}` : didText(row))}
      </span>
      {row.undo && onChanged && <UndoButton row={row} className="link" onDone={onChanged} onNote={setNote} />}
      <OutcomeTag row={row} />
    </div>
  );
}
