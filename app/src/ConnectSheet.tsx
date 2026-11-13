import { useEffect, useState } from "react";
import { connectAntigravity, installStop, setupStatus, terminalState } from "./bridge";
import Terminal from "./Terminal";
import useModal from "./useModal";

// shortcut: the folder as it is with the default state folder; the brain builds it wherever its state lives.
const COMMAND = "agy plugin install ~/.local/state/assieme/agy-plugin";
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Paper "v2 — Antigravity: connect Assieme": the person's OK before Assieme's plugin goes into Antigravity, then the
 *  install in a terminal they can watch. Only a fresh status saying it's connected counts as done. */
export default function ConnectSheet({ onConnected, onClose, pollMs = 1500 }: {
  onConnected: () => void;
  onClose: () => void;
  pollMs?: number;
}) {
  const [address, setAddress] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const connect = async () => {
    setBusy(true);
    setError("");
    setFailed(false);
    try {
      setAddress((await connectAntigravity()).address);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!address || failed) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        if ((await terminalState(address)) === "finished") {
          const ok = !!(await setupStatus()).agents.antigravity?.connected;
          if (stopped) return;
          return ok ? onConnected() : setFailed(true);
        }
      } catch (e) {
        if (!stopped) setError(`Couldn’t check the plugin: ${message(e)}`);
      }
      if (!stopped) timer = setTimeout(poll, pollMs);
    };
    timer = setTimeout(poll, pollMs);
    return () => { stopped = true; clearTimeout(timer); };
  }, [address, failed, pollMs]); // eslint-disable-line react-hooks/exhaustive-deps

  const close = async () => {
    if (address) await installStop(address).catch(() => {});
    onClose();
  };
  const { ref, onKeyDown } = useModal(() => void close(), [!!address]);

  return (
    <div className="modal-dim">
      <div className={address ? "modal wide" : "modal"} role="dialog" aria-modal="true" aria-label="Connect Assieme to Antigravity"
        tabIndex={-1} ref={ref} onKeyDown={onKeyDown}>
        <div className="modal-head">
          <h2>Connect Assieme to Antigravity?</h2>
          <p className="big">
            Antigravity only loads team tools from a plugin, so Assieme adds a small one. Its agents can then message
            their team and say when they’re stuck. The plugin also shows in your own Antigravity sessions, where it can
            read team status and asks you before changing anything.
          </p>
        </div>
        {address ? (
          <div className="sheet-terminal"><Terminal key={address} session="assieme" address={address} /></div>
        ) : (
          <div className="sheet-command">
            <p>The exact command, if you want to check it</p>
            <code>{COMMAND}</code>
            <p>Turn it off any time with agy plugin disable assieme, or remove it with agy plugin uninstall assieme.</p>
          </div>
        )}
        {failed && <p className="modal-error">The plugin didn’t connect. The terminal above says why.</p>}
        {error && <p className="modal-error">{error}</p>}
        <div className="sheet-actions">
          <button type="button" className="btn-quiet" onClick={() => void close()}>{address ? "Close" : "Not now"}</button>
          {(!address || failed) && (
            <button type="button" className="btn-primary" disabled={busy} onClick={() => void connect()}>
              {failed ? "Try again" : "Connect Antigravity"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
