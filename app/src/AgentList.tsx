// The list (Paper "Main window", node "List v4"): heading, Needs you / Working / Done, then the views pinned under a rule.
import { useState, type ReactNode } from "react";
import { isGroup, type Group, type ListModel, type Row } from "./listModel";
import "./AgentList.css";

export type View = "overview" | "terminals" | "board" | "activity";
export type Selection = { kind: "agent"; role: string } | { kind: "view"; view: View };

const ICON = { fill: "none", stroke: "var(--quiet-slate)", strokeWidth: 1.4 } as const;
export const VIEWS: { view: View; label: string; icon: ReactNode }[] = [
  { view: "overview", label: "Team overview", icon: (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><circle cx="7" cy="2.5" r="2" {...ICON} /><circle cx="2.5" cy="11.5" r="2" {...ICON} /><circle cx="11.5" cy="11.5" r="2" {...ICON} /><path d="M6 4.3 3.5 9.7M8 4.3l2.5 5.4" {...ICON} /></svg>) },
  { view: "terminals", label: "All terminals", icon: (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><rect x="1" y="1" width="5" height="5" rx="1.2" {...ICON} /><rect x="8" y="1" width="5" height="5" rx="1.2" {...ICON} /><rect x="1" y="8" width="5" height="5" rx="1.2" {...ICON} /><rect x="8" y="8" width="5" height="5" rx="1.2" {...ICON} /></svg>) },
  { view: "board", label: "Board", icon: (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><rect x="1" y="1.5" width="3.4" height="11" rx="1" {...ICON} /><rect x="5.3" y="1.5" width="3.4" height="7" rx="1" {...ICON} /><rect x="9.6" y="1.5" width="3.4" height="9" rx="1" {...ICON} /></svg>) },
  { view: "activity", label: "Activity", icon: (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M1 7h2.6l1.8-4.5 3.2 9 1.8-4.5H13" {...ICON} strokeLinecap="round" strokeLinejoin="round" /></svg>) },
];

const TWO_LINES = ["needs", "assieme", "fixed"];

function Spinner() {
  return (
    <svg className="list-spinner" width="11" height="11" viewBox="0 0 12 12" aria-hidden="true">
      <circle cx="6" cy="6" r="4.5" fill="none" stroke="var(--quiet-assieme-track)" strokeWidth="1.6" />
      <path d="M6 1.5a4.5 4.5 0 0 1 4.5 4.5" fill="none" stroke="var(--quiet-accent)" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function ReviewIcon() {
  const s = { fill: "none", stroke: "var(--quiet-ink)", strokeWidth: 1.3 } as const;
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <circle cx="3" cy="3" r="1.6" {...s} /><circle cx="3" cy="9.5" r="1.6" {...s} /><circle cx="9" cy="6" r="1.6" {...s} />
      <path d="M3 4.6v3.3M4.5 9.2C7 9.2 9 8.5 9 7.6" {...s} />
    </svg>
  );
}

function AgentRow({ row, selected, onSelect }: { row: Row; selected: boolean; onSelect: () => void }) {
  const two = TWO_LINES.includes(row.tone);
  const cls = ["list-row", row.dot === "needs" || (row.dot === "stale" && row.tone === "needs") ? "needs" : "", selected ? "selected" : ""];
  return (
    <button type="button" className={cls.filter(Boolean).join(" ")} aria-label={row.spoken} aria-current={selected ? "true" : undefined}
      data-entry={`agent:${row.role}`} onClick={onSelect}>
      <span className="dot-slot"><i className={`list-dot ${row.dot}`} /></span>
      {two ? (
        <span className="row-lines">
          <span className="row-name">{row.role}</span>
          <span className={`row-line ${row.tone}`}>{row.line}</span>
        </span>
      ) : (
        <>
          <span className="row-name grow">{row.role}</span>
          <span className={`row-line ${row.tone}`}>{row.review !== null && <ReviewIcon />}{row.line}</span>
        </>
      )}
      {(row.right || row.spinner) && (
        <span className={`row-right ${row.tone}`}>{row.spinner && <Spinner />}{row.right}</span>
      )}
    </button>
  );
}

function GroupRows({ group, selected, onSelect }: { group: Group; selected: Selection; onSelect: (s: Selection) => void }) {
  const [open, setOpen] = useState(group.open);
  const [lead, ...rest] = group.summary.split(" · ");
  return (
    <>
      <button type="button" className="list-group" aria-expanded={open} data-entry={`group:${group.name}`} onClick={() => setOpen(!open)}>
        <span className="dot-slot"><span className={open ? "chevron open" : "chevron"} aria-hidden="true">›</span></span>
        <span className="row-name grow">{group.name}</span>
        <span className="row-line mute">
          {group.fixing ? <><b className="assieme-text">{lead}</b>{rest.length ? ` · ${rest.join(" · ")}` : ""}</> : group.summary}
        </span>
      </button>
      {open && <div className="group-rows">{group.rows.map((r) => <AgentRow key={r.role} row={r} selected={isAgent(selected, r.role)} onSelect={() => onSelect({ kind: "agent", role: r.role })} />)}</div>}
    </>
  );
}

const isAgent = (s: Selection, role: string) => s.kind === "agent" && s.role === role;

function Section({ label, cls, items, selected, onSelect }: {
  label: string; cls: string; items: (Row | Group)[]; selected: Selection; onSelect: (s: Selection) => void;
}) {
  if (!items.length) return null;
  return (
    <>
      <div className={`list-label ${cls}`}>{label}</div>
      {items.map((x) => isGroup(x)
        ? <GroupRows key={`g:${x.name}`} group={x} selected={selected} onSelect={onSelect} />
        : <AgentRow key={x.role} row={x} selected={isAgent(selected, x.role)} onSelect={() => onSelect({ kind: "agent", role: x.role })} />)}
    </>
  );
}

export default function AgentList({ model, selected, views, onSelect, children }: {
  model: ListModel;
  selected: Selection;
  /** The views' counts; none when no team runs (the list is its heading alone). */
  views?: Record<View, string>;
  onSelect: (s: Selection) => void;
  /** Under the heading, before the sections: New team when no team runs. */
  children?: ReactNode;
}) {
  return (
    <nav className={model.stale ? "agent-list stale" : "agent-list"} aria-label="Agents">
      <div className="list-heading">
        <div className="list-verdict">{model.verdict}</div>
        {model.counts && <div className="list-counts">{model.counts}</div>}
        {model.assieme && (
          <button type="button" className="list-assieme" onClick={() => onSelect({ kind: "view", view: "overview" })}>
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.2l2.3 2.3L9.5 3.5" fill="none" stroke="var(--quiet-accent)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
            {model.assieme}
          </button>
        )}
        {children}
      </div>
      <div className="agent-sections" data-testid="agent-sections">
        <Section label="Needs you" cls="needs" items={model.needs} selected={selected} onSelect={onSelect} />
        <Section label="Working" cls="" items={model.working} selected={selected} onSelect={onSelect} />
        <Section label="Done" cls="" items={model.done} selected={selected} onSelect={onSelect} />
      </div>
      {views && <div className="list-views">
        {VIEWS.map((v) => {
          const on = selected.kind === "view" && selected.view === v.view;
          return (
            <button key={v.view} type="button" className={on ? "list-row view selected" : "list-row view"} aria-current={on ? "true" : undefined}
              data-entry={`view:${v.view}`} onClick={() => onSelect({ kind: "view", view: v.view })}>
              <span className="dot-slot">{v.icon}</span>
              <span className="view-name grow">{v.label}</span>
              <span className="row-line mute">{views[v.view]}</span>
            </button>
          );
        })}
      </div>}
    </nav>
  );
}
