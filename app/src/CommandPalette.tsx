// The ⌘K palette (Paper "v3 — Main window: command palette, plan limits", node "Command palette"): agents and actions,
// filtered by what is typed, moved through with ↑↓, opened with Return, closed with Escape.
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { Row } from "./listModel";
import "./CommandPalette.css";

export type PaletteAgent = { row: Row; key: string | null };
export type PaletteAction = { label: string; key?: string; run(): void };

export default function CommandPalette({ agents, actions, onAgent, onClose }: {
  agents: PaletteAgent[];
  actions: PaletteAction[];
  onAgent: (role: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [at, setAt] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const id = useId();

  // Focus returns to where it was once the palette closes.
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    input.current?.focus();
    return () => before?.focus?.();
  }, []);

  const q = query.trim().toLowerCase();
  const found = useMemo(() => {
    const has = (...s: string[]) => !q || s.some((x) => x.toLowerCase().includes(q));
    const a = agents.filter((x) => has(x.row.role, x.row.line)).sort((x, y) => x.row.role.localeCompare(y.row.role));
    return { agents: a, actions: actions.filter((x) => has(x.label)) };
  }, [agents, actions, q]);
  const all: (() => void)[] = [...found.agents.map((x) => () => onAgent(x.row.role)), ...found.actions.map((x) => x.run)];
  const active = Math.min(at, Math.max(0, all.length - 1));

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setAt(Math.min(all.length - 1, Math.max(0, active + (e.key === "ArrowDown" ? 1 : -1))));
    } else if (e.key === "Enter" && all[active]) {
      e.preventDefault();
      onClose();
      all[active]();
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      onClose();
    }
  };
  const option = (i: number) => ({
    id: `${id}-${i}`, role: "option", "aria-selected": i === active,
    className: i === active ? "palette-row on" : "palette-row",
    onMouseMove: () => setAt(i), onClick: () => { onClose(); all[i](); },
  });

  return (
    <div className="palette-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Command palette" onKeyDown={onKey}>
        <div className="palette-search">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <circle cx="7" cy="7" r="5" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.6" />
            <path d="M11 11l3.5 3.5" stroke="var(--quiet-slate)" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
          <input ref={input} role="combobox" aria-expanded="true" aria-controls={`${id}-list`} aria-autocomplete="list"
            aria-activedescendant={all.length ? `${id}-${active}` : undefined} aria-label="Search agents and actions"
            value={query} onChange={(e) => { setQuery(e.target.value); setAt(0); }} spellCheck={false} />
          <span className="palette-esc">esc</span>
        </div>
        <div className="palette-results" role="listbox" id={`${id}-list`} aria-label="Agents and actions">
          {found.agents.length > 0 && <div className="palette-label" role="presentation">Agents</div>}
          {found.agents.map((x, i) => (
            <div key={x.row.role} {...option(i)}>
              <i className={`list-dot ${x.row.dot}`} />
              <span className="palette-name">{x.row.role}</span>
              <span className="palette-line">{x.row.line}</span>
              <span className="palette-key">{x.key}</span>
            </div>
          ))}
          {found.actions.length > 0 && <div className="palette-label actions" role="presentation">Actions</div>}
          {found.actions.map((x, j) => (
            <div key={x.label} {...option(found.agents.length + j)}>
              <span className="palette-action">{x.label}</span>
              {x.key && <span className="palette-hint">{x.key}</span>}
            </div>
          ))}
        </div>
        <div className="palette-foot">↑↓ to move · Return to open · ⌘K anywhere</div>
      </div>
    </div>
  );
}
