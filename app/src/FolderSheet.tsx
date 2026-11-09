import { useEffect, useState } from "react";
import { folderPreview, folderSave, type FolderPreview } from "./bridge";
import useModal from "./useModal";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const files = (n: number) => (n === 1 ? "1 file" : `${n} files`);
const NAMES = 3;
const more = (n: number) => (n > 0 ? ` and ${n} more` : "");
/** At most NAMES names, then how many more. */
const leftOut = (names: string[]) => names.slice(0, NAMES).join(", ") + more(names.length - NAMES);

/** Set up this folder (a first saved version) and Save them first (a new one): one sheet, since `folderSave`
 *  picks the right commit from the repository's state. Copy is from Paper "New team: set up folder" and the spec (section 2). */
export default function FolderSheet({ project, onClose, onSaved }: { project: string; onClose: () => void; onSaved: () => void }) {
  const [preview, setPreview] = useState<FolderPreview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [all, setAll] = useState(false);

  const load = () => {
    setError("");
    folderPreview(project).then(setPreview, (e) => setError(message(e)));
  };
  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await folderSave(project);
      onSaved();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const { ref, onKeyDown } = useModal(() => busy || onClose(), [!!preview, !!error]);
  const dialog = (title: string, children: React.ReactNode) => (
    <div className="modal-dim">
      <div className="modal folder" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref} onKeyDown={onKeyDown}>
        {children}
      </div>
    </div>
  );

  if (error) {
    const title = !preview ? "Couldn’t look through this folder" : preview.first ? "Couldn’t save the first version" : "Couldn’t save the new version";
    return dialog(title, <>
      <div className="modal-head">
        <h2>{title}</h2>
        <p className="big">{preview ? "Your files are as they were. Try again, or choose another folder." : "Nothing was changed. Try again, or choose another folder."}</p>
        <p className="folder-small">{`Git said: ${error}`}</p>
      </div>
      <div className="sheet-actions">
        <button type="button" className="btn-quiet" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={busy} onClick={preview ? save : load}>Try again</button>
      </div>
    </>);
  }

  if (!preview)
    return dialog("Looking through this folder", <>
      <div className="modal-head"><h2>Looking through this folder…</h2></div>
      <div className="sheet-actions"><button type="button" className="btn-quiet" onClick={onClose}>Not now</button></div>
    </>);

  const { first, empty, sample, left_out: out } = preview;
  const title = empty ? "Start this empty project?" : `Save ${files(preview.files)} as ${first ? "the first version" : "a new version"}?`;
  const what = first ? "the first version" : "the new version";
  const names = out.length > 0 && leftOut(out);
  return dialog(title, <>
    <div className="modal-head">
      <h2>{title}</h2>
      <p className="big">
        {empty ? "Agents will start from an empty saved version. " : "Your files stay as they are. "}
        {first ? "Assieme adds a hidden .git folder to track versions. " : ""}
        {empty ? "" : "Saving stays on this Mac. "}
        When agents work, what they read goes to Anthropic or OpenAI.
      </p>
      <p className="folder-small">{empty ? "Uses git: git init, then an empty first commit." : first ? "Uses git: git init, then a first commit." : "Uses git: git add, then a commit."}</p>
    </div>
    {!empty && (
      <div className="folder-files">
        {all ? (
          <>
            <p>{`Files in ${what}`}</p>
            <div className="folder-box" tabIndex={0} role="region" aria-label={`Files in ${what}`}>{[...sample, ...(preview.files > sample.length ? [`and ${preview.files - sample.length} more`] : [])].join("\n")}</div>
          </>
        ) : names ? (
          <>
            <p>Left out of the saved version, so agents don’t get a copy</p>
            <div className="folder-box">{names}</div>
          </>
        ) : null}
        <p>
          {all && names ? `Left out: ${names}. ` : ""}
          {names ? "Your project may need these to run. " : ""}
          <button type="button" className="link-btn" onClick={() => setAll(!all)}>{all ? "Hide files" : `Show all ${files(preview.files)}`}</button>
        </p>
      </div>
    )}
    <div className="sheet-actions">
      <button type="button" className="btn-quiet" disabled={busy} onClick={onClose}>Not now</button>
      <button type="button" className="btn-primary" disabled={busy} onClick={save}>{empty ? "Start project" : "Save and continue"}</button>
    </div>
  </>);
}
