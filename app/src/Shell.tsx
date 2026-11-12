import { useEffect, useState } from "react";
import { onOpen, reviewUndo, setSetting, takeDraft, type MergeResult, type Snapshot, type TeamDraft } from "./bridge";
import Activity from "./Activity";
import AddAgent from "./AddAgent";
import Appearance from "./Appearance";
import Board, { NewTask } from "./Board";
import FirstRun from "./FirstRun";
import NewTeam from "./NewTeam";
import Review, { Notice } from "./Review";
import Settings from "./Settings";
import Team from "./Team";
import TeamBrief from "./TeamBrief";
import Terminals from "./Terminals";
import "./Shell.css";

type Tab = "first-run" | "team" | "board" | "terminals" | "activity";

const TABS = [["team", "Team"], ["board", "Board"], ["terminals", "Terminals"], ["activity", "Activity"]] as const;

/** The live team names, and the one to open on: most agents at a prompt, else the last listed, else a found team. */
export function liveTeams(snap: Snapshot | null): { names: string[]; neediest: string | null } {
  const rows = (snap?.teams ?? []).filter((t) => t.state === "live");
  const started = [...new Set(rows.map((t) => t.team))];
  const names = [...new Set([...started, ...(snap?.found_teams ?? []), ...(snap?.scheduled_teams ?? [])])];
  let neediest: string | null = started.length ? started[started.length - 1] : names[0] ?? null;
  let most = 0;
  for (const n of started) {
    const blocked = rows.filter((t) => t.team === n && t.status === "blocked").length;
    if (blocked > most) [most, neediest] = [blocked, n];
  }
  return { names, neediest };
}

/** The app's frame: the top bar with its team picker and tabs, and First run until "Open my team" is pressed. */
export default function Shell() {
  const [tab, setTab] = useState<Tab | null>(null);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [adding, setAdding] = useState(false);
  const [briefing, setBriefing] = useState(false);
  const [tasking, setTasking] = useState(false);
  const [boardKey, setBoardKey] = useState(0);
  const [settings, setSettings] = useState(false);
  const [settingsAt, setSettingsAt] = useState<string | undefined>();
  const [focus, setFocus] = useState<string | null>(null);
  const [review, setReview] = useState<{ team: string; role: string } | null>(null);
  const [merged, setMerged] = useState<MergeResult | null>(null);
  const [notice, setNotice] = useState("");
  const [draft, setDraft] = useState<TeamDraft | null>(null);
  const { names, neediest } = liveTeams(snap);
  const team = picked && names.includes(picked) ? picked : neediest; // a pick sticks until that team stops
  const started = (snap?.teams ?? []).some((t) => t.team === team && t.state === "live");
  // Until the first snapshot answers, nothing decides: First run is what a new person should see.
  const shown: Tab = tab ?? (snap?.first_run_done ? "team" : "first-run");

  useEffect(() => {
    const stop = onOpen(({ team: t, role, view }) => {
      setPicked(t);
      setSettings(false);
      if (view === "review" && role) return setReview({ team: t, role });
      setReview(null);
      setFocus(role);
      setTab("terminals");
    });
    return () => void stop.then((off) => off());
  }, []);

  const openTeam = () => {
    setTab("team");
    void setSetting("first_run_done", true).catch(() => {}); // only a convenience: it asks again next time
  };

  // An assistant's draft opens in New team as soon as nothing is being filled in there.
  const waiting = snap?.draft ?? null;
  useEffect(() => {
    if (!waiting || creating) return;
    void takeDraft(waiting).then((d) => {
      if (!d) return;
      setSettings(false);
      setDraft(d);
      setCreating(true);
    }).catch(() => {}); // the next snapshot tries again
  }, [waiting, creating]);

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
      {shown !== "first-run" && (
        <div className="shell-bar" hidden={!!review} data-tauri-drag-region>
          <div className="shell-left" data-tauri-drag-region>
            <svg className="brand" aria-hidden="true" width="18" height="18" viewBox="0 0 18 18">
              <path d="M9 5 4.5 13M9 5l4.5 8" fill="none" stroke="var(--quiet-accent)" strokeWidth="1.6" strokeLinecap="round" />
              <circle cx="9" cy="4.5" r="2.5" fill="var(--quiet-accent)" /><circle cx="4.5" cy="13.5" r="2.5" fill="var(--quiet-accent)" /><circle cx="13.5" cy="13.5" r="2.5" fill="var(--quiet-accent)" />
            </svg>
            {names.length > 0 && (
              <select className="team-pick" aria-label="Team" value={team ?? ""}
                onChange={(e) => { setPicked(e.target.value); setFocus(null); }}>
                {names.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            )}
            {names.length > 0 && <span className="where">· This Mac</span>}
          </div>
          <div className="tabs" role="tablist">
            {TABS.map(([id, label]) => (
              <button key={id} type="button" role="tab" aria-selected={shown === id} className={shown === id ? "on" : ""}
                onClick={() => {
                  // A tab keeps its team: switching under the person would move focus, and typing, to another
                  // team's agent.
                  if (!(picked && names.includes(picked))) setPicked(neediest);
                  setFocus(null);
                  setTab(id);
                }}>{label}</button>
            ))}
          </div>
          <div className="shell-right" data-tauri-drag-region>
            {started && <button type="button" className="add-agent" onClick={() => setBriefing(true)}>Team brief</button>}
            {team && shown === "board" && (
              <button type="button" className="add-agent" onClick={() => setTasking(true)}>
                <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                  <path d="M6 1.5v9M1.5 6h9" fill="none" stroke="var(--quiet-ink)" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
                New task
              </button>
            )}
            {started && shown !== "board" && (
              <button type="button" className="add-agent" onClick={() => setAdding(true)}>
                <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                  <path d="M6 1.5v9M1.5 6h9" fill="none" stroke="var(--quiet-ink)" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
                Add agent
              </button>
            )}
            <Appearance />
            <button type="button" className="gear" aria-label="Settings" onClick={() => { setSettingsAt(undefined); setSettings(true); }}>
              <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.5" strokeLinecap="round">
                <circle cx="9" cy="9" r="2.4" />
                <path d="M9 2v2M9 14v2M2 9h2M14 9h2M4 4l1.4 1.4M12.6 12.6L14 14M4 14l1.4-1.4M12.6 5.4L14 4" />
              </svg>
            </button>
          </div>
        </div>
      )}
      {shown === "first-run" && !review && <div className="window-strip" data-tauri-drag-region />}
      {/* First run stays mounted so its snapshot poll keeps the team list fresh. */}
      <div className="shell-page" hidden={!!review || shown !== "first-run"}>
        <FirstRun pollMs={5000} onSnapshot={setSnap} onOpenTeam={openTeam} shown={shown === "first-run" && !review}
          onOpenSettings={(section) => { setSettingsAt(section); setSettings(true); }} />
      </div>
      {shown === "team" && !review && (
        <div className="shell-page">
          <Team key={team ?? ""} team={team} pollMs={5000} starting={snap?.starting ?? []}
            onNewTeam={() => setCreating(true)}
            onOpenTerminal={(role) => { setPicked(team); setFocus(role); setTab("terminals"); }}
            onSeeAll={() => setTab("activity")}
            onReview={(t, role) => setReview({ team: t, role })} />
        </div>
      )}
      {shown === "board" && !review && (
        <div className="shell-page">
          <Board key={team ?? ""} team={team} pollMs={5000} refreshKey={boardKey} onNewTeam={() => setCreating(true)} onNewTask={() => setTasking(true)}
            onOpenTerminal={(role) => { setPicked(team); setFocus(role); setTab("terminals"); }}
            onReview={(t, role) => setReview({ team: t, role })} />
        </div>
      )}
      {shown === "terminals" && !review && (
        <div className="shell-page">
          <Terminals key={`${team ?? ""}:${focus ?? ""}`} team={team} pollMs={3000} initialFocus={focus} onNewTeam={() => setCreating(true)}
            onReview={(t, role) => setReview({ team: t, role })} />
        </div>
      )}
      {shown === "activity" && !review && (
        <div className="shell-page">
          <Activity key={team ?? ""} team={team} />
        </div>
      )}
      {review && (
        <div className="shell-page">
          <Review key={`${review.team}/${review.role}`} team={review.team} role={review.role} from={TABS.find(([id]) => id === shown)?.[1]} onBack={() => setReview(null)}
            onMerged={(m) => {
              setReview(null);
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
          Merged <strong>{merged.role}</strong> into {merged.base}. {merged.problem ?? `Its worktree is archived${merged.preview_stopped ? " and its preview stopped" : ""}.`}
        </Notice>
      )}
      {notice && <Notice key={notice} ms={8000} onDone={() => setNotice("")}>{notice}</Notice>}
      {creating && (
        <NewTeam key={draft?.id ?? "new"} clis={snap ? { claude: !!snap.clis.claude, codex: !!snap.clis.codex } : null} draft={draft}
          onClose={() => { setCreating(false); setDraft(null); }} />
      )}
      {adding && team && (
        <AddAgent team={team} roles={(snap?.teams ?? []).filter((t) => t.team === team && t.state === "live").map((t) => t.role ?? t.address)}
          clis={{ claude: !!snap?.clis.claude, codex: !!snap?.clis.codex }} onClose={() => setAdding(false)} />
      )}
      {briefing && team && <TeamBrief team={team} onClose={() => setBriefing(false)} />}
      {tasking && team && (
        <NewTask team={team} roles={(snap?.teams ?? []).filter((t) => t.team === team && t.state === "live").map((t) => t.role ?? t.address)}
          onClose={(added, note) => {
            setTasking(false);
            if (added) setBoardKey((k) => k + 1);
            if (note) setNotice(note);
          }} />
      )}
      {settings && (
        <Settings team={team} section={settingsAt} onClose={() => setSettings(false)}
          onDraft={(d) => { setSettings(false); setDraft(d); setCreating(true); }}
          onShowFirstRun={() => { setSettings(false); setTab("first-run"); }}
          onOpenTeam={(t, role) => {
            setSettings(false);
            setPicked(t);
            if (role) return setReview({ team: t, role });
            setFocus(null);
            setTab("terminals");
          }} />
      )}
    </div>
  );
}
