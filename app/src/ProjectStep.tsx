import { useEffect, useRef, useState } from "react";
import { folderCreate, pickFolder, pickNewFolder, type ProjectCheck } from "./bridge";
import FolderSetupBlock, { setupAction, useFolderSetup } from "./FolderSetup";
import { folderMovesOn } from "./newTeamSteps";
import { RemoteProject } from "./RemoteMachine";
import StepPanel, { type PanelProps } from "./StepPanel";

export const shortPath = (p: string) => p.replace(/^\/Users\/[^/]+/, "~");
const base = (p: string) => p.split("/").filter(Boolean).pop() ?? p;
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const files = (n: number) => (n === 1 ? "1 file" : `${n} files`);

function FolderIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M1.5 4.5a1 1 0 0 1 1-1h3.6l1.4 1.5h6a1 1 0 0 1 1 1v6.5a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1z" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.4" />
    </svg>
  );
}

/** New team, Project: which folder. It hands a chosen folder to NewTeam, which checks it and moves on only for a
 *  folder that needs nothing; any problem shows here as one block with one action. Paper "v3 — New team: Project". */
export default function ProjectStep({ panel, machine, recent, project, check, error, resume, onUse, onRecheck, onInstallTools,
  onContinueAnyway, onCancel }: {
  panel: PanelProps; machine: string | null; recent: string[]; project: string; check: ProjectCheck | null;
  error: string; // a check error, an installer error or a Start error for the project
  resume?: { name: string; act: () => void } | null; // a draft kept from a closed window
  onUse: (path: string) => void; onRecheck: () => void; onInstallTools: () => void; onContinueAnyway: () => void; onCancel: () => void;
}) {
  const [note, setNote] = useState(""); // what went wrong with a picker or New folder
  const [typed, setTyped] = useState(project);
  const setup = useFolderSetup(project, machine || undefined, !!check?.setup);
  const primary = useRef<HTMLButtonElement>(null);

  const choose = async () => {
    setNote("");
    try {
      const p = await pickFolder();
      if (p) onUse(p);
    } catch (e) {
      setNote(message(e));
    }
  };
  const newFolder = async () => {
    setNote("");
    try {
      const p = await pickNewFolder();
      if (!p) return;
      await folderCreate(p);
      onUse(p);
    } catch (e) {
      setNote(message(e));
    }
  };
  const saveThen = () => void setup.save().then((ok) => ok && onRecheck());

  const warn = !!check?.ok && !!check.uncommitted;
  const problem = !check ? "" : check.tools ? "tools" : check.setup ? "setup" : warn ? "uncommitted" : !check.ok ? "other" : "";
  // A blocked state focuses its fix.
  useEffect(() => { if (problem && problem !== "setup") primary.current?.focus(); }, [problem, project]);

  const action = !project || (!check && !error) ? null
    : problem === "tools" ? { label: "Install developer tools", act: onInstallTools }
    : problem === "setup" ? setupAction(setup, onRecheck)
    : problem === "uncommitted" ? { label: `Save ${files(check!.uncommitted!)}`, act: saveThen }
    : problem === "other" || !check ? { label: "Choose another folder", act: choose }
    : folderMovesOn(check) ? { label: "Continue", act: onContinueAnyway } : null;

  const footer = <>
    <span className="safe" />
    <button type="button" className="quiet" onClick={onCancel}>Cancel</button>
    {problem === "uncommitted" && <button type="button" className="outline" onClick={onContinueAnyway}>Continue without them</button>}
    {action && <button type="button" className="primary" ref={primary} disabled={setup.busy} onClick={action.act}>{action.label}</button>}
  </>;

  return (
    <StepPanel {...panel} title="Which project should the team work on?" hint="Choosing a folder moves to the next step." footer={footer}>
      {machine !== null ? (
        <div className="box folder">
          <FolderIcon />
          <RemoteProject machine={machine} value={typed} onChange={setTyped} onDone={onUse} />
        </div>
      ) : project ? (
        <div className="box folder">
          <FolderIcon />
          <span className="grow">{shortPath(project)}</span>
          <button type="button" className="pick" onClick={choose}>Choose another…</button>
        </div>
      ) : (
        <>
          {resume && <button type="button" className="option" onClick={resume.act}><b>Continue setting up {resume.name}</b></button>}
          <div className="pickers">
            <button type="button" className="option" onClick={choose}><FolderIcon /><b>Choose a folder…</b></button>
            <button type="button" className="option" onClick={newFolder}><b>New folder…</b></button>
          </div>
          {recent.length > 0 && (
            <div className="recent-projects">
              <div className="label">Recent projects</div>
              {recent.slice(0, 3).map((p) => (
                <button type="button" className="option" key={p} onClick={() => onUse(p)}>
                  <b>{base(p)}</b><small>{shortPath(p)}</small>
                </button>
              ))}
            </div>
          )}
        </>
      )}
      {note && <p className="error">{note}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {setup.error && problem === "uncommitted" && <p className="error" role="alert">{setup.error}</p>}
      {project && !check && !error && <p className="muted" role="status">Checking this folder…</p>}
      {problem === "setup" && <FolderSetupBlock setup={setup} />}
      {problem === "tools" && (
        <div className="assieme-block" role="alert">
          <p>Assieme needs Apple’s developer tools to keep versions of your project. macOS asks you to confirm.</p>
        </div>
      )}
      {problem === "other" && <div className="warn-block" role="alert">{check!.reason}</div>}
      {problem === "uncommitted" && (
        <div className="warn-block" role="alert">
          {`${files(check!.uncommitted!)} changed since the last saved version. Agents won’t see these changes.`}
        </div>
      )}
    </StepPanel>
  );
}
