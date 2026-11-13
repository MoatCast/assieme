import type { BiggerPreset, PracticeGroup, Practices, RoleDraft } from "./bridge";
import useModal from "./useModal";
import "./Settings.css";
import "./Practices.css";

/** A practice choice: group and rule keys are on unless false; bigger-team keys are on only when true. */
export type Choice = Record<string, boolean>;

export const ruleCount = (n: number) => (n === 1 ? "1 rule" : `${n} rules`);
/** Agents are told not to push only while the group and its rule are both on. */
export const pushesBlocked = (v: Choice) => v.safety !== false && v["no-push"] !== false;
// A researcher or investigator by its name, never by its job's wording: the brain's preset.RESEARCHER.
const RESEARCHER = /^(research|investigat)/i;

/** How many agents a team starts with, counting those the presets for bigger teams add. */
export function agentCount(roles: RoleDraft[], v: Choice) {
  const names = new Set(roles.map((r) => r.name));
  const critics = v["research-pairs"] === true
    ? roles.filter((r) => RESEARCHER.test(r.name) && !names.has(`${r.name}-critic`)).length : 0;
  return roles.length + critics + (v.steward === true && !names.has("steward") ? 1 : 0);
}

function Toggle({ label, on, disabled, onChange }: { label: string; on: boolean; disabled?: boolean; onChange: (on: boolean) => void }) {
  return <input type="checkbox" role="switch" className="pswitch" aria-label={label} checked={on} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />;
}

/** The chevron that opens a group's rules. */
export function Chevron({ title, onClick }: { title: string; onClick: () => void }) {
  return (
    <button type="button" className="pchevron" aria-label={`${title}: each rule`} onClick={onClick}>
      <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M5 3l4 4-4 4" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
    </button>
  );
}

/** One group's rules, each with its own switch. Paper "v2 — Practices: group detail", left card. */
export function GroupSheet({ group, value, onChange, onClose }: { group: PracticeGroup; value: Choice; onChange: (key: string, on: boolean) => void; onClose: () => void }) {
  const { ref, onKeyDown } = useModal(onClose, []);
  const groupOn = value[group.id] !== false;
  return (
    <div className="modal-dim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="pcard group-sheet" role="dialog" aria-label={group.title} ref={ref} tabIndex={-1} onKeyDown={onKeyDown}>
        <div className="pcard-head">
          <span className="pkicker">Recommended setup</span>
          <div className="pcard-title">
            <h2>{group.title}</h2>
            <Toggle label={group.title} on={groupOn} onChange={(on) => onChange(group.id, on)} />
          </div>
          <p>{group.detail}</p>
        </div>
        <div className="prules">
          {group.rules.map((r) => (
            <div className="prule" key={r.id}>
              <div className="grow">
                <b>{r.title}</b>
                <span>{r.detail}</span>
                {r.note && <small>{r.note}</small>}
              </div>
              <Toggle label={r.title} on={groupOn && value[r.id] !== false} disabled={!groupOn} onChange={(on) => onChange(r.id, on)} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** The ten basics. Paper "v2 — Practices: group detail", right card. */
export function AlwaysOnSheet({ basics, onClose }: { basics: Practices["basics"]; onClose: () => void }) {
  const { ref, onKeyDown } = useModal(onClose, []);
  return (
    <div className="modal-dim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="pcard always-on" role="dialog" aria-label="Always on" ref={ref} tabIndex={-1} onKeyDown={onKeyDown}>
        <div className="pcard-head">
          <div className="pcard-title start">
            <Shield />
            <h2>Always on</h2>
          </div>
          <p>Built into how Assieme runs every team. They can’t be turned off.</p>
        </div>
        <ol className="pbasics">
          {basics.map((b, i) => <li key={b.title}><span>{i + 1}</span>{b.title}</li>)}
        </ol>
      </div>
    </div>
  );
}

export function Shield() {
  return <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5l5.5 2v4c0 3.5-2.4 6-5.5 7-3.1-1-5.5-3.5-5.5-7v-4z" fill="none" stroke="var(--quiet-working)" strokeWidth="1.6" /></svg>;
}

/** Steward agent and Research pairs. Paper "v2 — New team: recommended setup" and "v2 — Settings: team practices, bigger teams". */
export function BiggerTeams({ bigger, value, note, onChange }: { bigger: BiggerPreset[]; value: Choice; note: string; onChange: (key: string, on: boolean) => void }) {
  return (
    <div className="bigger">
      <span className="bigger-label"><b>For bigger teams</b> · {note}</span>
      <div className="bigger-cards">
        {bigger.map((b) => (
          <label className="bigger-card" key={b.id} title={b.detail}>
            <span className="grow">
              <b>{b.title}</b>
              <small>{b.summary}</small>
            </span>
            <Toggle label={b.title} on={value[b.id] === true} onChange={(on) => onChange(b.id, on)} />
          </label>
        ))}
      </div>
    </div>
  );
}
