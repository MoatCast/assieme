import { useState } from "react";
import { remoteConnect, remoteTerminal, since, type Machine } from "./remote";
import "./Remote.css";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The team view's words while a team's machine can't be reached: Paper "v2 — Team: remote machine unreachable". */
export function awayTitle(line: Pick<Machine, "name" | "since_ms">) {
  return {
    title: `Can’t reach ${line.name}`,
    subtitle: `${line.since_ms ? `Since ${since(line.since_ms)}. ` : ""}Its agents keep working there; this is what they were doing then.`,
  };
}

/** The card in place of "Nothing waiting on you": reconnecting, or a login only the person can give, in Terminal. */
export default function Unreachable({ line, onRetried }: { line: Machine; onRetried: () => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const login = line.state === "needs_login";
  const act = async () => {
    setBusy(true);
    setNote("");
    try {
      if (login) await remoteTerminal({ machine: line.name });
      else await remoteConnect(line.name);
      onRetried();
    } catch (e) {
      setNote(message(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="rm-away" role="status">
      {login ? (
        <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true" fill="none" stroke="var(--quiet-waste)" strokeWidth="1.7" strokeLinecap="round">
          <circle cx="7.5" cy="11" r="4" /><path d="M11.5 11H19M16.5 11v3M19 11v2.5" />
        </svg>
      ) : (
        <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true" fill="none" stroke="var(--quiet-control)" strokeWidth="1.7" strokeLinecap="round">
          <path d="M3 8.5a12 12 0 0 1 16 0M6 11.8a7.5 7.5 0 0 1 10 0M9 15a3 3 0 0 1 4 0M3.5 3.5l15 15" />
        </svg>
      )}
      <b>{login ? `${line.name} wants you to log in again` : `Reconnecting to ${line.name}…`}</b>
      <p>{login ? "Its agents keep working. Log in in Terminal and Assieme reconnects right after."
        : "Assieme tries again on its own, and at once when your Mac wakes. Buttons that change something there wait until it’s back."}</p>
      {note && <p className="rm-error">{note}</p>}
      <button type="button" className={login ? "btn-primary" : "btn-secondary"} disabled={busy} onClick={act}>
        {login ? "Open Terminal" : busy ? "Trying…" : "Try now"}
      </button>
    </div>
  );
}
