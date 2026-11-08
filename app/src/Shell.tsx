import { useState } from "react";
import { reviewUndo, type MergeResult, type Snapshot } from "./bridge";
import FirstRun from "./FirstRun";
import NewTeam from "./NewTeam";
import Review, { Notice } from "./Review";
import Terminals from "./Terminals";
import "./Shell.css";

type Tab = "first-run" | "terminals";

/** The live team names, and the one Terminals opens on: most agents at a prompt, else the last listed. */
export function liveTeams(snap: Snapshot | null): { names: string[]; neediest: string | null } {
  const rows = (snap?.teams ?? []).filter((t) => t.state === "live");
  const names = [...new Set(rows.map((t) => t.team))];
  let neediest: string | null = names.length ? names[names.length - 1] : null;
  let most = 0;
  for (const n of names) {
    const blocked = rows.filter((t) => t.team === n && t.status === "blocked").length;
    if (blocked > most) [most, neediest] = [blocked, n];
  }
  return { names, neediest };
}

/** The app's frame: First run and Terminals as tabs, and the team picker for Terminals. */
export default function Shell() {
  const [tab, setTab] = useState<Tab>("first-run");
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [review, setReview] = useState<{ team: string; role: string } | null>(null);
  const [merged, setMerged] = useState<MergeResult | null>(null);
  const [notice, setNotice] = useState("");
  const { names, neediest } = liveTeams(snap);
  const team = picked && names.includes(picked) ? picked : neediest; // a pick sticks until that team stops

  const undo = async (m: MergeResult) => {
    setMerged(null);
    try {
      await reviewUndo(m.undo);
      setNotice(`Undone. ${m.role}'s work is back, ready to review.`);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="shell">
      <div className="shell-bar" hidden={!!review}>
        <svg className="brand" aria-hidden="true" width="18" height="18" viewBox="0 0 18 18">
          <path d="M9 5 4.5 13M9 5l4.5 8" fill="none" stroke="#2747d9" strokeWidth="1.6" strokeLinecap="round" />
          <circle cx="9" cy="4.5" r="2.5" fill="#2747d9" /><circle cx="4.5" cy="13.5" r="2.5" fill="#2747d9" /><circle cx="13.5" cy="13.5" r="2.5" fill="#2747d9" />
        </svg>
        {tab === "terminals" && names.length > 0 && (
          <select className="team-pick" aria-label="Team" value={team ?? ""} onChange={(e) => setPicked(e.target.value)}>
            {names.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        )}
        <div className="tabs" role="tablist">
          {([["first-run", "First run"], ["terminals", "Terminals"]] as const).map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""}
              onClick={() => {
                // Terminals opens on the neediest team and stays there: switching under the person would move
                // focus, and typing, to another team's agent.
                if (id === "terminals" && !(picked && names.includes(picked))) setPicked(neediest);
                setTab(id);
              }}>{label}</button>
          ))}
        </div>
      </div>
      {/* First run stays mounted so its snapshot poll keeps the team list fresh. */}
      <div className="shell-page" hidden={!!review || tab !== "first-run"}>
        <FirstRun pollMs={5000} onSnapshot={setSnap} />
      </div>
      {tab === "terminals" && !review && (
        <div className="shell-page">
          <Terminals key={team ?? ""} team={team} pollMs={3000} onNewTeam={() => setCreating(true)}
            onReview={(t, role) => setReview({ team: t, role })} />
        </div>
      )}
      {review && (
        <div className="shell-page">
          <Review key={`${review.team}/${review.role}`} team={review.team} role={review.role} onBack={() => setReview(null)}
            onMerged={(m) => {
              setReview(null);
              setTab("terminals");
              setNotice("");
              setMerged(m);
            }}
            onDiscarded={(text) => {
              setReview(null);
              setNotice(text);
            }} />
        </div>
      )}
      {merged && (
        <Notice key={merged.undo} ms={120_000} onDone={() => setMerged(null)}
          action={<button type="button" onClick={() => undo(merged)}>Undo</button>}>
          Merged <strong>{merged.role}</strong> into {merged.base}. {merged.problem ?? "Its worktree is archived."}
        </Notice>
      )}
      {notice && <Notice key={notice} ms={8000} onDone={() => setNotice("")}>{notice}</Notice>}
      {creating && (
        <NewTeam clis={{ claude: !!snap?.clis.claude, codex: !!snap?.clis.codex }} onClose={() => setCreating(false)} />
      )}
    </div>
  );
}
