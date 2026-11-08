import { useEffect, useRef, useState } from "react";
import { addAgent, fieldOf, teamPresets, type AgentKind, type ModelOptions } from "./bridge";

const KINDS = [["builder", "Builder"], ["reviewer", "Reviewer"], ["tester", "Tester"], ["planner", "Planner"]] as const;
const CLIS = [["claude", "Claude Code"], ["codex", "Codex"]] as const;
type Kind = (typeof KINDS)[number][0];

/** The next free name for this kind: builder-4 when builder-1 to 3 are taken. */
export function nextName(kind: string, taken: string[]) {
  if (!taken.includes(kind) && kind !== "builder") return kind;
  let n = 1;
  while (taken.includes(`${kind}-${n}`)) n++;
  return `${kind}-${n}`;
}

/** One more agent for a running team, with the row New team uses. */
export default function AddAgent({ team, roles, clis, onClose }: {
  team: string;
  roles: string[];
  clis: Record<AgentKind, boolean>;
  onClose: () => void;
}) {
  const installed = CLIS.filter(([id]) => clis[id]);
  const [kind, setKind] = useState<Kind>("builder");
  const [name, setName] = useState(nextName("builder", roles));
  const [named, setNamed] = useState(false);
  const [agent, setAgent] = useState<AgentKind>(installed[0]?.[0] ?? "claude");
  const [models, setModels] = useState<ModelOptions | null>(null);
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [parent, setParent] = useState(roles[0] ?? "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const adding = useRef(false); // a second click before React re-renders must not add a second agent

  useEffect(() => {
    void teamPresets().then((p) => setModels(p.models)).catch(() => {}); // Default still works without the lists
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const cli = models?.[agent];
  const efforts = cli?.models.find((m) => m.id === model)?.efforts ?? cli?.efforts ?? [];
  const pick = (k: Kind) => {
    setKind(k);
    if (!named) setName(nextName(k, roles));
  };
  const switchCli = (a: AgentKind) => {
    setAgent(a);
    setModel(""); // each CLI has its own models and efforts
    setEffort("");
  };
  const add = async () => {
    if (adding.current) return;
    adding.current = true;
    setPending(true);
    setError("");
    try {
      await addAgent(team, { name: name.trim(), agent, kind, parent: parent || null, model: model || null, effort: effort || null });
      onClose();
    } catch (e) {
      setError(fieldOf(e).message);
    } finally {
      adding.current = false;
      setPending(false);
    }
  };

  return (
    <>
      <div className="dim" onClick={onClose} />
      <div className="sheet" role="dialog" aria-label="Add agent" style={{ gap: 20 }}>
        <div className="sheet-head">
          <div>
            <h2>Add agent</h2>
            <p>Joins {team} on its own branch.</p>
          </div>
          <button type="button" className="sheet-close" aria-label="Close" onClick={onClose}>
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M5 5l10 10M15 5L5 15" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <label className="field">
          <span>Name</span>
          <input className="mono" value={name} onChange={(e) => { setName(e.target.value); setNamed(true); }} />
        </label>
        <div className="field">
          <div className="label">What it does</div>
          <div className="pills">
            {KINDS.map(([id, label]) => (
              <button key={id} type="button" className={kind === id ? "on" : ""} aria-pressed={kind === id} onClick={() => pick(id)}>{label}</button>
            ))}
          </div>
        </div>
        <div className="field">
          <div className="label">Runs on</div>
          <div className="two-way">
            {installed.map(([id, label]) => (
              <button key={id} type="button" className={agent === id ? "on" : ""} aria-pressed={agent === id} onClick={() => switchCli(id)}>{label}</button>
            ))}
          </div>
        </div>
        <div className="two-fields">
          <label className="field">
            <span>Model</span>
            <select value={model} onChange={(e) => { setModel(e.target.value); setEffort(""); }}>
              <option value="">Default</option>
              {cli?.models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Thinking</span>
            <select value={effort} onChange={(e) => setEffort(e.target.value)}>
              <option value="">Default</option>
              {efforts.map((e) => <option key={e} value={e}>{e}</option>)}
            </select>
          </label>
        </div>
        <label className="field">
          <span>Reports to</span>
          <select className="mono" value={parent} onChange={(e) => setParent(e.target.value)}>
            <option value="">Nobody</option>
            {roles.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <div className="sheet-foot">
          {error && <p className="error">{error}</p>}
          <p>It starts with the standard {kind} brief and your team practices. It can read, edit and run files in {team}.</p>
          <div className="sheet-actions">
            <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
            <button type="button" className="btn-primary" disabled={pending || !installed.length} onClick={add}>{pending ? "Adding…" : "Add agent"}</button>
          </div>
        </div>
      </div>
    </>
  );
}
