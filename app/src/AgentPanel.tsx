// Placeholder until Task 5: the selected agent's name and its terminal slot.
import type { Pane } from "./bridge";

export default function AgentPanel({ pane }: { team: string; pane: Pane; onChanged: () => void; onReview: (team: string, role: string) => void }) {
  return <div className="panel-pad"><h2>{pane.role}</h2><div data-terminal tabIndex={-1} /></div>;
}
