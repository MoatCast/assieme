import { useEffect, useState } from "react";
import {
  bringOverFind, bringOverImport, bringOverWorkspaces, herdrImport, pickFolder, pickScript,
  type BringOverFound, type BringOverProject, type BringOverWorkspaces, type ScriptKind, type TeamDraft,
} from "./bridge";
import useModal from "./useModal";
import "./BringOver.css";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
/** Each tool's heading, in the order the sheet lists them (Paper "v2 — Bring your team over"). */
export const GROUPS: [string, string][] = [
  ["conductor", "From Conductor"], ["vibe-kanban", "From Vibe Kanban"], ["claude-squad", "From Claude Squad"],
  ["herdr", "From herdr"], ["other", "Other agent work found"],
];
export const TOOL_NAMES: Record<string, string> = {
  conductor: "Conductor", "vibe-kanban": "Vibe Kanban", "claude-squad": "Claude Squad", herdr: "herdr",
};
const ASK: Partial<Record<ScriptKind, string>> = {
  setup: "Run this setup in each new agent’s copy", archive: "Run this before a copy is archived",
};

type Picking = { id: string; w: BringOverWorkspaces; ticked: string[]; allow: ScriptKind[] };

/** Bring your team over (step 30): agent work from other tools on this Mac, then what each workspace becomes, then
 *  New team filled in. Nothing is started, and nothing in the other tool changes. From Paper "v2 — Bring your team
 *  over", "… : what it becomes" and "… : nothing found". */
export default function BringOver({ onClose, onImported }: { onClose: () => void; onImported: (draft: TeamDraft) => void }) {
  const [found, setFound] = useState<BringOverFound | null>(null);
  const [picking, setPicking] = useState<Picking | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const look = (folder?: string) => {
    setError("");
    bringOverFind(folder).then(setFound, (e) => {
      setFound({ projects: [], unreadable: [], problems: [], looked_for: [] });
      setError(message(e));
    });
  };
  useEffect(() => look(), []);

  const run = async (key: string, work: () => Promise<void>) => {
    setBusy(key);
    setError("");
    try {
      await work();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy("");
    }
  };
  const open = (p: BringOverProject) => run(p.id, async () => {
    if (p.tool === "herdr") return onImported(await herdrImport(p.id.slice("herdr:".length)));
    const w = await bringOverWorkspaces(p.id);
    setPicking({ id: p.id, w, ticked: w.rows.filter((r) => r.ticked).map((r) => r.name), allow: [] });
  });
  const folder = () => run("folder", async () => {
    const path = await pickFolder().catch(() => null);
    if (path) look(path);
  });
  const script = () => run("script", async () => {
    const path = await pickScript().catch(() => null);
    if (path) onImported(await herdrImport(`script:${path}`));
  });
  const bring = (p: Picking) => run("bring", async () => {
    const ticked = p.w.rows.map((r) => r.name).filter((n) => p.ticked.includes(n)); // the sheet's order
    const d = await bringOverImport(p.id, ticked);
    onImported({ ...d, allow_scripts: p.allow });
  });

  const { ref, onKeyDown } = useModal(() => busy || (picking ? setPicking(null) : onClose()), [found !== null, !!picking]);
  const projects = found?.projects ?? [];
  const notices = [...(found?.problems ?? []), ...(found?.unreadable ?? []).map((r) => `Couldn’t read ${r}.`)];

  if (picking) {
    const { w } = picking;
    const n = picking.ticked.length;
    let k = 0;
    const toggle = (name: string) => setPicking({ ...picking, ticked: picking.ticked.includes(name)
      ? picking.ticked.filter((x) => x !== name) : [...picking.ticked, name] });
    const allow = (which: ScriptKind) => setPicking({ ...picking, allow: picking.allow.includes(which)
      ? picking.allow.filter((x) => x !== which) : [...picking.allow, which] });
    return (
      <div className="modal-dim">
        <div className="modal bring" role="dialog" aria-modal="true" aria-label={`Bring over ${w.label}`} tabIndex={-1}
          ref={ref} onKeyDown={onKeyDown}>
          <div className="modal-head">
            <h2>{`Bring over ${w.label}`}</h2>
            <p className="big">{w.tool === "another tool"
              ? "Found from its branches and agent history. Each workspace becomes an agent on its own branch, in its folder as it is. Nothing is copied, and nothing in the tool it came from changes."
              : `From ${w.tool}. Each workspace becomes an agent on its own branch, in its folder as it is. Nothing is copied, and nothing in ${w.tool} changes.`}</p>
          </div>
          <div className="bring-group">
            <p className="bring-heading">Workspaces</p>
            <ul>
              {w.rows.map((r) => {
                const on = picking.ticked.includes(r.name);
                if (on) k += 1;
                return (
                  <li key={r.name}>
                    <label className="bring-pick">
                      <input type="checkbox" checked={on} onChange={() => toggle(r.name)} />
                      <span className="bring-row">
                        <strong>{r.name} <code>{r.branch}</code></strong>
                        <span>{on ? r.becomes.replace(/builder-\d+/, `builder-${k}`) : r.note || "Left out."}</span>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </div>
          {!!w.scripts.length && (
            <div className="bring-group">
              <p className="bring-heading">{`Scripts from ${w.tool === "Conductor" ? "conductor.json" : w.tool}`}</p>
              <ul className="bring-scripts">
                {w.scripts.map((s) => (
                  <li key={s.which}>
                    <span className="bring-which">{s.which[0].toUpperCase() + s.which.slice(1)}</span>
                    <code>{s.line}</code>
                    <span className="bring-becomes">{s.becomes}</span>
                    {ASK[s.which] && (
                      <label className="bring-allow">
                        <input type="checkbox" checked={picking.allow.includes(s.which)} onChange={() => allow(s.which)} />
                        {ASK[s.which]}
                      </label>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="bring-note">New team opens with these agents filled in. They start only when you click Start, and pick up their conversations where the agent allows it. You can undo this from Activity.</p>
          {error && <p className="bring-error" role="alert">{error}</p>}
          <div className="sheet-actions">
            <button type="button" className="btn-quiet" disabled={!!busy} onClick={() => setPicking(null)}>Back</button>
            <button type="button" className="btn-primary" disabled={!n || !!busy} onClick={() => bring(picking)}>
              {busy === "bring" ? "Bringing over…" : `Bring over ${n === 1 ? "1 agent" : `${n} agents`}`}
            </button>
          </div>
        </div>
      </div>
    );
  }

  const link = (label: string, onClick: () => void) => (
    <button type="button" className="link-btn" disabled={!!busy} onClick={onClick}>{label}</button>
  );
  return (
    <div className="modal-dim">
      <div className="modal bring" role="dialog" aria-modal="true" aria-label="Bring your team over" tabIndex={-1}
        ref={ref} onKeyDown={onKeyDown}>
        <div className="modal-head">
          <h2>Bring your team over</h2>
          <p className="big">{found && !projects.length
            ? `No agent work from other tools on this Mac. Assieme looks for ${(found.looked_for.length ? found.looked_for : ["Conductor", "Vibe Kanban", "Claude Squad", "herdr"]).join(", ").replace(/, ([^,]*)$/, " and $1")}, and for any project with agent branches and history.`
            : "Agent work from other tools on this Mac. Pick a project to see what each part becomes before anything changes. Nothing in the other tool changes."}</p>
        </div>
        {notices.map((n) => <p className="bring-error" role="status" key={n}>{n}</p>)}
        {found === null ? <p className="bring-heading">Looking for agent work…</p> : projects.length ? (
          GROUPS.map(([tool, heading]) => {
            const its = projects.filter((p) => p.tool === tool);
            return its.length ? (
              <div className="bring-group" key={tool}>
                <div className="bring-heading-row">
                  <p className="bring-heading">{heading}</p>
                  {tool === "other" && <p className="bring-aside">from its branches and agent history</p>}
                </div>
                <ul>
                  {its.map((p) => (
                    <li key={p.id}>
                      <div className="bring-row">
                        <strong>{p.label}</strong>
                        <span>{p.summary}</span>
                        {p.running && <span className="bring-running">{p.running}</span>}
                      </div>
                      <button type="button" className="btn-secondary" disabled={!!busy} onClick={() => open(p)}>
                        {busy === p.id ? "Reading…" : "Bring over"}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null;
          })
        ) : (
          <div className="bring-empty">
            <strong>Know where your team’s work is?</strong>
            <p>Choose the project folder. Assieme reads its branches and worktrees, and the agent history saved for it, and shows what it finds before anything changes.</p>
            <button type="button" className="btn-secondary" disabled={!!busy} onClick={folder}>Choose a project folder…</button>
          </div>
        )}
        {error && <p className="bring-error" role="alert">{error}</p>}
        <div className="sheet-actions">
          {found && (projects.length
            ? <span className="bring-foot">Not listed? {link("Choose a project folder…", folder)} or {link("a launch script…", script)}</span>
            : <span className="bring-foot">Start your team with a script? {link("Choose a launch script…", script)}</span>)}
          <button type="button" className="btn-quiet" disabled={!!busy} onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
