import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  computeNewLineNumber, computeOldLineNumber, Decoration, Diff, getChangeKey, getCollapsedLinesCountBetween, Hunk, isDelete,
  isInsert, parseDiff, type ChangeData, type HunkData,
} from "react-diff-view";
import "react-diff-view/style/index.css";
import {
  openLink, reviewAsk, reviewDiscard, reviewEditor, reviewGet, reviewMerge, reviewNote, reviewPullRequest,
  type MergeResult, type ReviewCheck, type ReviewData, type ReviewNote,
} from "./bridge";
import { PreviewGroup, usePreviews } from "./Preview";
import "./Review.css";

const words = (e: unknown) => (e instanceof Error ? e.message : String(e));
const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

/** The dark notice from Paper "v2 — Board: after merge". It goes by itself after `ms`. */
export function Notice({ children, ms, onDone, action }: {
  children: ReactNode;
  ms: number;
  onDone: () => void;
  action?: ReactNode;
}) {
  const done = useRef(onDone);
  done.current = onDone;
  useEffect(() => {
    const t = setTimeout(() => done.current(), ms);
    return () => clearTimeout(t);
  }, [ms]); // a new message gets a new `key`; a re-render must not restart the wait
  return (
    <div className="notice" role="status">
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3 3 7-7" fill="none" stroke="#7fd19d" strokeWidth="2" strokeLinecap="round" /></svg>
      <div className="notice-text">{children}</div>
      {action}
    </div>
  );
}

function CheckRow({ check }: { check: ReviewCheck }) {
  return (
    <>
      <div className={`check check-${check.state}`}>
        {check.state === "ok" && <svg width="13" height="13" viewBox="0 0 16 16" aria-label="Passed"><path d="M3 8.5l3 3 7-7" fill="none" stroke="#3e8e5e" strokeWidth="2" strokeLinecap="round" /></svg>}
        {check.state === "bad" && <svg width="13" height="13" viewBox="0 0 16 16" aria-label="Problem"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="#c8432b" strokeWidth="2" strokeLinecap="round" /></svg>}
        {check.state !== "ok" && check.state !== "bad" && <span className="check-dot" />}
        <span>{check.text}</span>
      </div>
      {check.detail && <pre className="check-detail">{check.detail}</pre>}
    </>
  );
}

function NoteCard({ note, role, away, busy, onAct }: {
  note: ReviewNote;
  role: string;
  away: boolean;
  busy: boolean;
  onAct: (id: string, action: "fix" | "ignore") => void;
}) {
  return (
    <div className="note">
      <div className="note-head">
        <span className="mono">{note.author}</span> on line {note.line}{away ? ", which isn't in this diff any more" : ""}
      </div>
      <div className="note-text">{note.text}</div>
      <div className="note-actions">
        <button type="button" className="note-fix" disabled={busy} onClick={() => onAct(note.id, "fix")}>Ask {role} to fix it</button>
        <button type="button" className="note-ignore" disabled={busy} onClick={() => onAct(note.id, "ignore")}>Ignore</button>
      </div>
    </div>
  );
}

/** One line number per row, as in Paper: deleted lines keep their old number, the rest show their new one. */
function gutter({ change, side }: { change: ChangeData; side: "old" | "new" }) {
  if (side === "old") return null;
  return isDelete(change) ? computeOldLineNumber(change) : computeNewLineNumber(change);
}

function firstLine(hunks: HunkData[]): number | undefined {
  const changes = hunks.flatMap((h) => h.changes);
  return computeNewLineNumber(changes.find(isInsert) ?? changes[0]) || undefined;
}

/** Git ends a path that has spaces with a tab on its ---/+++ lines. */
const samePath = (a: string, b: string) => a.trimEnd() === b;
const fileIn = (files: ReturnType<typeof parseDiff>, path: string) =>
  files.find((f) => samePath(f.newPath, path)) ?? files.find((f) => samePath(f.oldPath, path));

function FileDiff({ data, path, busy, onNote }: {
  data: ReviewData;
  path: string;
  busy: boolean;
  onNote: (id: string, action: "fix" | "ignore") => void;
}) {
  const parsed = useMemo(() => {
    try {
      return parseDiff(data.diff);
    } catch {
      return null; // the file list and Open in editor still work
    }
  }, [data.diff]);
  const file = parsed ? fileIn(parsed, path) : undefined;
  const notes = data.notes.filter((n) => n.file === path);
  const hunks = file?.hunks ?? [];
  const changes = hunks.flatMap((h) => h.changes);
  const widgets: Record<string, ReactNode> = {};
  const away: ReviewNote[] = [];
  for (const note of notes) {
    const change = changes.find((c) => !isDelete(c) && computeNewLineNumber(c) === note.line);
    if (!change) away.push(note);
  }
  for (const change of changes) {
    const here = isDelete(change) ? [] : notes.filter((n) => n.line === computeNewLineNumber(change));
    if (here.length)
      widgets[getChangeKey(change)] = here.map((n) => <NoteCard key={n.id} note={n} role={data.role} away={false} busy={busy} onAct={onNote} />);
  }
  const info = data.files.find((f) => f.path === path);
  const message = !parsed ? "This diff couldn't be read. Open the file in your editor."
    : info?.binary ? "This is a binary file, so there's nothing to show here."
    : !hunks.length ? "No lines changed in this file." : null;
  return (
    <div className="diff-area">
      {away.map((n) => <NoteCard key={n.id} note={n} role={data.role} away busy={busy} onAct={onNote} />)}
      {message ? <p className="diff-message">{message}</p> : file && (
        <Diff viewType="unified" diffType={file.type} hunks={hunks} widgets={widgets} renderGutter={gutter}
          className="review-diff">
          {(hs) => hs.flatMap((h, i) => {
            const hidden = i ? getCollapsedLinesCountBetween(hs[i - 1], h) : 0;
            return [
              ...(hidden > 0 ? [
                <Decoration key={`gap-${h.content}`} className="unchanged">
                  <span className="unchanged-mark">⋯</span>
                  <span>{plural(hidden, "unchanged line")}</span>
                </Decoration>,
              ] : []),
              <Hunk key={h.content} hunk={h} />,
            ];
          })}
        </Diff>
      )}
    </div>
  );
}

type Dialog = "ask" | "discard" | "merge" | null;

/** Review, from Paper "v2 — Review": one agent's branch, its checks and diff, and what to do with it. */
export default function Review({ team, role, pollMs = 5000, from = "Terminals", onBack, onMerged, onDiscarded }: {
  team: string;
  role: string;
  pollMs?: number;
  from?: string;
  onBack: () => void;
  onMerged: (result: MergeResult) => void;
  onDiscarded: (text: string) => void;
}) {
  const [data, setData] = useState<ReviewData | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [problem, setProblem] = useState("");
  const [sent, setSent] = useState("");
  const [dialog, setDialog] = useState<Dialog>(null);
  const [request, setRequest] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useRef<() => void>(() => {});
  const loaded = useRef(false);
  const pv = usePreviews(pollMs);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      clearTimeout(timer);
      try {
        const d = await reviewGet(team, role, !loaded.current);
        if (!stopped) {
          if (!loaded.current) setProblem(""); // an action's refusal stays; only a failed first load clears here
          loaded.current = true;
          setData(d);
        }
      } catch (e) {
        if (!stopped && !loaded.current) setProblem(words(e)); // later blips: the last good view stays
      }
      if (!stopped) timer = setTimeout(poll, pollMs);
    };
    refresh.current = () => void poll();
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [team, role, pollMs]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && setDialog(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy]);

  /** Every action: one at a time, its refusal shown on the screen, the view fresh afterwards. */
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setProblem("");
    try {
      await fn();
      setDialog(null);
    } catch (e) {
      setDialog(null);
      setProblem(words(e));
      refresh.current();
    } finally {
      setBusy(false);
    }
  };

  const crumbs = (
    <div className="crumbs">
      <strong>{data?.project ?? team}</strong> · This Mac /{" "}
      <button type="button" className="crumb" onClick={onBack}>{from}</button> / <strong>{role}</strong>
    </div>
  );
  if (!data)
    return (
      <div className="review">
        <div className="review-bar" data-tauri-drag-region>{crumbs}</div>
        {problem && <div className="refused">{problem}</div>}
      </div>
    );

  const file = data.files.find((f) => f.path === picked) ?? data.files[0];
  const check = (id: string) => data.checks.find((c) => c.id === id);
  const blocked = check("conflicts")?.state === "bad" || !!check("uncommitted") || data.ahead === 0;
  const tests = check("tests")?.state;
  const working = data.agent.working;
  const merge = () => act(async () => onMerged(await reviewMerge(team, role, data.tip, dialog === "merge")));
  const note = (id: string, action: "fix" | "ignore") => act(async () => {
    await reviewNote(team, role, id, action);
    if (action === "fix") setSent(`Sent the note to ${role}.`);
    refresh.current();
  });

  return (
    <div className="review">
      <div className="review-bar" data-tauri-drag-region>
        {crumbs}
        <div className="grow" />
        <button type="button" className="quiet" disabled={busy} onClick={() => setDialog("discard")}>Discard</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => setDialog("ask")}>Ask for changes</button>
        <button type="button" className="secondary" disabled={busy || data.ahead === 0} onClick={() => act(async () => {
          const pr = await reviewPullRequest(team, role);
          if (pr.url) await openLink(pr.url);
          setSent(pr.url ? `The pull request is open: ${pr.url}` : pr.detail ?? "");
        })}>Open pull request</button>
        <button type="button" className="primary" disabled={busy || blocked}
          onClick={() => (tests === "bad" || tests === "running" || working ? setDialog("merge") : void merge())}>
          Merge into {data.base}
        </button>
      </div>
      {problem && <div className="refused">{problem}</div>}
      <div className="review-body">
        <aside className="review-side">
          <div className="side-group title">
            <h1>{role}</h1>
            <div className="by">By <span className="mono">{role}</span> on branch <span className="mono">{data.branch}</span></div>
          </div>
          <div className="side-group">
            <h2>Checks</h2>
            {data.checks.map((c) => <CheckRow key={c.id} check={c} />)}
          </div>
          <PreviewGroup team={team} role={role} pv={pv} />
          <div className="side-group files">
            <h2>Files</h2>
            {data.files.map((f) => (
              <button key={f.path} type="button" className={`file${f === file ? " on" : ""}`} aria-current={f === file}
                onClick={() => setPicked(f.path)}>
                <span className="file-path">{f.path}</span>
                <span className="count">{f.add > 0 && <i className="add">+{f.add}</i>} {f.del > 0 && <i className="del">−{f.del}</i>}</span>
              </button>
            ))}
            {data.files.length === 0 && <p className="nothing">No changes yet.</p>}
          </div>
        </aside>
        <main className="review-main">
          {data.too_big && <p className="diff-message">This diff is too big to show here. Open it in your editor.</p>}
          {file && (
            <>
              <div className="file-head">
                <span className="file-name">{file.path}</span>
                <span className="count">{file.add > 0 && <i className="add">+{file.add}</i>} {file.del > 0 && <i className="del">−{file.del}</i>}</span>
                <div className="grow" />
                <button type="button" className="open-editor" disabled={file.missing} onClick={() => act(() => {
                  const parsed = data.diff ? fileIn(parseDiff(data.diff), file.path) : undefined;
                  return reviewEditor(team, role, file.path, parsed ? firstLine(parsed.hunks) : undefined);
                })}>Open in editor</button>
              </div>
              {!data.too_big && <FileDiff data={data} path={file.path} busy={busy} onNote={note} />}
            </>
          )}
        </main>
      </div>

      {dialog && (
        <div className="review-scrim">
          <div className="sheet" role="dialog" aria-modal="true" aria-label={dialog === "ask" ? "Ask for changes" : dialog === "discard" ? "Discard" : "Merge anyway"}>
            {dialog === "ask" && (
              <>
                <label className="sheet-title" htmlFor="review-request">What should {role} change?</label>
                <textarea id="review-request" autoFocus value={request} onChange={(e) => setRequest(e.target.value)} rows={4} />
                <div className="sheet-actions">
                  <button type="button" className="quiet" disabled={busy} onClick={() => setDialog(null)}>Cancel</button>
                  <button type="button" className="primary" disabled={busy || !request.trim()} onClick={() => act(async () => {
                    const r = await reviewAsk(team, role, request);
                    setRequest("");
                    setSent(r.restarted ? `${role} started again with your request.` : `Sent to ${role}.`);
                    refresh.current();
                  })}>Send to {role}</button>
                </div>
              </>
            )}
            {dialog === "discard" && (
              <>
                <div className="sheet-title">Discard {role}'s work?</div>
                <p>This stops {role} and removes its {plural(data.ahead, "commit")} and its worktree, along with any uncommitted changes.</p>
                <div className="sheet-actions">
                  <button type="button" className="quiet" disabled={busy} onClick={() => setDialog(null)}>Keep it</button>
                  <button type="button" className="danger" disabled={busy} onClick={() => act(async () => {
                    const r = await reviewDiscard(team, role, data.tip);
                    onDiscarded(r.problem ?? `Discarded ${role}'s work.`);
                  })}>Discard</button>
                </div>
              </>
            )}
            {dialog === "merge" && (
              <>
                <div className="sheet-title">
                  {tests === "bad" ? "The tests fail on this branch" : tests === "running" ? "The tests are still running" : `${role} is still working`}
                </div>
                {working && <p>Merging also stops {role}, which is still working.</p>}
                <div className="sheet-actions">
                  <button type="button" className="quiet" disabled={busy} onClick={() => setDialog(null)}>Not yet</button>
                  <button type="button" className="primary" disabled={busy} onClick={merge}>Merge anyway</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
      {sent && <Notice key={sent} ms={6000} onDone={() => setSent("")}>{sent}</Notice>}
      {pv.sheet}
    </div>
  );
}
