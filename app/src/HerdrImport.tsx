import { useEffect, useState } from "react";
import { herdrFind, herdrImport, pickScript, type HerdrTeam, type TeamDraft } from "./bridge";
import useModal from "./useModal";
import "./HerdrImport.css";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const agents = (n: number) => (n === 1 ? "1 agent" : `${n} agents`);

/** Import a team from herdr: what herdr has on this Mac, or a launch script, as a New team draft.
 *  From Paper "v2 — Import from herdr" and "v2 — Import from herdr: nothing found". Nothing is started here. */
export default function HerdrImport({ onClose, onImported }: { onClose: () => void; onImported: (draft: TeamDraft) => void }) {
  const [teams, setTeams] = useState<HerdrTeam[] | null>(null);
  const [unreadable, setUnreadable] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  useEffect(() => {
    herdrFind().then((r) => {
      setTeams(r.teams);
      setUnreadable(r.unreadable ?? "");
    }, (e) => {
      setTeams([]);
      setError(message(e));
    });
  }, []);

  const take = async (source: string) => {
    setBusy(source);
    setError("");
    try {
      onImported(await herdrImport(source));
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy("");
    }
  };
  const script = async () => {
    const path = await pickScript().catch(() => null);
    if (path) await take(`script:${path}`);
  };

  const { ref, onKeyDown } = useModal(() => busy || onClose(), [teams !== null]);
  const chooseScript = (
    <button type="button" className={teams?.length ? "link-btn" : "btn-secondary"} disabled={!!busy} onClick={script}>
      {busy.startsWith("script:") ? "Reading the script…" : "Choose a launch script…"}
    </button>
  );
  return (
    <div className="modal-dim">
      <div className="modal herdr" role="dialog" aria-modal="true" aria-label="Import a team from herdr" tabIndex={-1}
        ref={ref} onKeyDown={onKeyDown}>
        <div className="modal-head">
          <h2>Import a team from herdr</h2>
          <p className="big">Pick one and New team fills in its agents, models and briefs. You check it, then start it. Nothing in herdr changes.</p>
        </div>
        {unreadable && <p className="herdr-error" role="status">{unreadable}</p>}
        {teams === null ? <p className="herdr-quiet">Looking for herdr teams…</p> : teams.length ? (
          <div className="herdr-found">
            <p className="herdr-quiet">Found on this Mac</p>
            <ul>
              {teams.map((t) => (
                <li key={t.id}>
                  <div className="herdr-team">
                    <strong>{t.label}</strong>
                    <span>{t.where} · {agents(t.agents)}</span>
                    {t.running && <span className="herdr-running">Still running in herdr: stop it there first.</span>}
                  </div>
                  <button type="button" className="btn-secondary" disabled={!!busy} onClick={() => take(t.id)}>
                    {busy === t.id ? "Importing…" : "Import"}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <div className="herdr-empty">
            <strong>No herdr teams on this Mac</strong>
            <p>
              {unreadable ? "herdr has no saved workspaces with agents." : "Nothing is running in herdr, and herdr has no saved workspaces with agents."}
              {" "}If a script starts your team, choose it here.
            </p>
            {chooseScript}
          </div>
        )}
        {error && <p className="herdr-error" role="alert">{error}</p>}
        <div className="sheet-actions">
          {!!teams?.length && <span className="herdr-script">Start your team with a script? {chooseScript}</span>}
          <button type="button" className="btn-quiet" disabled={!!busy} onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
