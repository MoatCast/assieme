import { useEffect, useRef, useState } from "react";
import {
  installAgent, installStop, openLink, setupCommands, setupStatus, signinAgent, terminalState,
  type AgentKind, type SetupCommands, type SetupStatus,
} from "./bridge";
import { kindName } from "./kinds";
import { installAgentOn, setupStatusOn, signinAgentOn } from "./remote";
import Terminal from "./Terminal";

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
    body: "This installs Google’s Gemini CLI with npm, which comes with Node.js. Since June 2026 it needs a paid Gemini API key or a company Code Assist licence: a personal Google account can’t sign in to it.",
    guide: "https://github.com/google-gemini/gemini-cli",
    guideName: "Google’s install guide",
  },
  opencode: {
    body: "This installs OpenCode with npm, which comes with Node.js. It works with the model providers you sign in to.",
    guide: "https://opencode.ai/docs",
    guideName: "OpenCode’s install guide",
  },
  antigravity: {
    body: "This downloads Google’s installer from antigravity.google and runs it. Antigravity works with a Google account, including the free plan.",
    guide: "https://antigravity.google/product/antigravity-cli",
    guideName: "Google’s install guide",
  },
  cursor: {
    body: "This downloads Cursor’s installer from cursor.com and runs it. Cursor’s agent needs a Cursor account.",
    guide: "https://cursor.com/docs/cli/overview",
    guideName: "Cursor’s install guide",
  },
};
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

export type FlowAction = { label: string; act: () => void; busy: boolean } | null;

/** Installing an agent, then signing in to it, in a terminal the person can see. It sits in the Agent step's panel
 *  (the panel's footer draws the primary it hands over through onAction) or in InstallSheet (`inSheet`: it draws its
 *  own row of buttons). status: a poll that tells when the sign-in worked. */
export default function InstallFlow({ agent, start, status, onDone, onSkip, onAction, pollMs = 2000, machine, inSheet = false }: {
  agent: AgentKind;
  start: "install" | "signin";
  status: SetupStatus | null;
  onDone: () => void;
  onSkip: () => void;
  onAction: (a: FlowAction) => void;
  pollMs?: number;
  /** Another machine (step 15): it installs there, in a terminal on that machine. */
  machine?: string;
  inSheet?: boolean;
}) {
  const install = machine ? (a: AgentKind) => installAgentOn(a, machine) : installAgent;
  const signin = machine ? (a: AgentKind) => signinAgentOn(a, machine) : signinAgent;
  const statusNow = machine ? () => setupStatusOn(machine) : setupStatus;
  const [there, setThere] = useState<SetupStatus | null>(null); // a machine's own sign-in check: nobody else polls it
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
      const { address: a } = await (kind === "install" ? install : signin)(agent);
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
          const found = (await statusNow()).agents[agent];
          if (stopped) return;
          setError("");
          if (!found?.installed) return setStep("failed");
          if (found.signed_in) return onDone();
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

  // Stopping always works: if the terminal is already gone there is nothing left to stop.
  const stopThen = async (then: () => void) => {
    try {
      if (address) await installStop(address);
    } catch {
      /* already gone */
    }
    then();
  };

  useEffect(() => {
    if (!machine || step !== "signin") return;
    const timer = setInterval(() => void statusNow().then(setThere, () => {}), pollMs);
    return () => clearInterval(timer);
  }, [machine, step, pollMs]); // eslint-disable-line react-hooks/exhaustive-deps
  const signedIn = !!(status ?? there)?.agents[agent]?.signed_in;

  const primary: FlowAction = step === "confirm" ? { label: `Install ${name}`, act: () => void begin("install"), busy: busy || !commands }
    : step === "installing" ? { label: "Cancel", act: () => void stopThen(onSkip), busy: false }
    : step === "failed" ? { label: "Try again", act: () => void begin("install"), busy }
    : { label: "Done", act: () => void stopThen(onDone), busy: !signedIn };
  useEffect(() => { onAction(primary); }, [step, busy, !!commands, signedIn, address, name]); // eslint-disable-line react-hooks/exhaustive-deps

  const terminal = address && (
    <div className="sheet-terminal">
      <Terminal key={address} session="assieme" address={address} typing />
    </div>
  );
  const note = <span className="sheet-note" role="status">{error}</span>;
  const quiet = inSheet ? "btn-quiet" : "quiet";
  const own = inSheet && (
    <button type="button" className="btn-primary" disabled={primary!.busy} onClick={primary!.act}>{primary!.label}</button>
  );
  const row = (children: React.ReactNode) => <div className={inSheet ? "sheet-actions" : "flow-actions"}>{children}</div>;

  if (step === "confirm") {
    const c = CONFIRM[agent];
    return (
      <>
        <div className="modal-head">
          <h2 id="install-title">{`Install ${name}?`}</h2>
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
        {inSheet && row(<><button type="button" className={quiet} onClick={onSkip}>Not now</button>{own}</>)}
      </>
    );
  }

  const heading = {
    installing: [`Installing ${name}`, "This can take a minute. It may look still while it downloads. You can ignore any setup notes: Assieme will find it."],
    failed: [`${name} didn’t install`, "Check your internet, then try again. The details are below."],
    signin: [`Sign in to ${name}`, machine ? `Open the link it shows below, sign in, then paste the code here. It signs ${name} in on ${machine}.`
      : "A browser window will open. Sign in, then come back here. If it shows a code, paste it below."],
  }[step];

  return (
    <>
      <div className="modal-head">
        <h2 id="install-title">{heading[0]}</h2>
        <p className="big">{heading[1]}</p>
      </div>
      {terminal}
      {row(<>
        {step === "installing" && <>{note}{own}</>}
        {step === "failed" && <>{note}{inSheet && <button type="button" className={quiet} onClick={onSkip}>Close</button>}{own}</>}
        {step === "signin" && (
          <>
            {error ? note : <span className="sheet-note" role="status">{signedIn ? "Signed in." : "Waiting for sign-in…"}</span>}
            {!address && !busy && <button type="button" className={quiet} onClick={() => void begin("signin")}>Try again</button>}
            <button type="button" className={quiet} onClick={() => void stopThen(onSkip)}>Skip for now</button>
            {own}
          </>
        )}
      </>)}
    </>
  );
}
