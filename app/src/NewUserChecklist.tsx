import { useEffect, useState, type ReactNode } from "react";
import { installTools, setupStatus, type AgentKind, type SetupStatus } from "./bridge";
import InstallSheet from "./InstallSheet";

const AGENTS: [AgentKind, string][] = [["claude", "Claude Code"], ["codex", "Codex"]];
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

function Mark({ ok }: { ok: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      {ok ? (
        <path d="M3 8.5l3 3 7-7" fill="none" stroke="var(--quiet-working)" strokeWidth="1.8" strokeLinecap="round" />
      ) : (
        <circle cx="8" cy="8" r="5.5" fill="none" stroke="var(--quiet-control)" strokeWidth="1.5" />
      )}
    </svg>
  );
}

/** A row that is done: the name and a short note. */
const Done = ({ name, note }: { name: string; note: string }) => (
  <div className="check-row done">
    <Mark ok />
    <span className="check-name">{name}</span>
    <span className="check-note">{note}</span>
  </div>
);

/** A row with something to do: the name, a line about it, and its buttons. */
const Todo = ({ name, text, error, children }: { name: string; text?: string; error?: string; children: ReactNode }) => (
  <div className="check-row todo">
    <Mark ok={false} />
    <div className="check-body">
      <div className="check-text">
        <span className="check-name">{name}</span>
        {text && <span className="check-note">{text}</span>}
      </div>
      <div className="check-buttons">{children}</div>
      {error && <p className="check-error" role="alert">{error}</p>}
    </div>
  </div>
);

/** First run for someone with no agents yet, from Paper "v2 — First run: new user" and its variants.
 *  Polls the setup status every 3 s, one call at a time, and only while it is on screen. */
export default function NewUserChecklist({ herdr, onStart, onOpenSettings, pollMs = 3000 }: {
  herdr: boolean;
  onStart: () => void;
  onOpenSettings?: (section: string) => void;
  pollMs?: number;
}) {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [statusError, setStatusError] = useState("");
  const [waiting, setWaiting] = useState(false); // Apple's installer was opened
  const [toolsError, setToolsError] = useState("");
  const [sheet, setSheet] = useState<{ agent: AgentKind; start: "install" | "signin" } | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const s = await setupStatus();
        if (!stopped) {
          setStatus(s);
          setStatusError("");
        }
      } catch (e) {
        if (!stopped) setStatusError(message(e));
      }
      if (!stopped) timer = setTimeout(poll, pollMs);
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [pollMs]);

  const openInstaller = async () => {
    setToolsError("");
    try {
      await installTools();
      setWaiting(true);
    } catch (e) {
      setToolsError(message(e));
    }
  };

  const installed = status ? AGENTS.filter(([id]) => status.agents[id].installed) : [];
  // Developer tools don't block starting: New team stops at its project check with the same fix.
  // A status that can't be read doesn't lock anyone out either.
  const ready = status ? installed.some(([id]) => status.agents[id].signed_in) : !!statusError;
  const why = status ? (installed.length ? "after you’ve signed in" : "after you’ve added an agent") : "checking…";

  return (
    <>
      <section className="intro">
        <div className="eyebrow">Welcome to Assieme</div>
        <h1>Let’s start your first team.</h1>
        <p className="lede">
          Assieme runs coding agents for you and tells you when one needs you. Setup may take a while, mostly downloads,
          and you won’t need to type any commands.
        </p>
        {statusError && <p className="check-error" role="alert">Couldn’t check your setup: {statusError}</p>}
        {status && (
          <div className="checklist">
            {status.git ? (
              <Done name="Developer tools" note="Installed" />
            ) : (
              <Todo name="Developer tools" error={toolsError}
                text={waiting
                  ? "Waiting for Apple’s installer… Finish there, and this will update by itself."
                  : "Apple’s free Command Line Tools, which track your project’s changes. This opens Apple’s installer, a large download. You can install an agent meanwhile."}>
                <button type="button" className="outline" onClick={openInstaller}>{waiting ? "Open it again" : "Install developer tools"}</button>
              </Todo>
            )}
            {installed.map(([id, name]) =>
              status.agents[id].signed_in ? (
                <Done key={id} name={name} note="Installed" />
              ) : (
                <div className="check-row" key={id}>
                  <Mark ok={false} />
                  <div className="check-text grow">
                    <span className="check-name">{name}</span>
                    <span className="check-note">Installed, not signed in</span>
                  </div>
                  <button type="button" className="outline" onClick={() => setSheet({ agent: id, start: "signin" })}>Sign in</button>
                </div>
              ))}
            {!installed.length && (
              <Todo name="An agent to do the work"
                text="Pick the one you pay for. Claude Code needs a paid Claude plan. Codex works with ChatGPT, with limits on the free plan.">
                {AGENTS.map(([id, name]) => (
                  <button key={id} type="button" className="outline" onClick={() => setSheet({ agent: id, start: "install" })}>{`Install ${name}`}</button>
                ))}
              </Todo>
            )}
            {herdr && <Done name="herdr" note="connected, so fixes can be sent" />}
          </div>
        )}
        <div className="open-team">
          <button type="button" disabled={!ready} aria-describedby="start-why" onClick={onStart}>Start your first team</button>
          <span id="start-why">{ready ? "on a project you choose" : why}</span>
        </div>
        <p className="footnote">
          Assieme runs on this Mac and has no server of its own.{" "}
          {onOpenSettings ? <button type="button" className="link-btn" onClick={() => onOpenSettings("privacy")}>Settings, Privacy</button> : "Settings, Privacy"}{" "}
          shows what it keeps.
        </p>
      </section>

      <section className="panel aside">
        <div className="panel-label">What happens to your project</div>
        <div><h3>Each agent works on its own copy</h3><p>Your files stay as they are while it works.</p></div>
        <div><h3>You watch it live</h3><p>See what it’s doing, and step in whenever you like.</p></div>
        <div><h3>You decide what to keep</h3><p>Review the changes, then accept them or throw them away.</p></div>
      </section>
      {sheet && <InstallSheet key={`${sheet.agent}:${sheet.start}`} {...sheet} status={status} onClose={() => setSheet(null)} />}
    </>
  );
}
