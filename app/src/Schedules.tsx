import { useEffect, useRef, useState } from "react";
import {
  checkProject, installTools, pickFolder, scheduleList, schedulePause, scheduleRemove, scheduleRunNow, scheduleSave, scheduleStop,
  type Every, type ProjectCheck, type Schedule, type ScheduleTeam,
} from "./bridge";
import FolderSheet from "./FolderSheet";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const shortPath = (p: string) => p.replace(/^\/Users\/[^/]+/, "~");
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const FIELD = /^(name|project|preset|task|every|run): ([\s\S]*)$/;

/** When the first run after saving is, in the words the rows use. Hours count from saving. */
export function nextRun(every: Every, now = new Date()): Date {
  if ("n" in every) return new Date(now.getTime() + every.n * (every.kind === "hours" ? 3600e3 : 60e3));
  const [h, m] = every.at.split(":").map(Number);
  const t = new Date(now);
  t.setHours(h, m, 0, 0);
  if (every.kind === "week") t.setDate(t.getDate() + ((every.day - ((t.getDay() + 6) % 7) + 7) % 7));
  if (t <= now) t.setDate(t.getDate() + (every.kind === "week" ? 7 : 1));
  return t;
}

export function dayWords(t: Date, now = new Date()): string {
  const days = Math.round((new Date(t).setHours(0, 0, 0, 0) - new Date(now).setHours(0, 0, 0, 0)) / 86400e3);
  const day = days === 0 ? "today" : days === 1 ? "tomorrow" : days < 7 ? DAYS[(t.getDay() + 6) % 7] : t.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return `${day} at ${t.toTimeString().slice(0, 5)}`;
}

function Sheet({ edit, teams, onClose, onSaved }: { edit: Schedule | null; teams: ScheduleTeam[]; onClose: () => void; onSaved: (rows: Schedule[]) => void }) {
  const [name, setName] = useState(edit?.name ?? "");
  const [project, setProject] = useState(edit?.project ?? "");
  const [task, setTask] = useState(edit?.task ?? "");
  const [preset, setPreset] = useState(edit?.preset ?? teams[0]?.id ?? "one-agent");
  const first = edit?.every;
  const [kind, setKind] = useState<"day" | "week" | "hours">(first?.kind === "week" || first?.kind === "hours" ? first.kind : "day");
  const [at, setAt] = useState(first && "at" in first ? first.at : "02:00");
  const [day, setDay] = useState(first?.kind === "week" ? first.day : 0);
  const [hours, setHours] = useState(first?.kind === "hours" ? String(first.n) : "6");
  const [check, setCheck] = useState<ProjectCheck | null>(null);
  const [setup, setSetup] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const latest = useRef(0);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !setup && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, setup]);

  const verify = async (p: string) => {
    const id = ++latest.current;
    try {
      const c = await checkProject(p);
      if (id === latest.current) setCheck(c);
    } catch (e) {
      if (id === latest.current) setErrors((x) => ({ ...x, project: message(e) }));
    }
  };
  useEffect(() => { if (project) void verify(project); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const choose = async () => {
    const p = await pickFolder();
    if (!p) return;
    setProject(p);
    setCheck(null);
    setErrors((x) => ({ ...x, project: "" }));
    void verify(p);
  };

  const every: Every | null = kind === "hours" ? (/^\d+$/.test(hours) ? { kind, n: Number(hours) } : null)
    : kind === "week" ? { kind, day, at } : { kind, at };
  const unchanged = edit && JSON.stringify(edit.every) === JSON.stringify(every);
  const next = unchanged ? new Date(edit.next) : every && (kind !== "hours" || every.kind === "hours" && every.n >= 1) ? nextRun(every) : null;

  const save = async () => {
    if (!every) return setErrors({ every: "Give a number of hours." });
    setPending(true);
    setErrors({});
    try {
      onSaved(await scheduleSave({ ...(edit ? { id: edit.id } : {}), name, project, preset, task, every }));
    } catch (e) {
      const m = FIELD.exec(message(e));
      setErrors(m ? { [m[1]]: m[2] } : { run: message(e) });
    } finally {
      setPending(false);
    }
  };
  const remove = async () => {
    try {
      onSaved(await scheduleRemove(edit!.id));
    } catch (e) {
      setErrors({ run: message(e) });
    }
  };

  const folderButton = check?.tools ? { label: "Install developer tools", act: () => void installTools().then(() => verify(project)) }
    : check?.setup ? { label: "Set up this folder", act: () => setSetup(true) } : null;
  const error = (k: string) => errors[k] && <p className="error">{errors[k]}</p>;
  const chosen = teams.find((t) => t.id === preset);

  return (
    <>
      <div className="dim" onClick={onClose} />
      <div className="sheet schedule-sheet" role="dialog" aria-label={edit ? "Edit schedule" : "New schedule"} style={{ gap: 20 }}>
        <div className="sheet-head">
          <div>
            <h2>{edit ? "Edit schedule" : "New schedule"}</h2>
            <p>{edit ? "Changes apply from the next run. A run already going carries on." : "A small team starts on its own, does this, and stops when it says it is done."}</p>
          </div>
          <button type="button" className="sheet-close" aria-label="Close" onClick={onClose}>
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M5 5l10 10M15 5L5 15" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <label className="field">
          <span>Name</span>
          <input value={name} placeholder="Nightly tests" onChange={(e) => setName(e.target.value)} />
          {error("name")}
        </label>
        <div className="field">
          <div className="label">Project folder</div>
          <div className="folder-pick">
            <span className="mono grow">{project ? shortPath(project) : "No folder chosen"}</span>
            <button type="button" className="btn-small" onClick={choose}>Choose…</button>
          </div>
          {check && !check.ok && (
            <div className="folder-note">
              <span>{check.reason}</span>
              {folderButton && <button type="button" className="btn-small" onClick={folderButton.act}>{folderButton.label}</button>}
            </div>
          )}
          {error("project")}
        </div>
        <label className="field">
          <span>What to do</span>
          <textarea rows={3} value={task} placeholder="Run the tests. Fix what breaks, one commit per fix." onChange={(e) => setTask(e.target.value)} />
          {error("task")}
        </label>
        <label className="field">
          <span>Team</span>
          <select aria-label="Team" value={preset} onChange={(e) => setPreset(e.target.value)}>
            {teams.map((t) => <option key={t.id} value={t.id}>{t.label} · {t.who}</option>)}
            {!chosen && <option value={preset}>{preset.replace(/^saved:/, "")}</option>}
          </select>
          <p className="hint">Or a team you saved, with its agents and models. Daily limits you set on it apply to every run.</p>
          {error("preset")}
        </label>
        <div className="field">
          <div className="label">When</div>
          <div className="pills">
            {([["day", "Every day"], ["week", "Every week"], ["hours", "Every few hours"]] as const).map(([k, label]) => (
              <button key={k} type="button" className={kind === k ? "on" : ""} aria-pressed={kind === k} onClick={() => setKind(k)}>{label}</button>
            ))}
          </div>
          <div className="when-row">
            {kind === "week" && (
              <>
                <span>on</span>
                <select aria-label="Day" value={day} onChange={(e) => setDay(Number(e.target.value))}>
                  {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
                </select>
              </>
            )}
            {kind === "hours" ? (
              <>
                <span>every</span>
                <input className="mono hours" aria-label="Hours" inputMode="numeric" value={hours} onChange={(e) => setHours(e.target.value)} />
                <span>hours{next ? ` · Next run: ${dayWords(next)}` : ""}</span>
              </>
            ) : (
              <>
                <span>at</span>
                <input className="mono time" type="time" aria-label="Time" value={at} onChange={(e) => setAt(e.target.value)} />
                {next && <span>Next run: {dayWords(next)}</span>}
              </>
            )}
          </div>
          {error("every")}
        </div>
        <div className="sheet-foot">
          {error("run")}
          <p>Runs start even when this app is closed: the background service starts them.</p>
          <div className="sheet-actions">
            {edit && <button type="button" className="btn-quiet remove" onClick={remove}>Remove schedule</button>}
            <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
            <button type="button" className="btn-primary" disabled={pending || !name.trim() || !project || !task.trim()} onClick={save}>{pending ? "Saving…" : "Save"}</button>
          </div>
        </div>
      </div>
      {setup && <FolderSheet project={project} onClose={() => setSetup(false)} onSaved={() => { setSetup(false); void verify(project); }} />}
    </>
  );
}

/** Settings, Scheduled runs: Paper "v2 — Settings: scheduled runs" and its sheets. */
export default function Schedules({ onOpen }: { onOpen: (team: string, role: string | null) => void }) {
  const [rows, setRows] = useState<Schedule[] | null>(null);
  const [teams, setTeams] = useState<ScheduleTeam[]>([]);
  const [editing, setEditing] = useState<Schedule | "new" | null>(null);
  const [error, setError] = useState("");
  const load = () => scheduleList().then((r) => { setRows(r.schedules); setTeams(r.teams); }).catch((e) => setError(message(e)));
  useEffect(() => {
    void load();
    const t = setInterval(load, 5000); // a run starts, ends or is skipped on its own
    return () => clearInterval(t);
  }, []);
  const act = async (call: () => Promise<Schedule[]>) => {
    setError("");
    try {
      setRows(await call());
    } catch (e) {
      setError(message(e).replace(FIELD, "$2"));
    }
  };

  const buttons = (s: Schedule) => {
    const out: React.ReactNode[] = [];
    if (s.state === "running") {
      out.push(<button key="open" type="button" className="btn-small" onClick={() => onOpen(s.team, null)}>Open</button>);
      out.push(<button key="stop" type="button" className="btn-small" onClick={() => act(() => scheduleStop(s.id))}>Stop run</button>);
    } else if (!s.paused && s.result === "failed") {
      out.push(<button key="again" type="button" className="btn-small" onClick={() => act(() => scheduleRunNow(s.id))}>Try again now</button>);
    } else if (!s.paused && s.review_role && (s.result === "finished" || s.result === "ended" || s.result === "stopped")) {
      out.push(<button key="review" type="button" className="btn-small" onClick={() => onOpen(s.team, s.review_role)}>Review</button>);
    }
    out.push(<button key="pause" type="button" className="btn-small" onClick={() => act(() => schedulePause(s.id, !s.paused))}>{s.paused ? "Resume" : "Pause"}</button>);
    out.push(<button key="edit" type="button" className="btn-small" onClick={() => setEditing(s)}>Edit</button>);
    return out;
  };
  const tone = (s: Schedule) => (s.state === "running" ? "running" : s.paused ? "" : s.result === "failed" ? "bad" : s.result === "ended" ? "warn" : "");

  return (
    <>
      <div className="set-head">
        <h1>Scheduled runs</h1>
        <p>Teams that start on their own, such as nightly tests. Each run works on its own branch and waits in Review: nothing is merged.</p>
      </div>
      {rows && (rows.length ? (
        <div className="set-list">
          {rows.map((s) => (
            <div className="set-row" key={s.id}>
              <div className="set-text">
                <b>{s.name}</b>
                <span>{s.project_name} · {s.when} · {s.preset_label}: {s.who}</span>
                <span className={`sched-line ${tone(s)}`}>{s.state === "running" && <i />}{s.line}</span>
                {s.note && <span>{s.note}</span>}
              </div>
              <div className="sched-buttons">{buttons(s)}</div>
            </div>
          ))}
        </div>
      ) : (
        <div className="sched-empty">
          <b>No schedules yet</b>
          <span>For example: every night, run the tests and fix what breaks. Or every Monday, update the dependencies.</span>
        </div>
      ))}
      <div className="set-list note-row">
        <span className="grow">Runs start even when this app is closed. A run missed while the Mac slept starts once when it wakes.</span>
        <button type="button" className={rows && !rows.length ? "btn-small primary" : "btn-small"} onClick={() => setEditing("new")}>New schedule</button>
      </div>
      {error && <p className="set-error">{error}</p>}
      {editing && (
        <Sheet edit={editing === "new" ? null : editing} teams={teams} onClose={() => setEditing(null)}
          onSaved={(r) => { setRows(r); setEditing(null); }} />
      )}
    </>
  );
}
