import { useEffect, useRef, useState } from "react";
import { checkProject, fieldOf, pickFolder, startTeam, teamPresets, type Practices, type Preset, type RoleDraft } from "./bridge";
import HelpMeChoose from "./HelpMeChoose";
import RecommendedSetup from "./RecommendedSetup";
import "./NewTeam.css";

type Clis = { claude: boolean; codex: boolean };
const NAME = /^[a-z0-9-]+$/;
const BAD_NAME = "Names use lower-case letters, digits and dashes, and must differ.";
const AGENTS = [["claude", "Claude Code"], ["codex", "Codex"]] as const;
const shortPath = (p: string) => p.replace(/^\/Users\/[^/]+/, "~");

/** The first agent name that breaks the brain's rule, by index (the brain checks again on Start). */
export function badName(roles: RoleDraft[]): number {
  return roles.findIndex((r, i) => !NAME.test(r.name) || roles.findIndex((x) => x.name === r.name) !== i);
}

/** New team, step 1: project, task, starting team and agents. Step 2 is Recommended setup. */
export default function NewTeam({ clis, onClose }: { clis: Clis; onClose: () => void }) {
  const installed = AGENTS.filter(([id]) => clis[id]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [practices, setPractices] = useState<Practices | null>(null);
  const [project, setProject] = useState("");
  const [projectError, setProjectError] = useState("");
  const [task, setTask] = useState("");
  const [chosen, setChosen] = useState("");
  const [roles, setRoles] = useState<RoleDraft[]>([]);
  const [edited, setEdited] = useState(false);
  const [briefOf, setBriefOf] = useState<number | null>(null);
  const [roleErrors, setRoleErrors] = useState<Record<number, string>>({});
  const [helping, setHelping] = useState(false);
  const [why, setWhy] = useState("");
  const [step, setStep] = useState(1);
  const [practiceOn, setPracticeOn] = useState<Record<string, boolean>>({});
  const [startError, setStartError] = useState("");
  const [pending, setPending] = useState(false);
  const starting = useRef(false); // a second click before React re-renders must not start a second team

  const fit = (rs: RoleDraft[]) =>
    rs.map((r) => (clis[r.agent] || !installed.length ? r : { ...r, agent: installed[0][0] }));

  useEffect(() => {
    void teamPresets().then((p) => {
      const all = p.last ? [...p.presets, p.last] : p.presets;
      setPresets(all);
      setPractices(p.practices);
      setPracticeOn(Object.fromEntries(p.practices.groups.map((g) => [g.id, true])));
      if (all.length) {
        setChosen(all[0].id);
        setRoles(fit(all[0].roles));
      }
    });
  }, []);

  const choose = (p: Preset) => {
    if (edited && !window.confirm("Replace the agents you've edited?")) return;
    setChosen(p.id);
    setRoles(fit(p.roles));
    setEdited(false);
    setWhy("");
    setRoleErrors({});
  };

  const edit = (i: number, change: Partial<RoleDraft>) => {
    const old = roles[i].name;
    const renamed = change.name !== undefined && change.name !== old;
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
    const gone = roles[i].name;
    setRoles(roles.filter((_, j) => j !== i).map((r) => (r.parent === gone ? { ...r, parent: null } : r)));
    setEdited(true);
  };

  const chooseFolder = async () => {
    const p = await pickFolder();
    if (!p) return;
    setProject(p);
    const c = await checkProject(p);
    setProjectError(c.ok ? "" : c.reason ?? "");
  };

  const next = () => {
    const bad = badName(roles);
    if (bad >= 0) return setRoleErrors({ [bad]: BAD_NAME });
    setStep(2);
  };

  const start = async () => {
    if (starting.current) return;
    starting.current = true;
    setPending(true);
    setStartError("");
    try {
      await startTeam({ project, task: task.trim(), roles, practices: practiceOn });
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

  const ready = !!project && !projectError && !!task.trim() && roles.length > 0 && installed.length > 0;

  if (step === 2 && practices)
    return (
      <RecommendedSetup practices={practices} project={project} roleCount={roles.length} value={practiceOn}
        onChange={setPracticeOn} onBack={() => setStep(1)} onStart={start} pending={pending} error={startError} />
    );

  return (
    <div className="newteam">
      <div className="panel">
        <header>
          <h1>New team</h1>
          <p>Step 1 of 2. Pick a project and who works on it. You can change any of this later.</p>
        </header>
        <div className="body">
          <div className="field">
            <div className="label">Project</div>
            <button type="button" className="box folder" onClick={chooseFolder}>
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M2 4.5h4l1.5 1.5H14v6.5H2z" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.4" />
              </svg>
              <span className="grow">{project ? shortPath(project) : "No folder chosen"}</span>
              <span className="muted">Choose…</span>
            </button>
            {projectError && <p className="error">{projectError}</p>}
          </div>

          <label className="field">
            <span className="label">What should the team work on?</span>
            <input className="box" value={task} placeholder="Add rate limiting to the login endpoint"
              onChange={(e) => setTask(e.target.value)} />
          </label>

          <div className="field">
            <div className="label">Where should the agents run?</div>
            <div className="where">
              <label className="box option on">
                <input type="radio" name="where" checked readOnly />
                <span><b>This Mac</b> <span className="good">· recommended</span><small>Nothing to set up</small></span>
              </label>
              <label className="box option off">
                <input type="radio" name="where" disabled />
                <span><b>A remote machine</b><small>Coming later</small></span>
              </label>
            </div>
          </div>

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
            </div>
            {helping && (
              <HelpMeChoose project={project} agent={installed[0]?.[0] === "codex" ? "Codex" : "Claude Code"}
                onCancel={() => setHelping(false)}
                onDone={(s) => {
                  setRoles(fit(s.roles));
                  setWhy(s.why);
                  setChosen("");
                  setEdited(false);
                  setHelping(false);
                }} />
            )}
          </div>

          <div className="field">
            <div className="row">
              <div className="label">Agents</div>
              <div className="muted small">Mix Claude Code and Codex however you like</div>
            </div>
            {why && <p className="muted">{why}</p>}
            {installed.length === 0 ? (
              <p className="error">Install Claude Code or Codex, then reopen New team.</p>
            ) : (
              <div className="agents">
                {roles.map((r, i) => (
                  <div className="agent" key={i}>
                    <div className="line">
                      <input className="name" aria-label="Agent name" value={r.name}
                        onChange={(e) => edit(i, { name: e.target.value })} />
                      <div className="switch">
                        {installed.map(([id, label]) => (
                          <button key={id} type="button" className={r.agent === id ? "on" : ""}
                            aria-pressed={r.agent === id} onClick={() => edit(i, { agent: id })}>{label}</button>
                        ))}
                      </div>
                      <span className="job">{r.job}</span>
                      <button type="button" className="link" onClick={() => setBriefOf(briefOf === i ? null : i)}>Brief</button>
                      <button type="button" className="remove" aria-label="Remove agent" onClick={() => remove(i)}>×</button>
                    </div>
                    {roleErrors[i] && <p className="error">{roleErrors[i]}</p>}
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
          </div>
        </div>
        <footer role="contentinfo">
          <span className="safe">
            <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
              <path d="M8 1.5l5.5 2v4c0 3.5-2.4 6-5.5 7-3.1-1-5.5-3.5-5.5-7v-4z" fill="none" stroke="var(--quiet-working)" strokeWidth="1.6" />
            </svg>
            Safe permissions: can edit this project, can’t push or deploy.
          </span>
          <button type="button" className="quiet" onClick={onClose}>Cancel</button>
          <button type="button" className="primary" disabled={!ready} onClick={next}>Next</button>
        </footer>
      </div>
    </div>
  );
}
