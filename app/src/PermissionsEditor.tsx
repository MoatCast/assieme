import { useState } from "react";
import { fieldOf, ruleDraft, type Level, type PermissionTable, type RoleDraft, type Rule } from "./bridge";
import useModal from "./useModal";
import "./Editors.css";

const AGENT_NAMES = { claude: "Claude Code", codex: "Codex" } as const;
export const defaultLevel = (r: Pick<RoleDraft, "checkout">): Level => (r.checkout === "read-only" ? "read-only" : "safe");
const same = (a: Rule, b: Rule) => a.decision === b.decision && a.program.join(" ") === b.program.join(" ");

/** Who it may message: the agent it reports to and the agents that report to it. */
function messages(role: RoleDraft, roles: RoleDraft[]) {
  const names = [role.parent, ...roles.filter((r) => r.parent === role.name).map((r) => r.name)].filter(Boolean) as string[];
  if (!names.length) return <>Message its team</>;
  return <>Message {names.map((n, i) => (
    <span key={n}>{i ? (i === names.length - 1 ? " and " : ", ") : ""}<code>{n}</code></span>
  ))}</>;
}

/** The Permissions editor (Paper "v2 — Permissions editor"): a level, its Allowed and Blocked lists, and the person's
 *  own rules, each shown as the program rule it becomes before it is added. Nothing is saved until Save. */
export default function PermissionsEditor({ role, roles, project, table, asker, onSave, onCancel }: {
  role: RoleDraft;
  roles: RoleDraft[];
  project: string;
  table: PermissionTable;
  /** The person's own agent that turns words into a rule: "Claude Code" or "Codex". */
  asker: string;
  onSave: (change: { level: Level; rules: Rule[] }) => void;
  onCancel: () => void;
}) {
  const [level, setLevel] = useState<Level>(role.level ?? defaultLevel(role));
  const [rules, setRules] = useState<Rule[]>(role.rules ?? []);
  const [words, setWords] = useState("");
  const [draft, setDraft] = useState<Rule | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const { ref, onKeyDown } = useModal(onCancel, []);
  const info = table.levels.find((l) => l.id === level) ?? table.levels[0];
  const recommended = defaultLevel(role);
  const projectName = project.split("/").filter(Boolean).pop() ?? "";

  const ready = !!draft && draft.words === words.trim();
  const add = async () => {
    if (ready) {
      if (!rules.some((r) => same(r, draft!))) setRules([...rules, draft!]);
      setWords("");
      setDraft(null);
      return;
    }
    if (!words.trim() || checking) return;
    setChecking(true);
    setError("");
    setDraft(null);
    try {
      setDraft(await ruleDraft(project, words.trim()));
    } catch (e) {
      setError(fieldOf(e).message);
    } finally {
      setChecking(false);
    }
  };

  const item = (key: string) => {
    if (key === "message") return messages(role, roles);
    if (key === "edit_owned" && !role.owns?.length) return <>Edit any file in its own worktree</>;
    return <>{table.items[key]?.words ?? key}</>;
  };
  const ruleRow = (r: Rule) => (
    <li key={`${r.decision} ${r.program.join(" ")}`} className="perm-rule">
      <span className="grow">{r.words || "Your rule"} <code>{r.program.join(" ")}</code></span>
      <button type="button" className="perm-remove" aria-label={`Remove the rule ${r.words || r.program.join(" ")}`}
        onClick={() => setRules(rules.filter((x) => x !== r))}>×</button>
    </li>
  );
  const list = (keys: string[], decision: Rule["decision"]) => (
    <ul className="perm-list">
      {keys.map((k) => (
        <li key={k}>
          <span className="grow">{item(k)}</span>
          {table.items[k]?.note && <span className="perm-note">{table.items[k].note}</span>}
        </li>
      ))}
      {rules.filter((r) => r.decision === decision).map(ruleRow)}
    </ul>
  );

  return (
    <div className="modal-dim">
      <div className="editor perms" role="dialog" aria-modal="true" aria-label={`What ${role.name} may do`} tabIndex={-1}
        ref={ref} onKeyDown={onKeyDown}>
        <div className="editor-head stacked">
          <div className="editor-title">
            <span className="editor-where">{projectName} · {role.name} · {AGENT_NAMES[role.agent]}</span>
            <h2>What <code>{role.name}</code> may do</h2>
          </div>
          <div className="levels" role="radiogroup" aria-label="Permission level">
            {table.levels.map((l) => (
              <button key={l.id} type="button" role="radio" aria-checked={l.id === level}
                aria-label={`${l.title}${l.id === recommended ? ", recommended" : ""}: ${l.about}`}
                className={l.id === level ? "level on" : "level"} onClick={() => setLevel(l.id)}>
                <b>{l.title}{l.id === recommended && <span className="rec"> · recommended</span>}</b>
                <span>{l.about}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="perm-body">
          <div className="perm-lists">
            <section>
              <h3 className="allowed">Allowed</h3>
              {list(info.allowed, "allow")}
            </section>
            <section>
              <h3 className="blocked">Blocked</h3>
              {list(info.blocked, "block")}
            </section>
          </div>
          <div className="perm-add">
            <label htmlFor="perm-words" className="editor-label">Add a rule</label>
            <div className="perm-add-row">
              <input id="perm-words" className="editor-input" value={words} placeholder="Don’t run the cloud launcher"
                onChange={(e) => setWords(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void add(); } }} />
              <button type="button" className="btn-secondary" disabled={!words.trim() || checking} onClick={() => void add()}>Add</button>
            </div>
            {(checking || ready) && (
              <div className="perm-becomes" role="status">
                {checking ? <span>Asking {asker} what this means in your project…</span> : <>
                  <span>Becomes</span>
                  <code>{draft!.decision} program: {draft!.program.join(" ")}</code>
                  <span className="grow" />
                  <span>Matches the program run, not text that mentions it</span>
                </>}
              </div>
            )}
            {error && <p className="editor-error">{error}</p>}
            <div className="perm-else">
              <span className="dot" aria-hidden="true" />
              <span><b>Anything else asks you first.</b> <span className="soft">It shows up in “Needs you” with Allow once, Always allow and Block.</span></span>
            </div>
          </div>
        </div>
        <div className="editor-foot">
          <span className="grow editor-small">Codex gets these as a sandbox and program rules; Claude Code as allow and deny settings.</span>
          <button type="button" className="btn-quiet" onClick={onCancel}>Cancel</button>
          <button type="button" className="btn-primary" onClick={() => onSave({ level, rules })}>Save</button>
        </div>
      </div>
    </div>
  );
}
