import { useState } from "react";
import Details, { splitDetail } from "./Details";
import Explain from "./Explain";
import type { Practices, RoleDraft, Settings } from "./bridge";
import { kindName } from "./kinds";
import { rulesLine, teamSummary, whileTheyWork } from "./newTeamSteps";
import { AlwaysOnSheet, RESEARCHER, agentCount, type Choice } from "./Practices";
import StepPanel, { type PanelProps } from "./StepPanel";

const and = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

/** New team, Start: what Assieme does for this team, the team line, and the only Start button.
 *  Paper "v3 — New team: Start, what Assieme does" (and its dark twin). */
export default function StartStep({ panel, practices, value, roles, machine, fixes, onRules, onEditAgents, onBigger, onBack,
  onStart, pending, error, notice }: {
  panel: PanelProps; practices: Practices; value: Choice; roles: RoleDraft[]; machine: string | null;
  fixes: Settings["fixes"] | null; onRules: () => void; onEditAgents: () => void; onBigger: (key: string, on: boolean) => void;
  onBack: () => void; onStart: () => void; pending: boolean; error: string;
  notice?: React.ReactNode; // an import's team still running in its old tool
}) {
  const [always, setAlways] = useState(false);
  const n = agentCount(roles, value);
  const failed = splitDetail(error);
  const research = practices.bigger.find((x) => x.id === "research-pairs");
  const bigger = (key: string, text: string, addLabel: string, added: string) => (
    <div className="bigger-line">
      <span className="grow">{value[key] === true ? added : text}</span>
      {value[key] === true
        ? <button type="button" className="link" onClick={() => onBigger(key, false)}>Remove</button>
        : <button type="button" className="link" onClick={() => onBigger(key, true)}>{addLabel}</button>}
    </div>
  );
  return (
    <StepPanel {...panel} title="What Assieme does for this team"
      hint="It starts every agent with these and keeps watching while they work. All of it is on." footer={<>
        {error
          ? <span className="error grow" role="alert">{failed.message} <Details text={failed.detail} /></span>
          : <span className="safe">You can change these any time in Settings.</span>}
        <button type="button" className="quiet" onClick={onBack}>Back</button>
        <button type="button" className="primary" disabled={pending} onClick={onStart}>{n === 1 ? "Start 1 agent" : `Start ${n} agents`}</button>
      </>}>
      {notice}
      <div className="does">
        <div className="does-row">
          <b>Before they start</b>
          <div className="does-text">
            <p>{rulesLine(practices, value)} <Explain term="team-rules" /></p>
            <button type="button" className="link" onClick={onRules}>Review team rules ›</button>
          </div>
        </div>
        <div className="does-row">
          <b>While they work</b>
          <div className="does-text"><p>{whileTheyWork(fixes)}</p></div>
        </div>
        <div className="does-row">
          <b>Always</b>
          <div className="does-text">
            <p>Each agent works on its own copy<Explain term="copy" />. No agent pushes; you merge. Permission prompts always come to you.<Explain term="permissions" /></p>
            <button type="button" className="link" onClick={() => setAlways(true)}>{`See all ${practices.basics.length} ›`}</button>
          </div>
        </div>
      </div>
      <div className="team-line">
        <span className="grow">
          <b>{`${n === 1 ? "1 agent" : `${n} agents`} on ${machine ?? "This Mac"}`}</b>
          <small>{`${teamSummary(roles)} · ${and([...new Set(roles.map((r) => kindName(r.agent)))])}`}</small>
        </span>
        <button type="button" className="link" onClick={onEditAgents}>Edit agents ›</button>
      </div>
      {roles.length >= 4 && bigger("steward", "A steward agent can settle routine questions, so only real decisions reach you.",
        "Add a steward ›", "Steward added")}
      {research && roles.some((r) => RESEARCHER.test(r.name)) &&
        bigger(research.id, research.summary, `Add ${research.title} ›`, `${research.title} added`)}
      {always && <AlwaysOnSheet basics={practices.basics} onClose={() => setAlways(false)} />}
    </StepPanel>
  );
}
