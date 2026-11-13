import { useEffect, useRef, useState } from "react";
import { installAgent, installStop, openLink, setupCommands, setupStatus, signinAgent, terminalState, type AgentKind, type SetupCommands, type SetupStatus } from "./bridge";
import { kindName } from "./kinds";
import Terminal from "./Terminal";
import useModal from "./useModal";

type Step = "confirm" | "installing" | "failed" | "signin";

// Words and links for the confirmation, from Paper "v2 — Install agent: confirm" and the spec (section 3).
const CONFIRM: Record<AgentKind, { body: string; guide: string; guideName: string }> = {
  claude: {
    body: "This downloads Anthropic’s installer from claude.ai and runs it. Claude Code goes in your home folder. You’ll need a paid Claude plan (Pro or above).",
    guide: "https://docs.claude.com/en/docs/claude-code/setup",
    guideName: "Anthropic’s install guide",
  },
  codex: {
    body: "This downloads OpenAI’s installer from chatgpt.com and runs it. Codex works with ChatGPT, with limits on the free plan.",
    guide: "https://developers.openai.com/codex/cli",
    guideName: "OpenAI’s install guide",
  },
  gemini: {
    body: "This installs Google’s Gemini CLI with npm, which comes with Node.js. It works with a Google account, with daily limits on the free tier.",
    guide: "https://github.com/google-gemini/gemini-cli",
    guideName: "Google’s install guide",
  },
  opencode: {
    body: "This installs OpenCode with npm, which comes with Node.js. It works with the model providers you sign in to.",
    guide: "https://opencode.ai/docs",
    guideName: "OpenCode’s install guide",
  },
  cursor: {
    body: "This downloads Cursor’s installer from cursor.com and runs it. Cursor’s agent needs a Cursor account.",
    guide: "https://cursor.com/docs/cli/overview",
    guideName: "Cursor’s install guide",
  },
};
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The sheet that installs an agent, or signs in to one, in a terminal the person can see.
 *  status: the checklist's poll, which tells when the sign-in worked. */
export default function InstallSheet({ agent, start, status, onClose, pollMs = 2000 }: {
  agent: AgentKind;
  start: "install" | "signin";
  status: SetupStatus | null;
  onClose: () => void;
  pollMs?: number;
}) {
  const [step, setStep] = useState<Step>(start === "install" ? "confirm" : "signin");
  const [address, setAddress] = useState<string | null>(null);
  const [commands, setCommands] = useState<SetupCommands | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const name = commands?.[agent]?.name ?? kindName(agent);

  useEffect(() => { void setupCommands().then(setCommands).catch((e) => setError(message(e))); }, []);

  const begin = async (kind: "install" | "signin") => {
    setError("");
    setBusy(true);
    if (kind === "signin") {
      setStep("signin");
      setAddress(null);
    }
    try {
      const { address: a } = await (kind === "install" ? installAgent : signinAgent)(agent);
      setAddress(a);
      if (kind === "install") setStep("installing");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  const began = useRef(false); // React.StrictMode mounts effects twice in development
  useEffect(() => {
    if (start === "signin" && !began.current) {
      began.current = true;
      void begin("signin");
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // While the installer runs, ask about it every pollMs, one question at a time. A terminal that ends proves nothing
  // (the installer can fail after curl exits 0), so only a fresh status saying the agent is there moves on.
  useEffect(() => {
    if (step !== "installing" || !address) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        if ((await terminalState(address)) === "finished") {
          const found = (await setupStatus()).agents[agent];
          if (stopped) return;
          setError("");
          if (!found?.installed) return setStep("failed");
          if (found.signed_in) return onClose();
          return void begin("signin");
        }
        if (!stopped) setError("");
      } catch (e) {
        if (!stopped) setError(`Couldn’t check the installer: ${message(e)}`);
      }
      if (!stopped) timer = setTimeout(poll, pollMs);
    };
    timer = setTimeout(poll, pollMs);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [step, address, agent, pollMs]); // eslint-disable-line react-hooks/exhaustive-deps

  // Closing always works: if the terminal is already gone there is nothing left to stop.
  const stopAndClose = async () => {
    try {
      if (address) await installStop(address);
    } catch {
      /* already gone */
    }
    onClose();
  };

  // Escape does what Cancel or Skip for now does.
  const { ref: dialog, onKeyDown } = useModal(() => void (step === "installing" || step === "signin" ? stopAndClose() : onClose()), [step, !!commands]);

  const signedIn = !!status?.agents[agent]?.signed_in;
  const terminal = address && (
    <div className="sheet-terminal">
      <Terminal key={address} session="assieme" address={address} typing />
    </div>
  );
  const note = <span className="sheet-note" role="status">{error}</span>;

  if (step === "confirm") {
    const c = CONFIRM[agent];
    return (
      <div className="modal-dim">
        <div className="modal" role="dialog" aria-modal="true" aria-label={`Install ${name}`} tabIndex={-1} ref={dialog} onKeyDown={onKeyDown}>
          <div className="modal-head">
            <h2>{`Install ${name}?`}</h2>
            <p className="big">{c.body}</p>
          </div>
          <div className="sheet-command">
            <p>The exact command, if you want to check it</p>
            <code>{commands?.[agent]?.install}</code>
            <p>
              <button type="button" className="link-btn" onClick={() => openLink(c.guide)}>{c.guideName}</button>
              . Prefer another way? Install it yourself and Assieme will find it.
            </p>
          </div>
          {error && <p className="modal-error">{error}</p>}
          <div className="sheet-actions">
            <button type="button" className="btn-quiet" onClick={onClose}>Not now</button>
            <button type="button" className="btn-primary" disabled={busy || !commands} onClick={() => begin("install")}>{`Install ${name}`}</button>
          </div>
        </div>
      </div>
    );
  }

  const heading = {
    installing: [`Installing ${name}`, "This can take a minute. It may look still while it downloads. You can ignore any setup notes: Assieme will find it."],
    failed: [`${name} didn’t install`, "Check your internet, then try again. The details are below."],
    signin: [`Sign in to ${name}`, "A browser window will open. Sign in, then come back here. If it shows a code, paste it below."],
  }[step];

  return (
    <div className="modal-dim">
      <div className="modal wide" role="dialog" aria-modal="true" aria-label={heading[0]} tabIndex={-1} ref={dialog} onKeyDown={onKeyDown}>
        <div className="modal-head">
          <h2>{heading[0]}</h2>
          <p className="big">{heading[1]}</p>
        </div>
        {terminal}
        <div className="sheet-actions">
          {step === "installing" && <>{note}<button type="button" className="btn-primary" onClick={stopAndClose}>Cancel</button></>}
          {step === "failed" && (
            <>
              {note}
              <button type="button" className="btn-quiet" onClick={onClose}>Close</button>
              <button type="button" className="btn-primary" disabled={busy} onClick={() => begin("install")}>Try again</button>
            </>
          )}
          {step === "signin" && (
            <>
              {error ? note : <span className="sheet-note" role="status">{signedIn ? "Signed in." : "Waiting for sign-in…"}</span>}
              {!address && !busy && <button type="button" className="btn-quiet" onClick={() => begin("signin")}>Try again</button>}
              <button type="button" className="btn-quiet" onClick={stopAndClose}>Skip for now</button>
              <button type="button" className="btn-primary" disabled={!signedIn} onClick={stopAndClose}>Done</button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
