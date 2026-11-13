import { useState } from "react";
import type { ToolChoice, ToolItem } from "./bridge";
import useModal from "./useModal";
import "./ToolPicker.css";

const GROUPS: [ToolItem["type"], string][] = [["server", "MCP servers"], ["plugin", "Plugins"], ["extension", "Extensions"],
  ["skill", "Skills"]];
const AVOIDS: Record<string, string> = { reviewer: "browser and deploy tools are off.", planner: "browser and deploy tools are off.",
  builder: "deploy tools are off.", tester: "deploy tools are off." };

/** What a chip says: "All skills", a plugin without its marketplace ("planban", not "planban@planban"), else its name. */
export const toolLabel = (i: ToolItem) =>
  i.id === "skill:*" ? "All skills" : i.type === "plugin" ? i.name.split("@")[0] : i.name;

const sameSet =(a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));
const counted = (items: ToolItem[]) => items.filter((i) => !i.locked);

/** "6 of 8": what is on, of what this agent's kind lets Assieme choose. Empty when there is nothing to choose. */
export function toolSummary(items: ToolItem[], value: ToolChoice) {
  const all = counted(items);
  return all.length ? `${all.filter((i) => !value.tools_off.includes(i.id)).length} of ${all.length}` : "";
}

const Lock = () => (
  <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
    <rect x="2.5" y="5.5" width="7" height="5" rx="1.2" stroke="currentColor" strokeWidth="1.3" />
    <path d="M4 5.5V4a2 2 0 0 1 4 0v1.5" stroke="currentColor" strokeWidth="1.3" />
  </svg>
);
const Check = () => (
  <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
    <path d="M2.5 6.2L5 8.5L9.5 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

type Props = { items: ToolItem[]; value: ToolChoice; suggested: string[]; roleName: string; roleKind: string | null;
  kindName: string; onDone: (v: ToolChoice) => void; onClose: () => void };

/** Step 32: which skills, plugins and MCP servers one agent gets (Paper "v2 — Tools: picker for a reviewer", "… builder,
 *  changed and searched", "… Cursor in the shared folder"). Nothing is saved until Done. */
export default function ToolPicker({ items, value, suggested, roleName, roleKind, kindName, onDone, onClose }: Props) {
  const [off, setOff] = useState<string[]>(value.tools_off);
  const [fresh, setFresh] = useState(value.tools_new);
  const [query, setQuery] = useState("");
  const { ref, onKeyDown } = useModal(onClose, []);

  const q = query.trim().toLowerCase();
  const shown = items.filter((i) => !q || `${i.name} ${i.description}`.toLowerCase().includes(q));
  const free = counted(items).map((i) => i.id);
  const toggle = (id: string) => setOff(off.includes(id) ? off.filter((x) => x !== id) : [...off, id]);
  const visible = counted(shown).map((i) => i.id);
  const all = () => setOff(off.filter((id) => !visible.includes(id)));
  const none = () => setOff([...new Set([...off, ...visible])]);
  const isSuggested = sameSet(off.filter((id) => free.includes(id)), suggested.filter((id) => free.includes(id)));
  const allSkills = items.some((i) => i.id === "skill:*");

  return (
    <div className="modal-dim">
      <div className="modal tools" role="dialog" aria-modal="true" aria-label={`Skills and servers for ${roleName}`}
        tabIndex={-1} ref={ref} onKeyDown={onKeyDown}>
        <div className="modal-head">
          <h2>{`Skills and servers for ${roleName}`}</h2>
          <p className="big">{`${kindName} · Only what is on here goes into this agent’s context, so it uses fewer tokens and stays on its job.`}</p>
        </div>
        {roleKind && AVOIDS[roleKind] && (
          <div className="tools-note">
            <span>{isSuggested ? `Suggested for a ${roleKind}: ${AVOIDS[roleKind]}` : `You changed this from the ${roleKind} suggestion.`}</span>
            {!isSuggested && <button type="button" className="link-btn" onClick={() => setOff(suggested)}>Use suggestion</button>}
          </div>
        )}
        <div className="tools-search">
          <label className="tools-field">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
              <circle cx="6" cy="6" r="4.5" stroke="currentColor" strokeWidth="1.4" />
              <path d="M9.5 9.5L12.5 12.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
            <input type="search" placeholder="Search skills and servers" value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
          <button type="button" className="link-btn" onClick={all}>Select all</button>
          <button type="button" className="link-btn" onClick={none}>Clear</button>
          <strong className="tools-count">{`${free.filter((id) => !off.includes(id)).length} of ${free.length}`}</strong>
        </div>
        <div className="tools-groups">
          {GROUPS.map(([type, heading]) => {
            const here = shown.filter((i) => i.type === type);
            const hiddenLocked = type === "skill" && allSkills;  // its single skills are one line, not chips
            const chips = here.filter((i) => !(hiddenLocked && i.locked));
            const lines = [...new Set(here.filter((i) => i.locked).map((i) => i.why))];
            if (!here.length && !(type === "server" && !q)) return null;
            return (
              <div className="tools-group" key={type}>
                <p className="tools-heading">{heading}</p>
                <div className="tools-chips">
                  {chips.map((i) => i.locked ? (
                    <span key={i.id} className="tools-chip locked" aria-disabled="true" title={i.why}><Lock />{toolLabel(i)}</span>
                  ) : (
                    <button key={i.id} type="button" role="checkbox" aria-checked={!off.includes(i.id)} title={i.description || undefined}
                      className={`tools-chip ${off.includes(i.id) ? "off" : "on"}`} onClick={() => toggle(i.id)}>
                      {!off.includes(i.id) && <Check />}{toolLabel(i)}
                    </button>
                  ))}
                  {type === "server" && <span className="tools-chip locked"><Lock />Assieme’s team tools: always on</span>}
                </div>
                {lines.map((w) => <p className="tools-locked" key={w}><Lock />{w}</p>)}
              </div>
            );
          })}
          {q && <p className="tools-match">{`${visible.length} of ${free.length} match “${query.trim()}”.`}</p>}
        </div>
        <div className="tools-new">
          <div>
            <strong id="tools-new-label">Add newly installed ones</strong>
            <span>A skill or server you install later is on for this agent.</span>
          </div>
          <button type="button" role="switch" aria-checked={fresh} aria-labelledby="tools-new-label"
            className={`tools-switch ${fresh ? "on" : ""}`} onClick={() => setFresh(!fresh)}><span /></button>
        </div>
        <div className="sheet-actions">
          <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
          <button type="button" className="btn-primary" onClick={() => onDone({ tools_off: off, tools_new: fresh })}>Done</button>
        </div>
      </div>
    </div>
  );
}
