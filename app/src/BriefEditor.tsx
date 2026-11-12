import { useEffect, useRef, useState } from "react";
import { fieldOf, improveBrief, type AgentKind, type KindInfo, type PermissionTable, type RoleDraft } from "./bridge";
import PermissionsEditor, { defaultLevel } from "./PermissionsEditor";
import useModal from "./useModal";
import "./Editors.css";

const KIND_LABELS: [string, string][] = [["planner", "Planner"], ["builder", "Builder"], ["reviewer", "Reviewer"], ["tester", "Tester"]];
const OWNED = /^[A-Za-z0-9._/*@+-][A-Za-z0-9._/*@+ -]*$/;
/** The same check the brain makes on Start: a path inside the project. */
export const badPath = (p: string) => !OWNED.test(p) || p.startsWith("/") || p.split("/").includes("..");
/** An agent's role from its name, for agents that came without one (builder-2 is a builder). */
export const guessKind = (name: string) => /^(planner|builder|reviewer|tester)/.exec(name)?.[1] ?? null;

/** Everyone below `name`: none of them can be the one it reports to, or the team would go round in a loop. */
function below(name: string, roles: RoleDraft[]): Set<string> {
  const out = new Set<string>();
  const walk = (n: string) => roles.filter((r) => r.parent === n && !out.has(r.name)).forEach((r) => { out.add(r.name); walk(r.name); });
  walk(name);
  return out;
}

/** The Brief editor (Paper "v2 — Brief editor"): role, job, brief, the files it owns, who it reports to and its
 *  permissions, with "Improve this brief" from the person's own agent. Nothing reaches the team until Save brief. */
export default function BriefEditor({ role, roles, project, installed, kinds, table, asker, onSave, onCancel }: {
  role: RoleDraft;
  roles: RoleDraft[];
  project: string;
  installed: readonly (readonly [AgentKind, string])[];
  kinds: Record<string, KindInfo>;
  table: PermissionTable;
  /** The person's own agent that improves the brief: "Claude Code" or "Codex". */
  asker: string;
  onSave: (role: RoleDraft) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<RoleDraft>({ ...role, level: role.level ?? defaultLevel(role), owns: role.owns ?? [], rules: role.rules ?? [] });
  const [perms, setPerms] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [improving, setImproving] = useState(false);
  const [undo, setUndo] = useState<string | null>(null);
  const [error, setError] = useState("");
  const gone = useRef(false); // a late Improve answer must not land after Cancel
  useEffect(() => () => void (gone.current = true), []);
  const { ref, onKeyDown } = useModal(() => (adding !== null ? setAdding(null) : onCancel()), [perms]);
  const change = (c: Partial<RoleDraft>) => setDraft((d) => ({ ...d, ...c }));
  const projectName = project.split("/").filter(Boolean).pop() ?? "";
  const templates = Object.values(kinds);
  const level = table.levels.find((l) => l.id === draft.level);
  const parents = roles.filter((r) => r.name !== role.name && !below(role.name, roles).has(r.name));

  const pickKind = (k: string | null) => {
    if (!k || !kinds[k]) return change({ kind: null });
    const t = kinds[k];
    // A template brief or job is replaced; anything the person wrote stays.
    const brief = !draft.brief.trim() || templates.some((x) => x.brief === draft.brief) ? t.brief : draft.brief;
    const job = !draft.job.trim() || templates.some((x) => x.job === draft.job) ? t.job : draft.job;
    change({ kind: k, checkout: t.checkout, level: defaultLevel(t), brief, job });
  };
  const pickAgent = (a: AgentKind) => a !== draft.agent && change({ agent: a, model: null, effort: null });

  const addPath = () => {
    const p = (adding ?? "").trim().replace(/^\.\//, "");
    if (!p) return setAdding(null);
    if (badPath(p)) return setError(`“${p}” isn’t a path inside the project.`);
    setError("");
    if (!draft.owns!.includes(p)) change({ owns: [...draft.owns!, p] });
    setAdding("");
  };

  const improve = async () => {
    setImproving(true);
    setError("");
    try {
      const { brief } = await improveBrief(project, draft);
      if (gone.current) return;
      setUndo(draft.brief);
      change({ brief });
    } catch (e) {
      if (!gone.current) setError(fieldOf(e).message);
    } finally {
      if (!gone.current) setImproving(false);
    }
  };

  const save = () => {
    if (!draft.brief.trim()) return setError("Every agent needs a brief.");
    onSave(draft);
  };

  if (perms)
    return (
      <PermissionsEditor role={draft} roles={roles} project={project} table={table} asker={asker}
        onCancel={() => setPerms(false)} onSave={(c) => { change(c); setPerms(false); }} />
    );

  const rules = draft.rules!.length;
  return (
    <div className="modal-dim">
      <div className="editor brief" role="dialog" aria-modal="true" aria-label={`Edit ${role.name}`} tabIndex={-1}
        ref={ref} onKeyDown={onKeyDown}>
        <div className="editor-head">
          <div className="editor-title">
            <span className="editor-where">New team · {projectName}</span>
            <h2>Brief for <code>{role.name}</code></h2>
          </div>
          {installed.length > 1 && (
            <div className="editor-switch">
              {installed.map(([id, label]) => (
                <button key={id} type="button" className={draft.agent === id ? "on" : ""} aria-pressed={draft.agent === id}
                  onClick={() => pickAgent(id)}>{label}</button>
              ))}
            </div>
          )}
        </div>
        <div className="brief-body">
          <div className="brief-main">
            <div className="editor-field">
              <span className="editor-label">Role</span>
              <div className="editor-chips">
                {[...KIND_LABELS, ["custom", "Custom"] as [string, string]].map(([k, label]) => {
                  const on = (draft.kind ?? "custom") === k;
                  return (
                    <button key={k} type="button" className={on ? "editor-chip on" : "editor-chip"} aria-pressed={on}
                      onClick={() => pickKind(k === "custom" ? null : k)}>{label}</button>
                  );
                })}
              </div>
            </div>
            <label className="editor-field">
              <span className="editor-label">Its job, in one line</span>
              <input className="editor-input" value={draft.job} onChange={(e) => change({ job: e.target.value })} />
            </label>
            <label className="editor-field grow">
              <span className="editor-label-row">
                <span className="editor-label">Brief</span>
                <span className="editor-small">Sent once, when it starts</span>
              </span>
              <textarea className="editor-text" aria-label={`Brief for ${role.name}`} value={draft.brief}
                onChange={(e) => { change({ brief: e.target.value }); setUndo(null); }} />
            </label>
          </div>
          <aside className="brief-side">
            <div className="editor-field">
              <span className="editor-label">Files it owns</span>
              <div className="owned">
                {draft.owns!.map((p) => (
                  <span key={p} className="owned-file">
                    <code>{p}</code>
                    <button type="button" aria-label={`${role.name} no longer owns ${p}`}
                      onClick={() => change({ owns: draft.owns!.filter((x) => x !== p) })}>×</button>
                  </span>
                ))}
                {adding === null ? (
                  <button type="button" className="owned-add" onClick={() => setAdding("")}>+ Add</button>
                ) : (
                  <input className="owned-input" autoFocus aria-label="A file it owns, from the project folder"
                    placeholder="src/retry.py" value={adding} onChange={(e) => setAdding(e.target.value)}
                    onBlur={addPath}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addPath(); } }} />
                )}
              </div>
              <span className="editor-small">
                {draft.owns!.length ? "Its own worktree. Nobody else edits these." : "Its own worktree. Add files to keep it to just those."}
              </span>
            </div>
            <label className="editor-field">
              <span className="editor-label">Reports to</span>
              <select className="editor-select" value={draft.parent ?? ""} onChange={(e) => change({ parent: e.target.value || null })}>
                <option value="">Nobody: it reports to you</option>
                {parents.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
              </select>
            </label>
            <div className="editor-field">
              <span className="editor-label">Permissions</span>
              <span className="editor-soft">
                {level?.summary}{rules ? ` Plus ${rules === 1 ? "1 rule" : `${rules} rules`} of your own.` : ""}
              </span>
              <button type="button" className="editor-link" onClick={() => setPerms(true)}>Change</button>
            </div>
            <div className="editor-tip">
              <b>Added automatically</b>
              <span>Recommended setup adds the report line, stop rules and handoff format. You don’t need to write them.</span>
            </div>
          </aside>
        </div>
        <div className="editor-foot">
          <span className="grow editor-improve">
            <button type="button" className="editor-link" disabled={improving} onClick={() => void improve()}>
              <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M8 1.5l1.6 4.1 4.4.3-3.4 2.8 1.1 4.3L8 10.6 4.3 13l1.1-4.3L2 5.9l4.4-.3z" fill="none"
                  stroke="var(--quiet-ink)" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
              {improving ? `Asking ${asker}…` : `Improve this brief with ${asker === "Codex" ? "Codex" : "Claude"}`}
            </button>
            {undo !== null && !improving && (
              <button type="button" className="editor-link soft" onClick={() => { change({ brief: undo }); setUndo(null); }}>Undo</button>
            )}
            {error && <span className="editor-error" role="alert">{error}</span>}
          </span>
          <button type="button" className="btn-quiet" onClick={onCancel}>Cancel</button>
          <button type="button" className="btn-primary" onClick={save}>Save brief</button>
        </div>
      </div>
    </div>
  );
}
