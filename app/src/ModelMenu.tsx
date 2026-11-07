import type { ModelOptions, RoleDraft } from "./bridge";

const EFFORT: Record<string, string> = {
  low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max", ultra: "Ultra",
};
const effortLabel = (e: string) => EFFORT[e] ?? e;

/** The row's button text: "Default", or what was picked ("Opus · High"). */
export function choiceLabel(role: RoleDraft, options: ModelOptions | null): string {
  const model = options?.[role.agent].models.find((m) => m.id === role.model)?.label ?? role.model;
  const parts = [model, role.effort && effortLabel(role.effort)].filter(Boolean);
  return parts.length ? parts.join(" · ") : "Default";
}

/** Model and thinking effort for one agent. Default leaves the agent's own setting alone. */
export default function ModelMenu({ role, options, onChange }: {
  role: RoleDraft;
  options: ModelOptions;
  onChange: (change: { model: string | null; effort: string | null }) => void;
}) {
  const cli = options[role.agent];
  const levels = (model: string | null) => cli.models.find((m) => m.id === model)?.efforts ?? cli.efforts;
  const setModel = (model: string | null) =>
    // An effort the new model doesn't support goes back to Default.
    onChange({ model, effort: role.effort && levels(model).includes(role.effort) ? role.effort : null });

  return (
    <div className="model-menu">
      <label>
        <span>Model</span>
        <select aria-label="Model" value={role.model ?? ""} onChange={(e) => setModel(e.target.value || null)}>
          <option value="">Default</option>
          {cli.models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
        </select>
      </label>
      <label>
        <span>Thinking effort</span>
        <select aria-label="Thinking effort" value={role.effort ?? ""}
          onChange={(e) => onChange({ model: role.model ?? null, effort: e.target.value || null })}>
          <option value="">Default</option>
          {levels(role.model ?? null).map((e) => <option key={e} value={e}>{effortLabel(e)}</option>)}
        </select>
      </label>
    </div>
  );
}
