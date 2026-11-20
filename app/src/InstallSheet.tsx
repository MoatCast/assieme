import { useState } from "react";
import type { AgentKind, SetupStatus } from "./bridge";
import InstallFlow, { type FlowAction } from "./InstallFlow";
import useModal from "./useModal";

/** The sheet that installs an agent, or signs in to one: InstallFlow in a modal, for Add agent, the agent menu and
 *  remote machines. status: a poll that tells when the sign-in worked. */
export default function InstallSheet({ agent, start, status, onClose, pollMs = 2000, machine }: {
  agent: AgentKind;
  start: "install" | "signin";
  status: SetupStatus | null;
  onClose: () => void;
  pollMs?: number;
  /** Another machine (step 15): it installs there, in a terminal on that machine. */
  machine?: string;
}) {
  const [primary, setPrimary] = useState<FlowAction>(null); // changes with the flow's step: focus moves to it
  // Escape does what Not now, Cancel or Skip for now does: the flow's own quiet button.
  const { ref: dialog, onKeyDown } = useModal(() => {
    const quiet = [...(dialog.current?.querySelectorAll<HTMLButtonElement>(".sheet-actions button") ?? [])]
      .find((x) => ["Not now", "Close", "Skip for now"].includes(x.textContent ?? "") || (x.textContent === "Cancel"));
    if (quiet) quiet.click();
    else onClose();
  }, [primary?.label, primary?.busy]);
  return (
    <div className="modal-dim">
      <div className={primary?.label.startsWith("Install ") ? "modal" : "modal wide"} role="dialog" aria-modal="true" aria-labelledby="install-title" tabIndex={-1} ref={dialog}
        onKeyDown={onKeyDown}>
        <InstallFlow agent={agent} start={start} status={status} pollMs={pollMs} machine={machine} inSheet
          onDone={onClose} onSkip={onClose} onAction={setPrimary} />
      </div>
    </div>
  );
}
