import { useState } from "react";
import { installTools, type SetupStatus } from "./bridge";
import StepPanel, { type PanelProps } from "./StepPanel";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
export const LOCAL_NOTE = "Assieme runs on this Mac and has no server of its own.";

function Spinner() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="none" stroke="var(--quiet-line)" strokeWidth="2" />
      <path d="M8 2a6 6 0 0 1 6 6" fill="none" stroke="var(--quiet-ink)" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

/** First run, Developer tools: Apple's installer is a long wait, so the step never moves on by itself; Continue while
 *  it installs. Paper "v3 — First run: Developer tools (installing)". */
export function ToolsStep({ panel, status, onNext }: { panel: PanelProps; status: SetupStatus | null; onNext: () => void }) {
  const [asked, setAsked] = useState(false);
  const [error, setError] = useState("");
  const open = () => {
    setError("");
    setAsked(true);
    installTools().catch((e) => setError(message(e)));
  };
  const done = !!status?.git;
  const label = done ? "Continue" : asked ? "Continue while it installs" : "Install developer tools";
  return (
    <StepPanel {...panel} title="Install Apple’s developer tools"
      hint="Welcome to Assieme. These keep versions of your project, so each agent can work on its own copy." footer={<>
        <span className="safe">{LOCAL_NOTE}</span>
        <button type="button" className="primary" onClick={done || asked ? onNext : open}>{label}</button>
      </>}>
      {done ? (
        <div className="wait-block" role="status"><b>Developer tools are installed.</b></div>
      ) : asked && (
        <div className="wait-block" role="status">
          <div className="wait-title"><Spinner /><b>Waiting for Apple’s installer</b></div>
          <p>Finish in Apple’s window. It’s a large download, so you can carry on while it installs; this updates by itself.</p>
          <button type="button" className="link" onClick={open}>Open the installer again</button>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </StepPanel>
  );
}
