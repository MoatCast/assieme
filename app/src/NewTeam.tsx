import { useEffect, useRef, useState } from "react";
import { checkProject, confirmDiscard, confirmReplace, fieldOf, getSettings, installTools, startTeam, teamPresets, toolsFind, toolsSuggest,
  type AgentKind, type KindInfo, type ModelOptions, type PermissionTable, type Practices, type Preset, type ProjectCheck, type RoleDraft,
  type Settings, type Suggestion, type TeamDraft, type ToolItem } from "./bridge";
import { AgentStep } from "./AgentStep";
import BriefEditor, { guessKind } from "./BriefEditor";
import BringOver from "./BringOver";
import EditAgents, { type Imported } from "./EditAgents";
import InstallSheet from "./InstallSheet";
import { AGENT_KINDS, kindName } from "./kinds";
import { folderMovesOn, nextStep, prevStep, stepsShown, STEP_LABEL, taskAnswer, type Needs, type StepId } from "./newTeamSteps";
import { clearDraft, loadDraft, saveDraft, type Saved } from "./newTeamDraft";
import Offline from "./Offline";
import { defaultLevel } from "./PermissionsEditor";
import ProjectStep from "./ProjectStep";
import { WhereStep } from "./RemoteMachine";
import { checkProjectOn, remoteMachines, startTeamOn } from "./remote";
import StartStep from "./StartStep";
import { useClickGuard, type Clis, type PanelProps } from "./StepPanel";
import TaskStep from "./TaskStep";
import TeamRules from "./TeamRules";
import TeamStep from "./TeamStep";
import ToolPicker from "./ToolPicker";
import { ToolsStep } from "./ToolsStep";
import { useSetupStatus } from "./useSetupStatus";
import "./NewTeam.css";

const NAME = /^[a-z0-9-]+$/;
const BAD_NAME = "Names use lower-case letters, digits and dashes, and must differ.";
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const base = (p: string) => p.split("/").filter(Boolean).pop() ?? p;

/** The first agent name that breaks the brain's rule, by index (the brain checks again on Start). */
export function badName(roles: RoleDraft[]): number {
  return roles.findIndex((r, i) => !NAME.test(r.name) || roles.findIndex((x) => x.name === r.name) !== i);
}

/** New team, one question per step: (Developer tools) → (Agent) → (Where) → Project → Task → Team → Start. This owns
 *  the form's state and the start; each step is a component of its own. A new user's first run is this same flow.
 *  clis: null until the service has said which are installed. */
export default function NewTeam({ clis, draft, retryMs = 3000, recheckMs = 3000, onClose, onStarted }: { clis: Clis | null; draft?: TeamDraft | null; retryMs?: number; recheckMs?: number; onClose: () => void; onStarted?: () => void }) {
  const [machine, setMachine] = useState<string | null>(null); // null: this Mac
  const [there, setThere] = useState<Clis | null>(null); // the agents on that machine, once it is ready
  const have = machine === null ? clis : there;
  const installed = AGENT_KINDS.filter((k) => have?.[k.id]).map((k) => [k.id, k.name] as const);
  const [installing, setInstalling] = useState<AgentKind | null>(null);
  // Which steps show. A step shown once stays for the rest of this setup; Developer tools and Agent come from the
  // setup status, Where from the machines Assieme knows.
  const [needs, setNeeds] = useState<Needs>({ tools: false, agent: !!clis && !Object.values(clis).some(Boolean), remote: false });
  const steps = stepsShown(needs);
  const [at, setAt] = useState<StepId>(steps[0]);
  const guard = useClickGuard(at); // one clock for every step: each step's panel mounts fresh
  const statusSeen = useRef(false);
  const setup = useSetupStatus(installing !== null || at === "tools" || at === "agent" || !statusSeen.current);
  const [notReady, setNotReady] = useState<Record<AgentKind, boolean>>({} as Record<AgentKind, boolean>);
  const [offline, setOffline] = useState(false);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [last, setLast] = useState<Preset | null>(null);
  const [recent, setRecent] = useState<string[]>([]);
  const [practices, setPractices] = useState<Practices | null>(null);
  const [fixes, setFixes] = useState<Settings["fixes"] | null>(null);
  const [project, setProject] = useState("");
  const [projectError, setProjectError] = useState("");
  const [check, setCheck] = useState<ProjectCheck | null>(null);
  const [checkError, setCheckError] = useState("");
  const latest = useRef(0); // only the newest folder check may change what is shown, or move on
  const jump = useRef<StepId | null>(null); // a draft or import: where to go once its folder passes the check
  const [announce, setAnnounce] = useState<string | undefined>(undefined);
  const [task, setTask] = useState("");
  const [chosen, setChosen] = useState("");
  const [roles, setRoles] = useState<RoleDraft[]>([]);
  const [edited, setEdited] = useState(false);
  const [briefOf, setBriefOf] = useState<number | null>(null);
  const [models, setModels] = useState<ModelOptions | null>(null);
  const [kinds, setKinds] = useState<Record<string, KindInfo>>({});
  const [perms, setPerms] = useState<PermissionTable | null>(null);
  const [roleErrors, setRoleErrors] = useState<Record<number, string>>({});
  const [why, setWhy] = useState("");
  const [imported, setImported] = useState<Imported | null>(null);
  const [importing, setImporting] = useState(false);
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [practiceOn, setPracticeOn] = useState<Record<string, boolean>>({});
  const [startError, setStartError] = useState("");
  const [pending, setPending] = useState(false);
  const starting = useRef(false); // a second click before React re-renders must not start a second team
  const [kept] = useState<Saved | null>(() => (draft ? null : loadDraft())); // a setup left when the window closed
  const [resumed, setResumed] = useState(false);

  // Step 32: what each agent kind has installed on this Mac, and which agent's picker is open. Each agent without a
  // choice of its own gets its role's suggestion once its kind's list is in.
  // Lists are read per kind and per where the agent works: a read-only agent shares the project folder, where some
  // kinds can't be limited, and its list says so.
  const [tools, setTools] = useState<Record<string, ToolItem[]>>({});  // "kind|own" → items
  const [toolsOf, setToolsOf] = useState<number | null>(null);
  const [suggestedTools, setSuggested] = useState<Record<string, string[]>>({});  // suggestKey → its role's suggestion
  const inFlight = useRef(new Set<string>());
  const toolsHere = machine === null && project ? project : "";
  const here = useRef(toolsHere);
  here.current = toolsHere;
  const own = (r: RoleDraft) => r.checkout === "branch" && !r.worktree;
  const listKey = (r: RoleDraft) => `${r.agent}|${own(r)}`;
  const suggestKey = (r: RoleDraft) => `${r.name}|${r.agent}|${r.kind ?? guessKind(r.name)}|${own(r)}`;
  const listsUsed = [...new Set(roles.map(listKey))].join(",");
  useEffect(() => { setTools({}); setSuggested({}); inFlight.current.clear(); }, [toolsHere]);
  useEffect(() => {
    if (!toolsHere) return;
    for (const key of listsUsed.split(",").filter(Boolean)) {
      if (tools[key] || inFlight.current.has(`find:${key}`)) continue;
      inFlight.current.add(`find:${key}`);
      const [kind, mine] = key.split("|");
      Promise.resolve(toolsFind(kind as AgentKind, toolsHere, mine === "true")).then((got) => {
        if (here.current === toolsHere) setTools((t) => ({ ...t, [key]: got?.items ?? [] }));  // not a stale folder's
      }, () => {}).finally(() => inFlight.current.delete(`find:${key}`));
    }
  }, [toolsHere, listsUsed, tools]);
  useEffect(() => {
    for (const r of roles) {
      const key = suggestKey(r);
      if (!tools[listKey(r)]?.length || (r.tools_off !== undefined && key in suggestedTools) || inFlight.current.has(key)) continue;
      inFlight.current.add(key);
      Promise.resolve(toolsSuggest(r.kind ?? guessKind(r.name), r.agent, toolsHere, own(r))).then((ids) => {
        if (here.current !== toolsHere) return;
        setSuggested((s) => ({ ...s, [key]: ids ?? [] }));
        setRoles((rs) => rs.map((x) => (suggestKey(x) === key && x.tools_off === undefined
          ? { ...x, tools_off: ids ?? [], tools_new: true } : x)));
      }, () => {}).finally(() => inFlight.current.delete(key));
    }
  }, [roles, tools, toolsHere, suggestedTools]); // eslint-disable-line react-hooks/exhaustive-deps

  // Every agent gets a role and a permission level here, shown in its Brief before Start.
  const fit = (rs: RoleDraft[]) =>
    rs.map((r) => ({ ...(have?.[r.agent] || !installed.length ? r : { ...r, agent: installed[0][0] }),
      kind: r.kind !== undefined ? r.kind : guessKind(r.name), level: r.level ?? defaultLevel(r) }));

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const loaded = (p: Awaited<ReturnType<typeof teamPresets>>) => {
      if (stopped) return;
      setOffline(false);
      const all = p.last ? [p.last, ...p.presets] : p.presets;
      setPresets(p.presets);
      setLast(p.last);
      setRecent(p.recent ?? []);
      setPractices(p.practices);
      setModels(p.models);
      setKinds(p.kinds ?? {});
      setPerms(p.permissions ?? null);
      setPracticeOn({ ...(draft?.practices ?? p.practice_defaults ?? {}) }); // groups and rules are on unless false
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

  // Another machine has its own agents: each role uses one that is there.
  useEffect(() => { if (have) setRoles((rs) => fit(rs)); }, [have?.claude, have?.codex]); // eslint-disable-line react-hooks/exhaustive-deps

  /** An assistant's draft, or an import: the whole form, which the person checks at Start. */
  function apply(d: TeamDraft) {
    setProject(d.project);
    setProjectError("");
    // A draft's folder needs the same check as a chosen one; it opens at Start once that passes.
    if (d.project) { jump.current = "start"; void verify(d.project); }
    else { setCheck(null); go("project"); }
    setTask((t) => d.task || t); // an import with no task keeps the one typed here
    setRoles(fit(d.roles));
    setChosen("");
    setEdited(true); // a preset's click must ask before replacing it
    setWhy(d.why);
    setRoleErrors({});
    setImported(d.from === "herdr" || d.from === "bringover"
      ? { notes: d.notes ?? [], running: d.running, from: d.from, tool: d.tool, label: d.label, scripts: d.scripts,
          allow_scripts: d.allow_scripts, cards: d.cards }
      : null);
  }

  /** False when the person kept the agents they had edited. */
  const choose = async (p: Preset) => {
    if (edited && !(await confirmReplace())) return false;
    setImported(null);
    setChosen(p.id);
    setRoles(fit(p.roles));
    setEdited(false);
    setWhy("");
    setRoleErrors({});
    return true;
  };

  const edit = (i: number, change: Partial<RoleDraft>) => {
    const old = roles[i].name;
    const renamed = change.name !== undefined && change.name !== old;
    // Model names belong to one CLI: switching Claude Code / Codex goes back to that agent's defaults.
    // Skills and servers too: the new kind has its own list, and gets its role's suggestion again.
    if (change.agent && change.agent !== roles[i].agent) change = { ...change, model: null, effort: null, tools_off: undefined, tools_new: undefined };
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


  // The setup status says which first-run steps show; only before a folder is chosen can they still be added.
  useEffect(() => {
    const s = setup.status;
    if (!s) return;
    const first = !statusSeen.current;
    statusSeen.current = true;
    if (!first || project) return;
    const signedIn = AGENT_KINDS.some((k) => s.agents[k.id]?.installed && s.agents[k.id]?.signed_in);
    setNeeds((n) => ({ ...n, tools: n.tools || !s.git, agent: n.agent || !signedIn }));
  }, [setup.status]); // eslint-disable-line react-hooks/exhaustive-deps
  // Machines Assieme is set up on add Where, before Project, while no folder is chosen yet.
  useEffect(() => {
    void Promise.resolve(remoteMachines()).then((ms) => {
      if (ms?.length) setNeeds((n) => (n.remote ? n : { ...n, remote: true }));
    }, () => {});
  }, []);
  // A step added in front of where the person is, before they chose a folder, becomes the first step.
  const firstShown = steps[0];
  const wasFirst = useRef(firstShown);
  useEffect(() => {
    if (!project && at === wasFirst.current && !jump.current) setAt(firstShown);
    wasFirst.current = firstShown;
  }, [firstShown]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { void Promise.resolve(getSettings()).then((s) => setFixes(s?.fixes ?? null), () => {}); }, []);

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

  /** keep: leave the shown result in place until the new one arrives (after a save). Returns the check, or null when a
   *  newer check replaced it or it failed. */
  const verify = async (p: string, keep = false, on = machine): Promise<ProjectCheck | null> => {
    const id = ++latest.current;
    setCheckError("");
    if (!keep) setCheck(null);
    try {
      const c = await (on ? checkProjectOn(p, on) : checkProject(p));
      if (id !== latest.current) return null;
      setCheck(c);
      if (folderMovesOn(c)) setAnnounce(`${base(p)} checked`);
      // A draft or an import goes to its step once its folder passes; one that fails opens at Project.
      if (jump.current) {
        setAt(folderMovesOn(c) ? jump.current : "project");
        jump.current = null;
      }
      return c;
    } catch (e) {
      if (id === latest.current) {
        setCheckError(message(e));
        if (jump.current) { setAt("project"); jump.current = null; }
      }
      return null;
    }
  };
  /** The only automatic move off Project: a fresh choice whose check is still the newest and needs nothing. */
  const use = async (p: string) => {
    setProject(p);
    setProjectError("");
    const c = await verify(p);
    if (c && folderMovesOn(c)) go(nextStep(steps, "project"));
  };
  const installDevTools = () => {
    setCheckError("");
    installTools().catch((e) => setCheckError(message(e)));
  };

  // Closing the window keeps the draft; Start and Discard clear it.
  useEffect(() => {
    if (project) saveDraft({ machine, project, task, roles, chosen, practices: practiceOn, at });
  }, [machine, project, task, roles, chosen, practiceOn, at]);
  const resume = () => {
    if (!kept) return;
    setResumed(true);
    setMachine(kept.machine);
    setTask(kept.task);
    setRoles(kept.roles);
    setChosen(kept.chosen);
    setEdited(!kept.chosen);
    setPracticeOn(kept.practices);
    setProject(kept.project);
    if (kept.machine) {
      // Its agents are known only once that machine answers: go through Where, which checks it.
      setNeeds((n) => ({ ...n, remote: true }));
      go("where");
    } else jump.current = steps.includes(kept.at) && kept.at !== "project" ? kept.at : "project";
    void verify(kept.project, false, kept.machine);
  };

  const go = (s: StepId) => {
    setAt(s);
    setAnnounce(undefined);
  };
  const next = () => go(nextStep(steps, at));
  const back = () => { const p = prevStep(steps, at); if (p) go(p); };

  const cancel = async () => {
    if (project) {
      if (!(await confirmDiscard())) return;
      clearDraft(); // only a discarded setup loses its draft; one kept from last time stays on offer
    }
    onClose();
  };
  // Escape closes the innermost sheet (each modal handles its own), then Team rules, then means Cancel. Never Back.
  const escape = useRef(() => {});
  escape.current = () => {
    if (rulesOpen) setRulesOpen(false);
    else void cancel();
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || document.querySelector("[aria-modal='true']")) return;
      escape.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const start = async () => {
    if (starting.current) return;
    const bad = badName(roles);
    if (bad >= 0) {
      setRoleErrors({ [bad]: BAD_NAME });
      setAgentsOpen(true);
      return;
    }
    starting.current = true;
    setPending(true);
    setStartError("");
    try {
      const brought = imported?.from === "bringover" ? {
        scripts: imported.scripts ?? {}, allow_scripts: imported.allow_scripts ?? [], cards: imported.cards ?? [],
        origin: { tool: imported.tool ?? "", label: imported.label ?? "" },
      } : {};
      const form = { project, task: task.trim(), roles, practices: practiceOn, ...brought };
      await (machine ? startTeamOn(form, machine) : startTeam(form));
      clearDraft();
      onStarted?.();
      onClose();
    } catch (e) {
      const { field, message } = fieldOf(e);
      if (field === "project") {
        setProjectError(message);
        go("project");
      } else if (field.startsWith("role:")) {
        setRoleErrors({ [Number(field.slice(5))]: message });
        setAgentsOpen(true);
      } else setStartError(message);
    } finally {
      starting.current = false;
      setPending(false);
    }
  };

  const pickTeam = async (p: Preset) => {
    if (await choose(p)) next();
  };
  const suggested = (s: Suggestion) => {
    setRoles(fit(s.roles));
    setWhy(s.why);
    setImported(null);
    setChosen("");
    setEdited(false);
    setRoleErrors({});
    next();
  };

  const signedInKind = AGENT_KINDS.find((k) => setup.status?.agents[k.id]?.signed_in);
  const answers: Partial<Record<StepId, string>> = {
    tools: STEP_LABEL.tools, agent: signedInKind?.name ?? STEP_LABEL.agent, where: machine ?? "This Mac",
    project: project ? base(project) : "", task: taskAnswer(task),
    team: imported ? "Brought over" : chosen ? presets.find((p) => p.id === chosen)?.label ?? last?.label ?? "" : why ? "Suggested team" : "",
  };
  const panel: PanelProps = { steps, at, answers, announce, onGo: go, guard };
  const containerWaiting = roles.some((r) => r.container && notReady[r.agent]);

  let body: React.ReactNode;
  if (offline) body = <div className="newteam"><div className="panel"><div className="body"><Offline afterMs={0} /></div></div></div>;
  else if (at === "tools") body = <ToolsStep panel={panel} status={setup.status} onNext={next} />;
  else if (at === "agent") body = <AgentStep panel={panel} status={setup.status} onNext={next} onBack={back} />;
  else if (at === "where") body = (
    <WhereStep panel={panel} machine={machine} there={there} onAgents={setThere} onNext={next} onCancel={() => void cancel()}
      onMachine={(m) => {
        if (m === machine) return; // the same machine again keeps the folder
        latest.current++; // a check still running for the old machine can't land
        setMachine(m); setProject(""); setCheck(null); setProjectError(""); setCheckError("");
      }} />
  );
  else if (at === "project") body = (
    <ProjectStep panel={panel} machine={machine} recent={machine ? [] : recent} project={project} check={check}
      error={projectError || checkError} onUse={(p) => void use(p)}
      resume={kept && !resumed && !project ? { name: base(kept.project), act: resume } : null} onRecheck={() => void verify(project, true)}
      onInstallTools={installDevTools} onContinueAnyway={() => go(nextStep(steps, "project"))} onCancel={() => void cancel()} />
  );
  else if (at === "task") body = (
    <TaskStep panel={panel} task={task} onTask={setTask} onNext={next} onBack={back} onSkip={() => { setTask(""); next(); }} />
  );
  else if (at === "team") body = (
    <TeamStep panel={panel} presets={presets} last={last} installed={installed.map(([k]) => k)} task={task} project={project}
      machine={machine} onPick={(p) => void pickTeam(p)} onSuggested={suggested} onBringOver={() => setImporting(true)}
      onInstall={setInstalling} onBack={back} />
  );
  else if (rulesOpen && practices) body = (
    <TeamRules panel={panel} practices={practices} project={project} value={practiceOn} onChange={setPracticeOn}
      onDone={() => setRulesOpen(false)} />
  );
  else if (practices) body = (
    <StartStep panel={panel} practices={practices} value={practiceOn} roles={roles} machine={machine} fixes={fixes}
      onRules={() => setRulesOpen(true)} onEditAgents={() => setAgentsOpen(true)}
      onBigger={(key, on) => setPracticeOn({ ...practiceOn, [key]: on })} onBack={back} onStart={() => void start()}
      pending={pending || containerWaiting} error={startError || (containerWaiting ? "Get the container ready to continue." : "")}
      notice={imported?.running && (
        <div className="still-running" role="status">
          <strong>{imported.from === "bringover" ? imported.running : `${imported.running} is still running in herdr`}</strong>
          <span>Stop it there before you start this team, or two teams will work on the same project.</span>
        </div>
      )} />
  );

  return (
    <>
    {body}
    {agentsOpen && (
      <EditAgents project={project} roles={roles} installed={have ?? {}} models={models} tools={tools} listKey={listKey}
        roleErrors={roleErrors} why={why} picked={answers.team ?? ""} imported={imported}
        onEdit={edit} onAdd={add} onRemove={remove} onBrief={setBriefOf} onTools={setToolsOf} onInstall={setInstalling}
        onNotReady={(agent, no) => setNotReady((n) => ({ ...n, [agent]: no }))} onDone={() => setAgentsOpen(false)} />
    )}
    {briefOf !== null && roles[briefOf] && perms && (
      <BriefEditor role={roles[briefOf]} roles={roles} project={project} installed={installed} kinds={kinds} table={perms}
        asker={installed[0]?.[0] === "codex" ? "Codex" : "Claude Code"}
        onCancel={() => setBriefOf(null)} onSave={(c) => { edit(briefOf, c); setBriefOf(null); }} />
    )}
    {toolsOf !== null && roles[toolsOf] && tools[listKey(roles[toolsOf])] && (
      <ToolPicker items={tools[listKey(roles[toolsOf])]!} roleName={roles[toolsOf].name}
        roleKind={roles[toolsOf].kind ?? guessKind(roles[toolsOf].name)} kindName={kindName(roles[toolsOf].agent)}
        value={{ tools_off: roles[toolsOf].tools_off ?? [], tools_new: roles[toolsOf].tools_new ?? true }}
        suggested={suggestedTools[suggestKey(roles[toolsOf])] ?? []}
        onClose={() => setToolsOf(null)} onDone={(v) => { edit(toolsOf, v); setToolsOf(null); }} />
    )}
    {installing && <InstallSheet agent={installing} start="install" status={setup.status} onClose={() => setInstalling(null)} />}
    {importing && (
      <BringOver onClose={() => setImporting(false)} onImported={async (d) => {
        setImporting(false);
        if (!edited || await confirmReplace()) apply(d);
      }} />
    )}
    </>
  );
}
