import { useEffect, useRef, useState } from "react";
import AgentMenu, { ContainerLine } from "./AgentMenu";
import { checkProject, confirmReplace, fieldOf, folderCreate, installTools, pickFolder, pickNewFolder, startTeam, teamPresets, type AgentKind, type ModelOptions, type Practices, type Preset, type ProjectCheck, type RoleDraft, type TeamDraft } from "./bridge";
import { AGENT_KINDS } from "./kinds";
import FolderSheet from "./FolderSheet";
import HelpMeChoose from "./HelpMeChoose";
import HerdrImport from "./HerdrImport";
import InstallSheet from "./InstallSheet";
import ModelMenu, { choiceLabel } from "./ModelMenu";
import Offline from "./Offline";
import RecommendedSetup, { footerNote } from "./RecommendedSetup";
import { useSetupStatus } from "./NewUserChecklist";
import "./NewTeam.css";

type Clis = Partial<Record<AgentKind, boolean>>;
const NAME = /^[a-z0-9-]+$/;
const BAD_NAME = "Names use lower-case letters, digits and dashes, and must differ.";
const shortPath = (p: string) => p.replace(/^\/Users\/[^/]+/, "~");
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const changes = (n: number) =>
  `${n === 1 ? "1 file has" : `${n} files have`} changes that aren’t in a saved version (uncommitted), so agents won’t see them.`;

/** The first agent name that breaks the brain's rule, by index (the brain checks again on Start). */
export function badName(roles: RoleDraft[]): number {
  return roles.findIndex((r, i) => !NAME.test(r.name) || roles.findIndex((x) => x.name === r.name) !== i);
}

/** New team, step 1: project, task, starting team and agents. Step 2 is Recommended setup. */
/** clis: null until the service has said which are installed. */
export default function NewTeam({ clis, draft, retryMs = 3000, recheckMs = 3000, onClose, onStarted }: { clis: Clis | null; draft?: TeamDraft | null; retryMs?: number; recheckMs?: number; onClose: () => void; onStarted?: () => void }) {
  const installed = AGENT_KINDS.filter((k) => clis?.[k.id]).map((k) => [k.id, k.name] as const);
  const [installing, setInstalling] = useState<AgentKind | null>(null);
  const setup = useSetupStatus(installing !== null);
  const [notReady, setNotReady] = useState<Record<AgentKind, boolean>>({} as Record<AgentKind, boolean>);
  const [offline, setOffline] = useState(false);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [practices, setPractices] = useState<Practices | null>(null);
  const [project, setProject] = useState("");
  const [projectError, setProjectError] = useState("");
  const [check, setCheck] = useState<ProjectCheck | null>(null);
  const [folderSheet, setFolderSheet] = useState(false);
  const [checkError, setCheckError] = useState("");
  const latest = useRef(0); // only the newest folder check may change what is shown
  const [noteError, setNoteError] = useState(""); // what went wrong with New folder or the installer
  const [task, setTask] = useState("");
  const [chosen, setChosen] = useState("");
  const [roles, setRoles] = useState<RoleDraft[]>([]);
  const [edited, setEdited] = useState(false);
  const [briefOf, setBriefOf] = useState<number | null>(null);
  const [modelOf, setModelOf] = useState<number | null>(null);
  const [models, setModels] = useState<ModelOptions | null>(null);
  const [roleErrors, setRoleErrors] = useState<Record<number, string>>({});
  const [helping, setHelping] = useState(false);
  const [why, setWhy] = useState("");
  // An import from herdr: what didn't carry over, and the herdr team still running (if it is). A preset replaces it.
  const [imported, setImported] = useState<Pick<TeamDraft, "notes" | "running"> | null>(null);
  const [importing, setImporting] = useState(false);
  const [step, setStep] = useState(1);
  const [practiceOn, setPracticeOn] = useState<Record<string, boolean>>({});
  const [startError, setStartError] = useState("");
  const [pending, setPending] = useState(false);
  const starting = useRef(false); // a second click before React re-renders must not start a second team

  const fit = (rs: RoleDraft[]) =>
    rs.map((r) => (clis?.[r.agent] || !installed.length ? r : { ...r, agent: installed[0][0] }));

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const loaded = (p: Awaited<ReturnType<typeof teamPresets>>) => {
      if (stopped) return;
      setOffline(false);
      const all = p.last ? [...p.presets, p.last] : p.presets;
      setPresets(all);
      setPractices(p.practices);
      setModels(p.models);
      setPracticeOn(Object.fromEntries(p.practices.groups.map((g) => [g.id, (draft?.practices ?? p.practice_defaults)?.[g.id] !== false])));
      if (draft) apply(draft);
      else if (all.length) {
        setChosen(all[0].id);
        setRoles(fit(all[0].roles));
      }
    };
    const load = () => teamPresets().then(loaded, () => {
      if (stopped) return;
      setOffline(true);
      timer = setTimeout(load, retryMs);
    });
    void load();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, []);

  /** An assistant's draft, or an import from herdr: the whole form, which the person checks before Start. */
  function apply(d: TeamDraft) {
    setProject(d.project);
    setProjectError("");
    if (d.project) void verify(d.project); // a draft's folder needs the same setup click as a chosen one
    else setCheck(null);
    setTask(d.task);
    setRoles(fit(d.roles));
    setChosen("");
    setEdited(true); // a preset's click must ask before replacing it
    setWhy(d.why);
    setRoleErrors({});
    setImported(d.from === "herdr" ? { notes: d.notes ?? [], running: d.running } : null);
  }

  const choose = async (p: Preset) => {
    if (edited && !(await confirmReplace())) return;
    setImported(null);
    setChosen(p.id);
    setRoles(fit(p.roles));
    setEdited(false);
    setWhy("");
    setRoleErrors({});
  };

  const edit = (i: number, change: Partial<RoleDraft>) => {
    const old = roles[i].name;
    const renamed = change.name !== undefined && change.name !== old;
    // Model names belong to one CLI: switching Claude Code / Codex goes back to that agent's defaults.
    if (change.agent && change.agent !== roles[i].agent) change = { ...change, model: null, effort: null };
    // Agents that report to a renamed one follow the new name: the form has no other way to fix their parent.
    setRoles(roles.map((r, j) => (j === i ? { ...r, ...change } : renamed && r.parent === old ? { ...r, parent: change.name! } : r)));
    setEdited(true);
    setRoleErrors({});
  };

  const add = () => {
    let n = 2;
    while (roles.some((r) => r.name === `builder-${n}`)) n++;
    const template = presets.flatMap((p) => p.roles).find((r) => r.name.startsWith("builder"));
    setRoles([...roles, {
      name: `builder-${n}`, agent: installed[0]?.[0] ?? "claude", job: template?.job ?? "Builds one piece at a time",
      checkout: "branch", parent: null, brief: template?.brief ?? "",
    }]);
    setEdited(true);
  };

  const remove = (i: number) => {
    setModelOf(null);
    const gone = roles[i].name;
    setRoles(roles.filter((_, j) => j !== i).map((r) => (r.parent === gone ? { ...r, parent: null } : r)));
    setEdited(true);
  };

  /** keep: leave the shown result in place until the new one arrives (after a save). */
  const verify = async (p: string, keep = false) => {
    const id = ++latest.current;
    setCheckError("");
    if (!keep) setCheck(null);
    try {
      const c = await checkProject(p);
      if (id === latest.current) setCheck(c);
    } catch (e) {
      if (id === latest.current) setCheckError(message(e));
    }
  };
  const use = async (p: string) => {
    setProject(p);
    setProjectError("");
    await verify(p);
  };
  const chooseFolder = async () => {
    setNoteError("");
    try {
      const p = await pickFolder();
      if (p) await use(p);
    } catch (e) {
      setNoteError(message(e));
    }
  };
  const newFolder = async () => {
    setNoteError("");
    try {
      const p = await pickNewFolder();
      if (!p) return;
      await folderCreate(p);
      await use(p);
    } catch (e) {
      setNoteError(message(e));
    }
  };
  const installDevTools = () => {
    setNoteError("");
    installTools().catch((e) => setNoteError(message(e)));
  };
  const saved = () => {
    setFolderSheet(false);
    return verify(project, true);
  };

  // Developer tools install in Apple's own window: ask again until they are there.
  useEffect(() => {
    if (!check?.tools || !project) return;
    let stopped = false;
    let asking = false; // skip a tick while the last question is still out
    const timer = setInterval(async () => {
      if (asking) return;
      asking = true;
      const id = latest.current;
      try {
        const c = await checkProject(project);
        if (!stopped && id === latest.current) setCheck(c);
      } catch {
        /* the next tick asks again */
      }
      asking = false;
    }, recheckMs);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [check?.tools, project, recheckMs]);

  // Names are checked here and again by the brain on Start.
  const named = () => {
    const bad = badName(roles);
    if (bad >= 0) setRoleErrors({ [bad]: BAD_NAME });
    return bad < 0;
  };
  const next = () => {
    if (!named()) return;
    setStartError("");
    setStep(2);
  };
  const quick = () => {
    if (named()) void start();
  };

  const start = async () => {
    if (starting.current) return;
    starting.current = true;
    setPending(true);
    setStartError("");
    try {
      await startTeam({ project, task: task.trim(), roles, practices: practiceOn });
      onStarted?.();
      onClose();
    } catch (e) {
      const { field, message } = fieldOf(e);
      if (field === "project") {
        setProjectError(message);
        setStep(1);
      } else if (field.startsWith("role:")) {
        setRoleErrors({ [Number(field.slice(5))]: message });
        setStep(1);
      } else setStartError(message);
    } finally {
      starting.current = false;
      setPending(false);
    }
  };

  // The first thing still missing, or nothing once Next can be pressed.
  const missing = offline ? "Waiting for Assieme’s service."
    : !project ? "Choose a project folder to continue."
    : !check ? (checkError ? "Couldn’t check this folder. Choose it again to continue." : "Checking this folder…")
    : check.tools ? "Install developer tools to continue."
    : check.setup ? "Set up this folder to continue."
    : projectError || !check.ok ? "Choose a different folder to continue."
    : !task.trim() ? "Say what the team should work on to continue."
    : clis && !installed.length ? "Install Claude Code or Codex to continue."
    : installed.length && !roles.length ? "Add an agent to continue."
    : roles.some((r) => r.container && notReady[r.agent]) ? "Get the container ready to continue."
    : "";
  const ready = !missing && installed.length > 0;

  if (step === 2 && practices)
    return (
      <RecommendedSetup practices={practices} roleCount={roles.length} value={practiceOn}
        onChange={setPracticeOn} onBack={() => setStep(1)} onStart={start} pending={pending} error={startError} />
    );

  const warn = !!check?.ok && !!check.uncommitted;
  const button = check?.tools ? { label: "Install developer tools", act: installDevTools }
    : check?.setup ? { label: "Set up this folder", act: () => setFolderSheet(true) }
    : warn ? { label: "Save them first", act: () => setFolderSheet(true) } : null;
  const plainError = (check && !check.ok && !check.tools && !check.setup && !check.too_large && check.reason) || projectError;

  return (
    <>
    <div className="newteam">
      <div className="panel">
        <header>
          <h1>New team</h1>
          <p>Step 1 of 2. Pick a project and who works on it. You can change any of this later.</p>
        </header>
        <div className="body">
          {offline && <Offline afterMs={0} />}
          {imported?.running && (
            <div className="still-running" role="status">
              <strong>{imported.running} is still running in herdr</strong>
              <span>Stop it there before you start this team, or two teams will work on the same project.</span>
            </div>
          )}
          <div className="field">
            <div className="label">Project</div>
            <div className="box folder">
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M2 4.5h4l1.5 1.5H14v6.5H2z" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.4" />
              </svg>
              <span className="grow">{project ? shortPath(project) : "No folder chosen"}</span>
              <button type="button" className="pick" onClick={newFolder}>New folder…</button>
              <span className="muted" aria-hidden="true">·</span>
              <button type="button" className="pick" onClick={chooseFolder}>Choose…</button>
            </div>
            {plainError && <p className="error">{plainError}</p>}
            {checkError && <p className="error">{checkError}</p>}
            {noteError && <p className="error">{noteError}</p>}
            {check && (button || check.too_large) && (
              <div className="folder-note">
                <span className={warn ? "warn" : ""}>{warn ? changes(check.uncommitted!) : check.reason}</span>
                {button && <button type="button" className="action" onClick={button.act}>{button.label}</button>}
              </div>
            )}
          </div>

          <label className="field">
            <span className="label">What should the team work on?</span>
            <input className="box" value={task} placeholder="Add rate limiting to the login endpoint"
              onChange={(e) => setTask(e.target.value)} />
          </label>

          <div className="field">
            <div className="row">
              <div className="label">Start from</div>
              <button type="button" className="link" disabled={!project || !installed.length} onClick={() => setHelping(true)}>
                <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true">
                  <path d="M8 1.5l1.6 4.1 4.4.3-3.4 2.8 1.1 4.3L8 10.6 4.3 13l1.1-4.3L2 5.9l4.4-.3z" fill="none"
                    stroke="var(--quiet-ink)" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
                Help me choose
              </button>
            </div>
            <div className="chips">
              {presets.map((p) => (
                <button key={p.id} type="button" className={chosen === p.id ? "chip on" : "chip"}
                  aria-pressed={chosen === p.id} onClick={() => choose(p)}>{p.label}</button>
              ))}
              <button type="button" className={imported ? "chip on" : "chip herdr"} aria-pressed={!!imported}
                onClick={() => setImporting(true)}>From herdr…</button>
            </div>
            {helping && (
              <HelpMeChoose project={project} agent={installed[0]?.[0] === "codex" ? "Codex" : "Claude Code"}
                onCancel={() => setHelping(false)}
                onDone={(s) => {
                  setRoles(fit(s.roles));
                  setWhy(s.why);
                  setImported(null);
                  setChosen("");
                  setEdited(false);
                  setHelping(false);
                }} />
            )}
          </div>

          <div className="field">
            <div className="row">
              <div className="label">Agents</div>
              {installed.length > 1 && <div className="muted small">Mix any agents on this Mac. The box icon means it runs in a container.</div>}
            </div>
            {why && <p className="muted">{why}</p>}
            {installed.length === 0 ? (
              offline ? null : clis ? (
                <p className="error">Install Claude Code or Codex, then reopen New team.</p>
              ) : <p className="muted">Checking which agents are installed…</p>
            ) : (
              <div className="agents">
                {roles.map((r, i) => (
                  <div className="agent" key={i}>
                    <div className="line">
                      <input className="name" aria-label="Agent name" value={r.name}
                        onChange={(e) => edit(i, { name: e.target.value })} />
                      <AgentMenu look="pill" value={r.agent} container={!!r.container} installed={clis ?? {}}
                        team={project ? project.split("/").filter(Boolean).pop() : undefined}
                        onChange={(c) => edit(i, c)} onInstall={setInstalling} />
                      <span className="job">{r.job}</span>
                      {models && (
                        <button type="button" className="choice" aria-label={`Model and effort for ${r.name}`}
                          aria-expanded={modelOf === i} onClick={() => setModelOf(modelOf === i ? null : i)}>
                          {choiceLabel(r, models)}
                        </button>
                      )}
                      <button type="button" className="link" onClick={() => setBriefOf(briefOf === i ? null : i)}>Brief</button>
                      <button type="button" className="remove" aria-label="Remove agent" onClick={() => remove(i)}>×</button>
                    </div>
                    {roleErrors[i] && <p className="error">{roleErrors[i]}</p>}
                    {r.container && roles.findIndex((x) => x.container && x.agent === r.agent) === i && (
                      <div className="container-row">
                        <ContainerLine agent={r.agent} onReady={(ok) => setNotReady((n) => ({ ...n, [r.agent]: !ok }))} />
                      </div>
                    )}
                    {modelOf === i && models && (
                      <ModelMenu role={r} options={models} onChange={(c) => edit(i, c)} />
                    )}
                    {briefOf === i && (
                      <div className="brief">
                        <textarea aria-label={`Brief for ${r.name}`} value={r.brief} rows={8}
                          onChange={(e) => edit(i, { brief: e.target.value })} />
                        <button type="button" className="link" onClick={() => setBriefOf(null)}>Done</button>
                      </div>
                    )}
                  </div>
                ))}
                <button type="button" className="add" onClick={add}>
                  <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                    <path d="M6 1.5v9M1.5 6h9" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.5" strokeLinecap="round" />
                  </svg>
                  Add an agent
                </button>
              </div>
            )}
            {!!imported?.notes?.length && (
              <div className="carried">
                <div className="carried-title">Imported from herdr. What didn’t carry over</div>
                {imported.notes.map((n) => (
                  <div className="carried-note" key={n.text}>
                    <p>{n.text}</p>
                    {n.agents.length > 0 && <p className="carried-agents">{n.agents.join(", ")}</p>}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        <footer role="contentinfo">
          {missing ? <span className="safe" role="status">{missing}</span> : (
            <span className="safe">
              <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M8 1.5l5.5 2v4c0 3.5-2.4 6-5.5 7-3.1-1-5.5-3.5-5.5-7v-4z" fill="none" stroke="var(--quiet-working)" strokeWidth="1.6" />
              </svg>
              {footerNote(practiceOn.safety !== false)}
            </span>
          )}
          {startError && <span className="error">{startError}</span>}
          <button type="button" className="quiet" onClick={onClose}>Cancel</button>
          <button type="button" className="outline" disabled={!ready || pending} onClick={next}>Customise</button>
          <button type="button" className="primary" disabled={!ready || pending} onClick={quick}>{imported ? "Start team" : "Start with recommended"}</button>
        </footer>
      </div>
    </div>
    {folderSheet && <FolderSheet project={project} onClose={() => setFolderSheet(false)} onSaved={saved} />}
    {installing && <InstallSheet agent={installing} start="install" status={setup.status} onClose={() => setInstalling(null)} />}
    {importing && (
      <HerdrImport onClose={() => setImporting(false)} onImported={async (d) => {
        setImporting(false);
        if (!edited || await confirmReplace()) apply(d);
      }} />
    )}
    </>
  );
}
