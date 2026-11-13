import { useEffect, useState } from "react";
import { activityUndo, herdrImport, listTeams, teamImprove, teamImproveApply, type Finding, type Improvement, type TeamDraft, type TeamListing } from "./bridge";
import useModal from "./useModal";
import "./Settings.css";
import "./ImproveTeam.css";

const things = (n: number) => (n === 1 ? "1 thing would help" : `${n} things would help`);
const agents = (n: number) => (n === 1 ? "1 agent" : `${n} agents`);
const RESTART = "This needs a restart, so Assieme won’t change it while they work.";

function Check() {
  return <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3 3 7-7" fill="none" stroke="var(--quiet-working)" strokeWidth="2" strokeLinecap="round" /></svg>;
}

function InPlace({ items }: { items: string[] }) {
  return items.length ? <div className="improve-in-place"><Check /><span>Already in place: {items.join(", ")}.</span></div> : null;
}

/** What Review shows under a finding: why it isn't applied here, and the one thing to do instead. */
function ReviewBox({ f, onOpen }: { f: Finding; onOpen: (f: Finding) => void }) {
  const [copied, setCopied] = useState(false);
  if (f.copy)
    return (
      <div className="improve-review">
        <p>Paste these rules into its next message:</p>
        <pre>{f.copy.split("\n").slice(1).join("\n")}</pre>
        <div><button type="button" className="btn-small primary" onClick={() => void navigator.clipboard.writeText(f.copy!).then(() => setCopied(true))}>
          {copied ? "Copied" : "Copy the rules"}</button></div>
      </div>
    );
  if (f.restart)
    return (
      <div className="improve-review">
        {f.draft ? (
          <>
            <p>{RESTART} Open the same agents in New team, each on its own branch and with the practices you have on. Yours keep running until you stop them.</p>
            <div><button type="button" className="btn-small primary" onClick={() => onOpen(f)}>Open in New team</button></div>
          </>
        ) : <p>{RESTART} When they’re done, start the team again from New team: each agent gets its own branch and the practices you have on.</p>}
      </div>
    );
  return <div className="improve-review"><p>Assieme leaves your rules and briefs as they are. Change them where the agent reads them; it picks them up at its next start.</p></div>;
}

/** Improve this team: a team started outside Assieme against the recommended setup. Paper "v2 — Improve this team",
 *  "v2 — Improve this team: review open, one applied" and "v2 — Improve this team: choose, none, paste". */
export default function ImproveTeam({ onClose, onDraft }: { onClose: () => void; onDraft: (d: TeamDraft) => void }) {
  const [teams, setTeams] = useState<TeamListing[] | null>(null);
  const [team, setTeam] = useState<string | null>(null);
  const [got, setGot] = useState<Improvement | null>(null);
  const [applied, setApplied] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { ref, onKeyDown } = useModal(onClose, [teams, got]);

  const check = (name: string) => {
    setTeam(name);
    setError("");
    void teamImprove(name).then(setGot).catch((e) => setError(String(e)));
  };
  useEffect(() => {
    void listTeams().then((r) => {
      const found = r.teams.filter((t) => t.kind === "found");
      setTeams(found);
      if (found.length === 1) check(found[0].name);
    }).catch((e) => setError(String(e)));
  }, []);

  const apply = async (ids: string[]) => {
    if (!team || !ids.length) return;
    setBusy(true);
    try {
      const r = await teamImproveApply(team, ids);
      setApplied((a) => ({ ...a, ...Object.fromEntries(r.applied.map((x) => [x.id, x.entry])) }));
      setError(r.applied.length < ids.length ? "Some agents have stopped since, so not everything was applied." : "");
    } catch (e) {
      setError(String(e));
    }
    setBusy(false);
  };
  const undo = async (id: string) => {
    const r = await activityUndo(applied[id]).catch((e) => ({ ok: false, detail: String(e) }));
    if (r.ok) setApplied(({ [id]: _, ...rest }) => rest);
    setError(r.ok ? "" : r.detail);
  };
  const openDraft = async (f: Finding) => {
    try {
      const d = await herdrImport(f.draft!);
      // The fix this finding asks for: its reviewer starts read-only. The rest is the import as it is.
      const reviewer = f.id.startsWith("reviewer:") ? f.id.slice("reviewer:".length) : null;
      onDraft({ ...d, roles: d.roles.map((r) => (r.name === reviewer ? { ...r, checkout: "read-only" } : r)) });
    } catch (e) {
      setError(String(e));
    }
  };
  const toggle = (id: string) => setOpen((o) => (o.includes(id) ? o.filter((x) => x !== id) : [...o, id]));

  const shell = (cls: string, label: string, body: React.ReactNode) => (
    <div className="modal-dim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`improve ${cls}`} role="dialog" aria-label={label} ref={ref} tabIndex={-1} onKeyDown={onKeyDown}>{body}</div>
    </div>
  );
  const err = error && <p className="modal-error">{error}</p>;

  if (teams && !team)
    return teams.length ? shell("small", "Improve which team?", (
      <>
        <div className="improve-head">
          <h2>Improve which team?</h2>
          <p>These teams were started outside Assieme and are running now.</p>
        </div>
        <div className="improve-teams">
          {teams.map((t) => (
            <div className="improve-team" key={t.name}>
              <span className="grow"><b>{t.name}</b><small>{agents(t.agents)}</small></span>
              <button type="button" className="btn-small" onClick={() => check(t.name)}>Check</button>
            </div>
          ))}
        </div>
        {err}
        <div className="sheet-actions"><button type="button" className="btn-quiet" onClick={onClose}>Not now</button></div>
      </>
    )) : shell("small", "No team to improve", (
      <>
        <div className="improve-head">
          <h2>No team to improve</h2>
          <p>No team started outside Assieme is running right now. Teams you start in Assieme already follow these practices.</p>
        </div>
        <div className="sheet-actions"><button type="button" className="btn-primary" onClick={onClose}>Done</button></div>
      </>
    ));

  if (!got)
    return shell("small", "Improve this team", (
      <>
        <div className="improve-head"><h2>Improve this team</h2><p>{team ? `Comparing ${team} with the recommended setup…` : "Looking for teams started outside Assieme…"}</p></div>
        {err}
        {error && <div className="sheet-actions"><button type="button" className="btn-quiet" onClick={onClose}>Close</button></div>}
      </>
    ));

  const kicker = <span className="improve-kicker">{got.team} · started outside Assieme</span>;
  if (!got.findings.length)
    return shell("small", "Nothing to improve", (
      <>
        <div className="improve-head">
          {kicker}
          <h2>Nothing to improve</h2>
          <p>This team already matches the recommended setup, as far as Assieme can see today.</p>
        </div>
        <InPlace items={got.in_place} />
        <div className="sheet-actions"><button type="button" className="btn-primary" onClick={onClose}>Done</button></div>
      </>
    ));

  const toApply = got.findings.filter((f) => f.kind === "apply" && !applied[f.id]).map((f) => f.id);
  const reviews = got.findings.filter((f) => f.kind === "review");
  // Once one row was applied by hand, the footer says it applies the others, not a count to read against the rows.
  const applyAll = Object.keys(applied).length ? `Apply the rest (${toApply.length})` : `Apply ${toApply.length}`;
  const primary = toApply.length && reviews.length && !open.length ? `${applyAll} and review ${reviews.length}`
    : toApply.length ? applyAll : reviews.length && !open.length ? `Review ${reviews.length}` : "Done";
  const go = async () => {
    if (primary === "Done") return onClose();
    await apply(toApply);
    if (reviews.length && !open.includes(reviews[0].id)) setOpen((o) => [...o, reviews[0].id]);
  };
  return shell("wide", "Improve this team", (
    <>
      <div className="improve-head">
        {kicker}
        <h2 className="big">Improve this team</h2>
        <p className="big">Assieme compared your running team with the recommended setup. {things(got.findings.length)}. Each change applies at the agent’s next task, never mid-work.</p>
      </div>
      <div className="improve-list">
        {got.findings.map((f) => (
          <div className="improve-row" key={f.id}>
            <div className="grow">
              <b>{f.title}</b>
              <span>{f.detail}</span>
              {open.includes(f.id) && <ReviewBox f={f} onOpen={(x) => void openDraft(x)} />}
            </div>
            {applied[f.id] ? (
              <span className="improve-applied">
                <span className="improve-done"><Check />Applied</span>
                <button type="button" className="link-undo" onClick={() => void undo(f.id)}>Undo</button>
              </span>
            ) : f.kind === "apply" ? (
              <button type="button" className="btn-small" disabled={busy} onClick={() => void apply([f.id])}>Apply</button>
            ) : (
              <button type="button" className="btn-small" aria-expanded={open.includes(f.id)} onClick={() => toggle(f.id)}>{open.includes(f.id) ? "Hide" : "Review"}</button>
            )}
          </div>
        ))}
      </div>
      <InPlace items={got.in_place} />
      {err}
      <div className="improve-foot">
        <span className="grow">Changes are logged in Activity and can be undone.</span>
        <button type="button" className="btn-quiet" onClick={onClose}>Not now</button>
        <button type="button" className="btn-primary" disabled={busy} onClick={() => void go()}>{primary}</button>
      </div>
    </>
  ));
}
