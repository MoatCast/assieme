import StepPanel, { type PanelProps } from "./StepPanel";

/** New team, Task: one line the agents start on. It can be skipped; then agents start and wait. */
export default function TaskStep({ panel, task, onTask, onNext, onSkip, onBack }: {
  panel: PanelProps; task: string; onTask: (t: string) => void; onNext: () => void; onSkip: () => void; onBack: () => void;
}) {
  const ready = !!task.trim();
  return (
    <StepPanel {...panel} title="What should the team work on?" hint="Agents start on this. It becomes the goal in the team brief." footer={<>
      <span className="safe" />
      <button type="button" className="quiet" onClick={onBack}>Back</button>
      <button type="button" className="link" onClick={onSkip}>Skip</button>
      <button type="button" className="primary" disabled={!ready} onClick={onNext}>Continue</button>
    </>}>
      <input className="box" aria-labelledby="step-title" value={task} placeholder="Add rate limiting to the login endpoint"
        onChange={(e) => onTask(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ready && onNext()} />
    </StepPanel>
  );
}
