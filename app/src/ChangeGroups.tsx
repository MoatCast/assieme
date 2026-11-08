import { useEffect, useState } from "react";
import { getSettings, listTeams, setGroups, type FoundAgent } from "./bridge";
import "./Settings.css";

const NEW = "\u0000new";

/** Which team each agent started outside Assieme belongs to. Assieme only remembers this; agents aren't touched. */
export default function ChangeGroups({ onClose }: { onClose: () => void }) {
  const [agents, setAgents] = useState<FoundAgent[] | null>(null);
  const [picks, setPicks] = useState<Record<string, string>>({});
  const [made, setMade] = useState<string[]>([]);
  const [naming, setNaming] = useState<{ cwd: string; name: string } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    void listTeams().then((r) => setAgents(r.found_agents)).catch((e) => setError(String(e)));
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const value = (a: FoundAgent) => picks[a.cwd] ?? a.group;
  const options = [...new Set([...(agents ?? []).map((a) => a.group), ...made])].sort();
  const choose = (cwd: string, v: string) => {
    if (v === NEW) setNaming({ cwd, name: "" });
    else setPicks((p) => ({ ...p, [cwd]: v }));
  };
  const finishNaming = () => {
    const name = naming?.name.trim();
    if (naming && name) {
      setMade((m) => [...m, name]);
      setPicks((p) => ({ ...p, [naming.cwd]: name }));
    }
    setNaming(null);
  };
  const save = async () => {
    try {
      const { groups } = await getSettings();
      await setGroups({ ...groups, ...picks });
      onClose();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="modal-dim">
      <div className="modal" role="dialog" aria-label="Change the groups">
        <div className="modal-head">
          <h2>Change the groups</h2>
          <p>Agents in one group show up as one team. Assieme only remembers this; your agents aren’t touched.</p>
        </div>
        {agents && agents.length === 0 && <p className="modal-empty">No agents started outside Assieme are running.</p>}
        {agents && agents.length > 0 && (
          <div className="group-list">
            {agents.map((a) => (
              <div className="group-row" key={a.cwd + a.name}>
                <span>{a.name}</span>
                {naming?.cwd === a.cwd ? (
                  <input autoFocus aria-label={`New group for ${a.name}`} placeholder="Group name" value={naming.name}
                    onChange={(e) => setNaming({ cwd: a.cwd, name: e.target.value })} onBlur={finishNaming}
                    onKeyDown={(e) => { if (e.key === "Enter") finishNaming(); e.stopPropagation(); }} />
                ) : (
                  <select aria-label={`Group for ${a.name}`} value={value(a)} onChange={(e) => choose(a.cwd, e.target.value)}>
                    {options.map((o) => <option key={o} value={o}>{o}</option>)}
                    <option value={NEW}>+ New group</option>
                  </select>
                )}
              </div>
            ))}
          </div>
        )}
        {error && <p className="modal-error">{error}</p>}
        <div className="sheet-actions">
          <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
          <button type="button" className="btn-primary" disabled={!agents} onClick={save}>Save</button>
        </div>
      </div>
    </div>
  );
}
