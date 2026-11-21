import { useEffect, useRef, useState } from "react";
import Details from "./Details";
import { folderPreview, folderSave, type FolderPreview } from "./bridge";
import { folderPreviewOn, folderSaveOn } from "./remote";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const files = (n: number) => (n === 1 ? "1 file" : `${n} files`);
const NAMES = 3;
const more = (n: number) => (n > 0 ? ` and ${n} more` : "");
/** At most NAMES names, then how many more. */
const leftOut = (names: string[]) => names.slice(0, NAMES).join(", ") + more(names.length - NAMES);

export type FolderSetupState = { preview: FolderPreview | null; error: string; busy: boolean;
  save: () => Promise<boolean>; retry: () => void };

/** A first saved version (or a new one) for the Project step's folder. `folderSave` picks the right commit itself. */
/** `load`: look through the folder now (the setup block shows it); a plain save, for uncommitted files, needs no preview. */
export function useFolderSetup(project: string, machine?: string, load = true): FolderSetupState {
  const [preview, setPreview] = useState<FolderPreview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const shown = useRef(project); // a slow answer for an earlier folder never lands
  shown.current = project;
  const look = () => {
    const p = project;
    setPreview(null);
    setError("");
    (machine ? folderPreviewOn(p, machine) : folderPreview(p)).then(
      (got) => { if (shown.current === p) setPreview(got); },
      (e) => { if (shown.current === p) setError(message(e)); });
  };
  useEffect(() => { if (load && project) look(); }, [project, machine, load]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await (machine ? folderSaveOn(project, machine) : folderSave(project));
      return true;
    } catch (e) {
      setError(message(e));
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { preview, error, busy, save, retry: () => (preview ? void save() : look()) };
}

/** The footer's primary for the block, or none while the folder is being looked through. */
export function setupAction(s: FolderSetupState, onSaved: () => void = () => {}): { label: string; act: () => void } | null {
  const saveThen = () => void s.save().then((ok) => ok && onSaved());
  if (s.error) return { label: "Try again", act: s.preview ? saveThen : s.retry };
  if (!s.preview) return null;
  return { label: s.preview.empty ? "Start project" : "Save and continue", act: saveThen };
}

/** The Assieme-tinted block on the Project step: what Assieme does to the folder before agents start. No buttons of
 *  its own beyond Show all files and Details; the action is the footer's. Copy from Paper frame 1 and copy.md § 3. */
export default function FolderSetupBlock({ setup }: { setup: FolderSetupState }) {
  const [all, setAll] = useState(false);
  const { preview, error } = setup;
  if (error) {
    const title = !preview ? "Couldn’t look through this folder" : preview.first ? "Couldn’t save the first version" : "Couldn’t save the new version";
    return (
      <div className="assieme-block" role="alert">
        <strong>{title}</strong>
        <p>{preview ? "Your files are unchanged. Another app may be using this folder’s versions. Close it, then try again."
          : "Your files are unchanged. Try again, or choose another folder."}</p>
        <div><Details text={`Git said: ${error}`} /></div>
      </div>
    );
  }
  if (!preview) return <div className="assieme-block" role="status"><p>Looking through this folder…</p></div>;
  const { first, empty, sample, left_out: out } = preview;
  const what = first ? "the first version" : "the new version";
  const title = empty ? "Start a new project in this folder?" : first ? "This folder has no saved version yet" : `Save ${files(preview.files)} as a new version?`;
  return (
    <div className="assieme-block">
      <strong>{title}</strong>
      <p>{empty ? "The folder is empty, so agents start from scratch."
        : first ? "Agents start from a saved version of your project, so Assieme saves one first. Your files stay as they are."
        : "Agents start from the last saved version. Your files stay as they are."}</p>
      {!empty && (
        <div className="files-line">
          <span className="grow">{`${files(preview.files)}.${out.length ? ` Left out: ${leftOut(out)}.` : ""}`}</span>
          <button type="button" className="link" onClick={() => setAll(!all)}>{all ? "Hide files" : "Show all files"}</button>
        </div>
      )}
      {all && !empty && (
        <div className="folder-box" tabIndex={0} role="region" aria-label={`Files in ${what}`}>
          {[...sample, ...(preview.files > sample.length ? [`and ${preview.files - sample.length} more`] : [])].join("\n")}
        </div>
      )}
      <p className="small-line">When agents work, what they read goes to the company behind each agent, such as Anthropic, OpenAI or Google.</p>
      <div><Details text={empty ? "Uses git: git init, then an empty first commit." : first ? "Uses git: git init, then a first commit." : "Uses git: git add, then a commit."} /></div>
    </div>
  );
}
