import { useEffect, useRef, useState } from "react";
import type { AgentKind, Preset, Suggestion } from "./bridge";
import HelpMeChoose from "./HelpMeChoose";
import { kindName } from "./kinds";
import { presetNote } from "./newTeamSteps";
import StepPanel, { useClickGuard, type PanelProps } from "./StepPanel";

const count = (n: number) => (n === 1 ? "1 agent" : `${n} agents`);

/** New team, Team: who works on it. Same as last time, the built-ins and Help me choose are push buttons of one size;
 *  picking one moves on. Paper "v3 — New team: Team". */
export default function TeamStep({ panel, presets, last, installed, task, project, machine, onPick, onSuggested, onBringOver,
  onInstall, onBack }: {
  panel: PanelProps; presets: Preset[]; last: Preset | null; installed: AgentKind[]; task: string; project: string;
  machine: string | null; onPick: (p: Preset) => void; onSuggested: (s: Suggestion) => void; onBringOver: () => void;
  onInstall: (k: AgentKind) => void; onBack: () => void;
}) {
  const [helping, setHelping] = useState(false);
  const guard = useClickGuard(panel.at);
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (last) first.current?.focus(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (p: Preset) => { if (!guard()) onPick(p); };
  const option = (p: Preset, isLast: boolean) => (
    <button type="button" className="option" key={p.id} ref={isLast ? first : undefined} onClick={() => pick(p)}>
      <span className="text"><b>{p.label}</b><small>{presetNote(p, isLast)}</small></span>
      <span className="count">{count(p.roles.length)}</span>
    </button>
  );

  return (
    <StepPanel {...panel} title="Who should work on it?" hint="Choosing a team moves to the next step. You can change any agent before you start."
      footer={<><span className="safe" /><button type="button" className="quiet" onClick={onBack}>Back</button></>}>
      {!installed.length ? (
        <div className="assieme-block">
          <strong>Install an agent to continue</strong>
          <div className="pickers">
            <button type="button" className="option" onClick={() => onInstall("claude")}><b>Install Claude Code</b></button>
            <button type="button" className="option" onClick={() => onInstall("codex")}><b>Install Codex</b></button>
          </div>
        </div>
      ) : helping ? (
        <HelpMeChoose machine={machine || undefined} project={project} agent={kindName(installed[0])} task={task}
          onCancel={() => setHelping(false)} onDone={onSuggested} />
      ) : (
        <>
          {last && option(last, true)}
          {presets.map((p) => option(p, false))}
          <button type="button" className="option" onClick={() => !guard() && setHelping(true)}>
            <span className="text"><b>Help me choose</b><small>Assieme reads your task and suggests a team for you to confirm.</small></span>
            <span className="count">Not sure?</span>
          </button>
          <p className="bring">
            <span>Moving from another tool?</span>
            <button type="button" className="link" onClick={onBringOver}>Bring your team over ›</button>
          </p>
        </>
      )}
    </StepPanel>
  );
}
