import { useEffect, useState } from "react";
import type { AgentKind, SetupStatus } from "./bridge";
import ConnectSheet from "./ConnectSheet";
import InstallFlow, { type FlowAction } from "./InstallFlow";
import { AGENT_KINDS } from "./kinds";
import StepPanel, { type PanelProps } from "./StepPanel";
import { LOCAL_NOTE } from "./ToolsStep";

const PLAN: Record<AgentKind, string> = {
  claude: "Anthropic · paid Claude plan", codex: "OpenAI · ChatGPT, limits on the free plan",
  antigravity: "Google · works on the free plan", gemini: "Google · paid key only",
  opencode: "Open source · your own model keys", cursor: "Anysphere · Cursor plan",
};

/** First run, Agent: which agent the person pays for. Choosing one shows how it installs, inside the panel; it moves on
 *  once that agent is signed in (the end of a deliberate install), or on Skip for now.
 *  Paper "v3 — First run: Agent (Claude Code chosen)". */
export function AgentStep({ panel, status, onNext, onBack }: { panel: PanelProps; status: SetupStatus | null; onNext: () => void; onBack: () => void }) {
  const [chosen, setChosen] = useState<AgentKind | null>(null);
  const [action, setAction] = useState<FlowAction>(null);
  const ready = chosen && status?.agents[chosen]?.signed_in;
  const connect = ready && chosen === "antigravity" && !status?.agents.antigravity?.connected;
  useEffect(() => { if (ready && !connect) onNext(); }, [ready, connect]); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (k: AgentKind) => {
    setAction(null);
    setChosen(k);
  };
  return (
    <StepPanel {...panel} title="Which agent do you pay for?"
      hint="Assieme runs it for you. Choosing one shows how it installs; it installs only when you say so." footer={<>
        <span className="safe">{LOCAL_NOTE}</span>
        <button type="button" className="quiet" onClick={onBack}>Back</button>
        {chosen && action && <button type="button" className="primary" disabled={action.busy} onClick={action.act}>{action.label}</button>}
      </>}>
      <div className="kinds">
        {AGENT_KINDS.map((k) => chosen === k.id ? (
          <div className="kind-card" key={k.id}>
            <div className="kind-head"><b>{k.name}</b><small>{PLAN[k.id]}</small></div>
            <InstallFlow key={k.id} agent={k.id} start={status?.agents[k.id]?.installed ? "signin" : "install"} status={status}
              onAction={setAction} onDone={onNext} onSkip={onNext} />
          </div>
        ) : (
          <button type="button" className="option" key={k.id} onClick={() => pick(k.id)}>
            <b className="grow">{k.name}</b><small>{PLAN[k.id]}</small>
          </button>
        ))}
      </div>
      {connect && <ConnectSheet onConnected={onNext} onClose={onNext} />}
    </StepPanel>
  );
}
