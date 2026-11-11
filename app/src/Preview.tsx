import { useEffect, useRef, useState } from "react";
import { openLink, previewList, previewStart, previewStop, type Preview } from "./bridge";
import "./Preview.css";

const words = (e: unknown) => (e instanceof Error ? e.message : String(e));
const keyOf = (team: string, role: string) => `${team}/${role}`;
/** "localhost:5174", as Paper shows it. */
export const host = (p: Preview) => (p.url ?? "").replace(/^https?:\/\//, "");

type Asking = { team: string; role: string; project: string; command: string };

/** Every agent's preview, polled, with start, stop and the start-command sheet. Review, Terminals and the Board each
 *  use one; a preview started from a screen opens in the browser once, when it is running. */
export function usePreviews(pollMs = 3000) {
  const [list, setList] = useState<Preview[]>([]);
  const [asking, setAsking] = useState<Asking | null>(null);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const opening = useRef(new Set<string>());
  const refresh = useRef<() => void>(() => {});

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      clearTimeout(timer);
      try {
        const all = await previewList();
        if (stopped) return;
        setList(all);
        for (const p of all) {
          const k = keyOf(p.team, p.role);
          if (opening.current.has(k) && p.state !== "starting") {
            opening.current.delete(k);
            if (p.state === "running" && p.url) void openLink(p.url);
          }
        }
      } catch {
        // the screen's own poll says when the brain is away
      }
      if (!stopped) timer = setTimeout(poll, opening.current.size ? 1000 : pollMs);
    };
    refresh.current = () => void poll();
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [pollMs]);

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setProblem("");
    try {
      await fn();
    } catch (e) {
      setProblem(words(e));
    } finally {
      setBusy(false);
      refresh.current();
    }
  };

  const start = (team: string, role: string, command: string | null = null, install = false) => act(async () => {
    const p = await previewStart(team, role, command, install);
    if (p.state === "needs_command") return setAsking({ team, role, project: p.project, command: "" });
    setAsking(null);
    opening.current.add(keyOf(team, role));
    setList((l) => [...l.filter((x) => keyOf(x.team, x.role) !== keyOf(team, role)), p]);
  });
  const stop = (team: string, role: string) => act(async () => {
    opening.current.delete(keyOf(team, role));
    await previewStop(team, role);
    setList((l) => l.filter((x) => keyOf(x.team, x.role) !== keyOf(team, role)));
  });
  const change = (p: Preview) => setAsking({ team: p.team, role: p.role, project: p.project, command: p.command ?? "" });
  const of = (team: string, role: string) => list.find((p) => p.team === team && p.role === role);

  const sheet = (
    <>
      {asking && (
        <CommandSheet key={keyOf(asking.team, asking.role)} asking={asking} busy={busy} problem={problem}
          onCancel={() => setAsking(null)} onStart={(command) => start(asking.team, asking.role, command)} />
      )}
      {problem && !asking && (
        <div className="preview-problem" role="alert">
          <span>{problem}</span>
          <button type="button" onClick={() => setProblem("")}>OK</button>
        </div>
      )}
    </>
  );
  return { of, start, stop, change, busy, sheet };
}

export type Previews = ReturnType<typeof usePreviews>;

/** Paper "v2 — Preview: states": how the project starts its dev server, asked once per project. */
function CommandSheet({ asking, busy, problem, onCancel, onStart }: {
  asking: Asking;
  busy: boolean;
  problem: string;
  onCancel: () => void;
  onStart: (command: string) => void;
}) {
  const [command, setCommand] = useState(asking.command);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  const go = () => command.trim() && !busy && onStart(command.trim());
  return (
    <div className="preview-scrim" onKeyDown={(e) => e.key === "Escape" && onCancel()}>
      <div className="preview-sheet" role="dialog" aria-modal="true" aria-labelledby="preview-sheet-title">
        <label id="preview-sheet-title" className="preview-sheet-title" htmlFor="preview-command">
          How does {asking.project} start its dev server?
        </label>
        <input id="preview-command" ref={input} value={command} spellCheck={false} placeholder="npm run dev"
          onChange={(e) => setCommand(e.target.value)} onKeyDown={(e) => e.key === "Enter" && go()} />
        <p>
          {asking.command ? "" : "Assieme couldn't find one in the project. "}It's saved for {asking.project} and used for
          every agent. Each agent's preview gets its own port (<code>$PORT</code> if your server needs it).
        </p>
        {problem && <p className="preview-bad">{problem}</p>}
        <div className="preview-sheet-actions">
          <button type="button" className="pv-quiet" onClick={onCancel}>Cancel</button>
          <button type="button" className="pv-primary" disabled={busy || !command.trim()} onClick={go}>Start preview</button>
        </div>
      </div>
    </div>
  );
}

export const Cross = ({ size = 13 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
    <path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="var(--quiet-needs-you)" strokeWidth="2" strokeLinecap="round" />
  </svg>
);

/** The Preview group in Review's sidebar, from Paper "v2 — Preview: running, in Review" and "v2 — Preview: states". */
export function PreviewGroup({ team, role, pv }: { team: string; role: string; pv: Previews }) {
  const p = pv.of(team, role);
  const stop = <button type="button" className="quiet" disabled={pv.busy} onClick={() => pv.stop(team, role)}>Stop</button>;
  return (
    <div className="side-group preview-group">
      <h2>Preview</h2>
      {!p && (
        <>
          <p className="preview-text">See {role}'s branch running before you merge it.</p>
          <div className="preview-buttons">
            <button type="button" className="secondary" disabled={pv.busy} onClick={() => pv.start(team, role)}>Start preview</button>
          </div>
        </>
      )}
      {p?.state === "starting" && (
        <>
          <div className="preview-state"><span className="preview-dot" />Starting <span className="mono">{p.command}</span>…</div>
          <div className="preview-buttons indent">{stop}</div>
        </>
      )}
      {p?.state === "running" && (
        <>
          <div className="preview-state"><span className="preview-dot on" />Running at <span className="mono ink">{host(p)}</span></div>
          <div className="preview-sub">{p.command}, in {role}'s worktree</div>
          <div className="preview-buttons indent">
            <button type="button" className="primary" onClick={() => p.url && openLink(p.url)}>Open preview</button>
            {stop}
          </div>
        </>
      )}
      {p?.state === "failed" && (
        <>
          <div className="preview-state bad"><Cross />The preview didn't start</div>
          <div className="preview-sub">{p.reason}</div>
          {p.output && <pre className="preview-out">{p.output}</pre>}
          <div className="preview-buttons indent">
            <button type="button" className="primary" disabled={pv.busy} onClick={() => pv.start(team, role, null, p.fix === "install")}>
              {p.fix === "install" ? "Install and try again" : "Try again"}
            </button>
            <button type="button" className="quiet" disabled={pv.busy} onClick={() => pv.change(p)}>Change the start command</button>
          </div>
          {p.fix === "install" && p.install && (
            <div className="preview-sub small">Runs <span className="mono">{p.install}</span> in {role}'s worktree, which downloads its packages.</div>
          )}
        </>
      )}
    </div>
  );
}
