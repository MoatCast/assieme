import { useState } from "react";
import type { Snapshot } from "./bridge";
import FirstRun from "./FirstRun";
import NewTeam from "./NewTeam";
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
  const { names, neediest } = liveTeams(snap);
  const team = picked && names.includes(picked) ? picked : neediest; // a pick sticks until that team stops

  return (
    <div className="shell">
      <div className="shell-bar">
        {tab === "terminals" && names.length > 0 && (
          <select className="team-pick" aria-label="Team" value={team ?? ""} onChange={(e) => setPicked(e.target.value)}>
            {names.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        )}
        <div className="tabs" role="tablist">
          {([["first-run", "First run"], ["terminals", "Terminals"]] as const).map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""}
              onClick={() => setTab(id)}>{label}</button>
          ))}
        </div>
      </div>
      {/* First run stays mounted so its snapshot poll keeps the team list fresh. */}
      <div className="shell-page" hidden={tab !== "first-run"}>
        <FirstRun pollMs={5000} onSnapshot={setSnap} />
      </div>
      {tab === "terminals" && (
        <div className="shell-page">
          <Terminals team={team} pollMs={3000} onNewTeam={() => setCreating(true)} />
        </div>
      )}
      {creating && (
        <NewTeam clis={{ claude: !!snap?.clis.claude, codex: !!snap?.clis.codex }} onClose={() => setCreating(false)} />
      )}
    </div>
  );
}
