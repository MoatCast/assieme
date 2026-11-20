import { useState } from "react";
import AgentMenu, { ContainerLine } from "./AgentMenu";
import type { AgentKind, ModelOptions, RoleDraft, TeamDraft, ToolItem } from "./bridge";
import { assignMusicians } from "./band";
import ModelMenu, { choiceLabel } from "./ModelMenu";
import Musician from "./Musician";
import type { Clis } from "./StepPanel";
import { toolSummary } from "./ToolPicker";
import useModal from "./useModal";
import Explain from "./Explain";

/** An import from herdr or another tool: what didn't carry over, and the team still running there (if it is). */
export type Imported = Pick<TeamDraft, "notes" | "running" | "from" | "tool" | "label" | "scripts" | "allow_scripts" | "cards">;

/** Start › Edit agents: the agent rows, each with its kind, model, tools and Open › for its instructions, permissions
 *  and tools. Edits apply as they are made; Done and Cancel both close. Paper "v3 — New team: Start › Edit agents". */
export default function EditAgents({ project, roles, installed, models, tools, listKey, roleErrors, why, picked, imported,
  onEdit, onAdd, onRemove, onBrief, onTools, onInstall, onNotReady, onDone }: {
  project: string; roles: RoleDraft[]; installed: Clis; models: ModelOptions | null; tools: Record<string, ToolItem[]>;
  listKey: (r: RoleDraft) => string; roleErrors: Record<number, string>; why: string;
  picked: string; // the team's label, for "Assieme picked these for a …"
  imported: Imported | null;
  onEdit: (i: number, c: Partial<RoleDraft>) => void; onAdd: () => void; onRemove: (i: number) => void;
  onBrief: (i: number) => void; onTools: (i: number) => void; onInstall: (k: AgentKind) => void;
  onNotReady: (agent: AgentKind, notReady: boolean) => void; onDone: () => void;
}) {
  const [modelOf, setModelOf] = useState<number | null>(null);
  const { ref, onKeyDown } = useModal(onDone, []);
  const seats = assignMusicians(roles);
  const name = project.split("/").filter(Boolean).pop() ?? project;
  const title = `Agents for ${name}`;
  return (
    <div className="newteam sheet-layer">
      <div className="panel" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref} onKeyDown={onKeyDown}>
        <header>
          <h1>{title}</h1>
          <p>{why || `Assieme picked these for a ${(picked || "team").toLowerCase()}. Change an agent’s kind here, or open it for its instructions, permissions and tools.`}</p>
        </header>
        <div className="body">
          <div className="field">
            <div className="row">
              <div className="label">Agents</div>
              {roles.some((r) => r.container) && <div className="small">The box icon means it runs sealed off.<Explain term="sealed-off" /></div>}
            </div>
            <div className="agents">
              {roles.map((r, i) => (
                <div className="agent" key={i}>
                  <div className="line">
                    <Musician instrument={seats[i].musician} palette={seats[i].palette} state="idle" role={r.name} size={1} noDot />
                    <input className="name" aria-label="Agent name" value={r.name} onChange={(e) => onEdit(i, { name: e.target.value })} />
                    <AgentMenu look="pill" value={r.agent} container={!!r.container} installed={installed} team={name}
                      onChange={(c) => onEdit(i, c)} onInstall={onInstall} />
                    <span className="job">{r.job}</span>
                    {models && (
                      <button type="button" className="choice" aria-label={`Model and effort for ${r.name}`}
                        aria-expanded={modelOf === i} onClick={() => setModelOf(modelOf === i ? null : i)}>
                        {choiceLabel(r, models)}
                      </button>
                    )}
                    {!!tools[listKey(r)]?.length && (() => {
                      const sum = toolSummary(tools[listKey(r)]!, { tools_off: r.tools_off ?? [], tools_new: r.tools_new ?? true });
                      return sum && (
                        <button type="button" className="link tools-sum" aria-label={`Tools for ${r.name}: ${sum}`}
                          onClick={() => onTools(i)}>Tools <b>{sum}</b></button>
                      );
                    })()}
                    <button type="button" className="link" onClick={() => onBrief(i)}>Open ›</button>
                    <button type="button" className="remove" aria-label="Remove agent" onClick={() => { setModelOf(null); onRemove(i); }}>×</button>
                  </div>
                  {roleErrors[i] && <p className="error" role="alert">{roleErrors[i]}</p>}
                  {r.container && roles.findIndex((x) => x.container && x.agent === r.agent) === i && (
                    <div className="container-row">
                      <ContainerLine agent={r.agent} onReady={(ok) => onNotReady(r.agent, !ok)} />
                    </div>
                  )}
                  {modelOf === i && models && <ModelMenu role={r} options={models} onChange={(c) => onEdit(i, c)} />}
                </div>
              ))}
              <button type="button" className="add" onClick={onAdd}>
                <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                  <path d="M6 1.5v9M1.5 6h9" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
                Add an agent
              </button>
            </div>
          </div>
          {!!imported?.notes?.length && (
            <div className="carried">
              <div className="carried-title">{imported.from === "bringover"
                ? `Brought over from ${imported.tool}. What didn’t carry over` : "Imported from herdr. What didn’t carry over"}</div>
              {imported.notes.map((n) => (
                <div className="carried-note" key={n.text}>
                  <p>{n.text}</p>
                  {n.agents.length > 0 && <p className="carried-agents">{n.agents.join(", ")}</p>}
                </div>
              ))}
            </div>
          )}
        </div>
        <footer role="contentinfo">
          <span className="safe" />
          <button type="button" className="quiet" onClick={onDone}>Cancel</button>
          <button type="button" className="primary" onClick={onDone}>Done</button>
        </footer>
      </div>
    </div>
  );
}
