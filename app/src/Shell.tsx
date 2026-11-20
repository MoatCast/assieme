import { useEffect, useState } from "react";
import { onNewTeam, onOpen, onOpenSettings,reviewUndo, setSetting, takeDraft, type MergeResult, type Snapshot, type TeamDraft } from "./bridge";
import { installedOf } from "./kinds";
import AddAgent from "./AddAgent";
import Appearance from "./Appearance";
import { NewTask } from "./Board";
import FirstRun from "./FirstRun";
import NewTeam from "./NewTeam";
import Review, { Notice } from "./Review";
import Settings from "./Settings";
import TeamBrief from "./TeamBrief";
import Window, { type OpenLink } from "./Window";
import "./Shell.css";

type Tab = "first-run" | "window";

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

/** The app's frame: the top bar with its team picker, the one window (list and panel), and First run until
 * "Open my team" is pressed. */
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
  const [open, setOpen] = useState<OpenLink | null>(null);
  const [review, setReview] = useState<{ team: string; role: string } | null>(null);
  const [merged, setMerged] = useState<MergeResult | null>(null);
  const [notice, setNotice] = useState("");
  const [draft, setDraft] = useState<TeamDraft | null>(null);
  const { names, neediest } = liveTeams(snap);
  const team = picked && names.includes(picked) ? picked : neediest; // a pick sticks until that team stops
  const started = (snap?.teams ?? []).some((t) => t.team === team && t.state === "live");
  const row = (snap?.teams ?? []).find((t) => t.team === team); // a team on another machine says which (step 15)
  const away = row?.reachable === false;
  // Until the first snapshot answers, nothing decides: First run is what a new person should see.
  const shown: Tab = tab ?? (snap?.first_run_done ? "window" : "first-run");

  useEffect(() => {
    const asked = onOpenSettings(() => { setSettingsAt(undefined); setSettings(true); });
    const newTeam = onNewTeam(() => { setSettings(false); setCreating(true); });
    const stop = onOpen(({ team: t, role, view }) => {
      setPicked(t);
      setSettings(false);
      if (view === "review" && role) return setReview({ team: t, role });
      setReview(null);
      setOpen({ role, view: null, nonce: Date.now() });
      setTab("window");
    });
    return () => { void stop.then((off) => off()); void asked.then((off) => off()); void newTeam.then((off) => off()); };
  }, []);

  // The team shown stays put while another becomes neediest: switching under the person would move focus, and
  // typing, to another team's agent. It changes when they pick one or it stops.
  useEffect(() => {
    if (shown === "window" && team && !(picked && names.includes(picked))) setPicked(team);
  }, [shown, team, picked, names]);

  const openTeam = () => {
    setTab("window");
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

  // Paper "v3 — First run: Settings button": a named pill, the same on every screen.
  const settingsButton = (
    <button type="button" className="settings-btn" onClick={() => { setSettingsAt(undefined); setSettings(true); }}>
      <svg width="14" height="14" viewBox="0 0 20 20" aria-hidden="true">
        <g fill="var(--quiet-slate)">
          {[0, 45].map((turn) => (
            <g key={turn} transform={`rotate(${turn} 10 10)`}>
              <rect x="8.4" y="1.2" width="3.2" height="4" rx="1" /><rect x="8.4" y="14.8" width="3.2" height="4" rx="1" />
              <rect x="1.2" y="8.4" width="4" height="3.2" rx="1" /><rect x="14.8" y="8.4" width="4" height="3.2" rx="1" />
            </g>
          ))}
          <circle cx="10" cy="10" r="6" />
        </g>
        <circle cx="10" cy="10" r="2.4" fill="var(--quiet-surface)" />
      </svg>
      Settings
    </button>
  );

  return (
    <div className="shell">
      {shown !== "first-run" && (
        <div className="shell-bar" hidden={!!review} data-tauri-drag-region>
          <div className="shell-left" data-tauri-drag-region>
            <img className="brand" src="/mark.svg" width="16" height="16" alt="" />
            {names.length > 0 && (
              <select className="team-pick" aria-label="Team" value={team ?? ""}
                onChange={(e) => {
                  if (e.target.value === "new") return setCreating(true); // the picker keeps showing this team
                  setPicked(e.target.value);
                }}>
                {names.map((n) => <option key={n} value={n}>{n}</option>)}
                {/* Paper "v3 — Team menu open: New team"; the Mac's own menu, so a disabled rule stands for the divider. */}
                <option disabled>──────────</option>
                <option value="new">New team…</option>
              </select>
            )}
            {names.length > 0 && <span className="where">· {row?.machine ?? "This Mac"}</span>}
          </div>
          <div className="shell-right" data-tauri-drag-region>
            {started && <button type="button" className="add-agent" disabled={away} onClick={() => setBriefing(true)}>Team brief</button>}
            {started && (
              <button type="button" className="add-agent" disabled={away} onClick={() => setAdding(true)}>
                <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                  <path d="M6 1.5v9M1.5 6h9" fill="none" stroke="var(--quiet-ink)" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
                Add agent
              </button>
            )}
            <Appearance />
            {settingsButton}
          </div>
        </div>
      )}
      {shown === "first-run" && !review && <div className="window-strip" data-tauri-drag-region>{settingsButton}</div>}
      {/* First run stays mounted so its snapshot poll keeps the team list fresh. */}
      <div className="shell-page" hidden={!!review || shown !== "first-run"}>
        <FirstRun pollMs={5000} onSnapshot={setSnap} onOpenTeam={openTeam} shown={shown === "first-run" && !review}
          onOpenSettings={(section) => { setSettingsAt(section); setSettings(true); }} />
      </div>
      {shown === "window" && (
        <div className="shell-page" hidden={!!review}>
          <Window team={team} starting={snap?.starting ?? []} open={open} boardKey={boardKey}
            onNewTeam={() => setCreating(true)} onNewTask={() => setTasking(true)}
            onMerged={(m) => { setNotice(""); setMerged(m); }} onDiscarded={setNotice}
            onOpenFixes={() => { setSettingsAt("fixes"); setSettings(true); }} />
        </div>
      )}
      {review && (
        <div className="shell-page">
          <Review key={`${review.team}/${review.role}`} team={review.team} role={review.role} from="Team" onBack={() => setReview(null)}
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
        <NewTeam key={draft?.id ?? "new"} clis={installedOf(snap?.clis)} draft={draft}
          onClose={() => { setCreating(false); setDraft(null); }} />
      )}
      {adding && team && (
        <AddAgent team={team} roles={(snap?.teams ?? []).filter((t) => t.team === team && t.state === "live").map((t) => t.role ?? t.address)}
          clis={installedOf(snap?.clis)} onClose={() => setAdding(false)} />
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
            setOpen(null);
            setTab("window");
          }} />
      )}
    </div>
  );
}
