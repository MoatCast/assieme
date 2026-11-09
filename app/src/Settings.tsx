import { useEffect, useState } from "react";
import {
  assistantsConnect, assistantsDisconnect, assistantsStatus, getSettings, listTeams, quietPairs, quietSet, serviceInstall, serviceRunning, setSetting, stopTeam, teamPresets,
  type Assistants, type Practices, type QuietPair, type Settings as Values, type TeamListing,
} from "./bridge";
import ChangeGroups from "./ChangeGroups";
import "./Settings.css";

const SECTIONS = [
  ["general", "General"], ["fixes", "Fixes"], ["practices", "Team practices"],
  ["notifications", "Notifications"], ["teams", "Teams"], ["privacy", "Privacy"],
] as const;
type Section = (typeof SECTIONS)[number][0];

const FIXES = [
  ["nudge", "Ask stuck agents to try another way", "When an agent repeats the same thing or goes quiet for 15 minutes."],
  ["compact", "Free up memory between tasks", "When a team agent is idle and its memory is over 75% full."],
  ["tell", "Explain blocked commands", "When an agent keeps retrying something your rules don’t allow."],
] as const;
const rules = (n: number) => (n === 1 ? "1 rule" : `${n} rules`);
const plural = (n: number) => (n === 1 ? "1 agent" : `${n} agents`);

function Switch({ label, on, disabled, onChange }: { label: string; on: boolean; disabled?: boolean; onChange: (on: boolean) => void }) {
  return <input type="checkbox" role="switch" className="switch" aria-label={label} checked={on} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />;
}

function Row({ title, sub, children }: { title: string; sub?: string; children?: React.ReactNode }) {
  return (
    <div className="set-row">
      <div className="set-text"><b>{title}</b>{sub && <span>{sub}</span>}</div>
      {children}
    </div>
  );
}

function Head({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="set-head"><h1>{title}</h1><p>{children}</p></div>;
}

function YourAssistants() {
  const [got, setGot] = useState<Assistants | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (call: () => Promise<Assistants>) => {
    setBusy(true);
    try {
      const a = await call();
      setGot(a);
      setError(a.problems.join(" "));
    } catch (e) {
      setError(String(e));
    }
    setBusy(false);
  };
  useEffect(() => { void run(assistantsStatus); }, []);
  const clients = (got?.clients ?? []).filter((c) => !c.blocked); // the Claude app can't be connected while it's open
  const blocked = got?.clients.some((c) => c.blocked) ?? false;
  const labels = clients.map((c) => c.label);
  const names = labels.length > 1 ? `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}` : labels[0] ?? "";
  const connected = clients.length > 0 && clients.every((c) => c.connected);
  const quit = blocked ? " To add the Claude app, quit it, then come back here." : "";
  const sub = !got ? "Let your assistants see your teams and help design new ones."
    : !clients.length && !blocked ? "Install Claude Code or Codex, and they can see your teams and help design new ones."
    : !clients.length ? "To let the Claude app see your teams and help design new ones, quit it, then come back here."
    : connected ? `${names} can see your teams and help design new ones.${quit}`
    : `Let ${names} see your teams and help design new ones. They ask you before changing anything.${quit}`;
  return (
    <>
      <Row title="Your assistants" sub={sub}>
        {connected && <span className="state good"><i />Connected</span>}
        {connected && <button type="button" className="btn-small" disabled={busy} onClick={() => run(assistantsDisconnect)}>Disconnect</button>}
        {got && clients.length > 0 && !connected && <button type="button" className="btn-small primary" disabled={busy} onClick={() => run(assistantsConnect)}>Connect</button>}
      </Row>
      {error && <p className="set-error assistants-error">{error}</p>}
    </>
  );
}

function General({ onShowFirstRun }: { onShowFirstRun: () => void }) {
  const [running, setRunning] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  const check = () => serviceRunning().then(setRunning).catch(() => setRunning(false));
  useEffect(() => { void check(); }, []);
  const start = async () => {
    try {
      await serviceInstall();
      setError("");
    } catch (e) {
      setError(String(e));
    }
    void check();
  };
  return (
    <>
      <Head title="General">How Assieme runs on this Mac. Agents keep working when you close this window.</Head>
      <div className="set-list">
        <Row title="Background service" sub={running === false ? "Your agents keep running, but nothing is watching them: no fixes, no notifications." : "Watches your agents and carries out fixes, even with this window closed."}>
          {running !== null && <span className={running ? "state good" : "state bad"}><i />{running ? "Running" : "Stopped"}</span>}
          {running === false && <button type="button" className="btn-small primary" onClick={start}>Start it</button>}
        </Row>
        <Row title="First run" sub="See the agents and projects Assieme found on this Mac.">
          <button type="button" className="btn-small" onClick={onShowFirstRun}>Show First run</button>
        </Row>
        <YourAssistants />
      </div>
      {error && <p className="set-error">{error}</p>}
    </>
  );
}

function Fixes({ values, set, team }: { values: Values; set: (k: string, v: unknown) => void; team: string | null }) {
  const [pairs, setPairs] = useState<QuietPair[]>([]);
  const [choices, setChoices] = useState<{ role: string; cwd: string }[]>([]);
  const [adding, setAdding] = useState(false);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const load = () => quietPairs(team).then((r) => { setPairs(r.pairs); setChoices(r.agents); }).catch(() => {});
  useEffect(() => { void load(); }, [team]);
  const name = (cwd: string) => choices.find((c) => c.cwd === cwd)?.role ?? "";
  const remove = async (p: QuietPair) => {
    await quietSet({ from_cwd: p.from_cwd, to_cwd: p.to_cwd, label: p.label, on: false });
    void load();
  };
  const add = async () => {
    if (!from || !to || from === to) return;
    await quietSet({ from_cwd: from, to_cwd: to, label: `${name(from)} to ${name(to)}`, on: true });
    setAdding(false);
    setFrom("");
    setTo("");
    void load();
  };
  return (
    <>
      <Head title="Fixes">Assieme always asks first. Turn a fix on here and it will do it for you, and tell you after in “Done for you”.</Head>
      <div className="set-list">
        {FIXES.map(([id, title, sub]) => {
          const auto = values.fixes[id] === "auto";
          return (
            <Row key={id} title={title} sub={sub}>
              <span className={auto ? "mode auto" : "mode"}>{auto ? "Automatic" : "Ask first"}</span>
              <Switch label={title} on={auto} onChange={(on) => set(`fixes.${id}`, on ? "auto" : "ask")} />
            </Row>
          );
        })}
      </div>
      <div className="set-block">
        <div className="set-sub">
          <h2>Notes that can wait</h2>
          <p>Routine notes between these agents are held until the receiver’s next turn, so they don’t wake it for nothing. Questions always go straight through.</p>
        </div>
        <div className="set-list">
          {pairs.map((p) => (
            <div className="pair" key={`${p.from_cwd}>${p.to_cwd}`}>
              <code>{p.label.replace(/ (to|->) /, " → ")}</code>
              <span className="grow">{p.saved ? `saved about ${Math.round(p.saved * 100)}% today` : ""}</span>
              <button type="button" className="text" onClick={() => remove(p)}>Remove</button>
            </div>
          ))}
          {adding ? (
            <div className="pair add">
              <select aria-label="From" value={from} onChange={(e) => setFrom(e.target.value)}>
                <option value="">From…</option>
                {choices.map((c) => <option key={c.cwd} value={c.cwd}>{c.role}</option>)}
              </select>
              <span aria-hidden="true">→</span>
              <select aria-label="To" value={to} onChange={(e) => setTo(e.target.value)}>
                <option value="">To…</option>
                {choices.filter((c) => c.cwd !== from).map((c) => <option key={c.cwd} value={c.cwd}>{c.role}</option>)}
              </select>
              <span className="grow" />
              <button type="button" className="text" onClick={() => setAdding(false)}>Cancel</button>
              <button type="button" className="btn-small" disabled={!from || !to} onClick={add}>Add</button>
            </div>
          ) : (
            <button type="button" className="pair add-pair" disabled={!choices.length} onClick={() => setAdding(true)}>
              <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M7 2v10M2 7h10" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.5" strokeLinecap="round" /></svg>
              {choices.length ? "Add a pair" : "Start a team in Assieme to add a pair"}
            </button>
          )}
        </div>
      </div>
    </>
  );
}

function PracticesPane({ values, set }: { values: Values; set: (k: string, v: unknown) => void }) {
  const [practices, setPractices] = useState<Practices | null>(null);
  const [basics, setBasics] = useState(false);
  useEffect(() => { void teamPresets().then((p) => setPractices(p.practices)).catch(() => {}); }, []);
  return (
    <>
      <Head title="Team practices">Habits from teams that ran for weeks. New teams start with all of them; turn any off here. Changes reach each agent at its next task.</Head>
      {practices && (
        <>
          <div className="set-list">
            {practices.groups.map((g) => (
              <Row key={g.id} title={g.title} sub={g.summary}>
                <span className="mode">{rules(g.rules.length)}</span>
                <Switch label={g.title} on={values.practices[g.id] !== false} onChange={(on) => set(`practices.${g.id}`, on)} />
              </Row>
            ))}
          </div>
          <div className="basics">
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5l5 2v4c0 3.2-2.2 5.6-5 7-2.8-1.4-5-3.8-5-7v-4z" fill="none" stroke="var(--quiet-working)" strokeWidth="1.4" strokeLinejoin="round" /></svg>
            <span className="grow">Plus <b>{practices.basics.length} basics that are always on</b>, like never answering an agent’s permission prompt for it.</span>
            <button type="button" className="text strong" onClick={() => setBasics(!basics)}>See all</button>
          </div>
          {basics && (
            <ul className="basic-list">
              {practices.basics.map((b) => <li key={b.title}><b>{b.title}</b> {b.detail}</li>)}
            </ul>
          )}
        </>
      )}
    </>
  );
}

function Notifications({ values, set }: { values: Values; set: (k: string, v: unknown) => void }) {
  return (
    <>
      <Head title="Notifications">A Mac notification when something needs you, or when work finishes or stops. Routine progress never notifies.</Head>
      <div className="set-list">
        <Row title="When an agent needs you" sub="A question, a permission prompt or a plan to approve.">
          <Switch label="When an agent needs you" on={values.notify.needs_you} onChange={(on) => set("notify.needs_you", on)} />
        </Row>
        <Row title="When a team stalls" sub="Agents are waiting on each other and nothing is moving.">
          <Switch label="When a team stalls" on={values.notify.stalls} onChange={(on) => set("notify.stalls", on)} />
        </Row>
        <Row title="When work finishes or stops" sub="Ready to review, or an agent stopped.">
          <Switch label="When work finishes or stops" on={values.notify.finished} onChange={(on) => set("notify.finished", on)} />
        </Row>
      </div>
    </>
  );
}

function Teams({ onChangeGroups }: { onChangeGroups: () => void }) {
  const [teams, setTeams] = useState<TeamListing[] | null>(null);
  const [stopping, setStopping] = useState<TeamListing | null>(null);
  const [error, setError] = useState("");
  const load = () => listTeams().then((r) => setTeams(r.teams)).catch((e) => setError(String(e)));
  useEffect(() => { void load(); }, []);
  const stop = async () => {
    try {
      await stopTeam(stopping!.name);
    } catch (e) {
      setError(String(e));
    }
    setStopping(null);
    void load();
  };
  return (
    <>
      <Head title="Teams">Every team Assieme can see. Stopping a team ends its agents; their branches stay.</Head>
      {teams && (teams.length ? (
        <div className="set-list">
          {teams.map((t) => (
            <Row key={t.name} title={t.name} sub={t.kind === "assieme" ? `Started in Assieme · ${plural(t.agents)} running` : `Found on this Mac · ${t.agents === 1 ? "1 agent, working alone" : plural(t.agents)}`}>
              {t.kind === "assieme" && <button type="button" className="btn-small" aria-label={`Stop ${t.name}`} onClick={() => setStopping(t)}>Stop</button>}
            </Row>
          ))}
        </div>
      ) : <p className="set-empty">No teams are running.</p>)}
      <div className="set-list note-row">
        <span className="grow">Agents you started yourself are grouped by project folder. Not right?</span>
        <button type="button" className="btn-small" onClick={onChangeGroups}>Change the groups</button>
      </div>
      {error && <p className="set-error">{error}</p>}
      {stopping && (
        <div className="modal-dim">
          <div className="modal small" role="dialog" aria-label={`Stop ${stopping.name}?`}>
            <div className="modal-head">
              <h2>Stop {stopping.name}?</h2>
              <p className="big">This ends its {plural(stopping.agents)} and anything they’re doing right now. Their branches and files stay, so no committed or saved work is lost.</p>
            </div>
            <div className="sheet-actions">
              <button type="button" className="btn-quiet" onClick={() => setStopping(null)}>Keep it running</button>
              <button type="button" className="btn-primary" onClick={stop}>Stop the team</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function Privacy({ values, set }: { values: Values; set: (k: string, v: unknown) => void }) {
  return (
    <>
      <Head title="Privacy">Assieme runs on this Mac and has no server of its own. It reads the logs Claude Code and Codex already save. Suggested answers go through your own Claude Code or Codex, so your agent’s question goes wherever those normally send your work.</Head>
      <div className="set-list">
        <Row title="Show commands in full" sub="Off, you see program and file names only. Commands can hold secrets.">
          <Switch label="Show commands in full" on={values.show_commands} onChange={(on) => set("show_commands", on)} />
        </Row>
      </div>
      <div className="set-block">
        <div className="set-sub"><h2>What Assieme keeps</h2></div>
        <div className="set-list">
          <Row title="Activity" sub="Which agent, what was done and why. Not what your agents said or asked." />
          <Row title="Notes that can wait" sub="Held as written until they’re delivered, then removed." />
          <Row title="Suggested answers" sub="Assieme holds them in memory for a day. Your Claude Code or Codex logs the question as it logs any session." />
          <Row title="Agent reports and drafts" sub="What an agent said is blocking it, until its next turn. A team an assistant drafted, until it opens in New team." />
          <Row title="Your assistants" sub="Once connected, an assistant can read your agents’ terminals when asked. What it reads goes wherever it normally sends your work." />
        </div>
      </div>
    </>
  );
}

/** Settings: a sheet over the whole window, from the gear. */
export default function Settings({ team, onClose, onShowFirstRun }: { team: string | null; onClose: () => void; onShowFirstRun: () => void }) {
  const [section, setSection] = useState<Section>("general");
  const [values, setValues] = useState<Values | null>(null);
  const [grouping, setGrouping] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { void getSettings().then(setValues).catch((e) => setError(String(e))); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !grouping) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, grouping]);

  const set = (key: string, value: unknown) => {
    void setSetting(key, value).then((v) => { setValues(v); setError(""); }).catch((e) => setError(String(e)));
  };

  return (
    <div className="settings" role="dialog" aria-label="Settings">
      <div className="settings-drag" data-tauri-drag-region />
      <nav className="settings-nav">
        <div className="settings-sections">
          {SECTIONS.map(([id, label]) => (
            <button key={id} type="button" className={section === id ? "on" : ""} aria-current={section === id} onClick={() => setSection(id)}>{label}</button>
          ))}
        </div>
        <button type="button" className="settings-back" onClick={onClose}>‹ Back</button>
      </nav>
      <div className="settings-ground">
        <div className="settings-sheet">
          {error && <p className="set-error">{error}</p>}
          {section === "general" && <General onShowFirstRun={onShowFirstRun} />}
          {section === "fixes" && values && <Fixes values={values} set={set} team={team} />}
          {section === "practices" && values && <PracticesPane values={values} set={set} />}
          {section === "notifications" && values && <Notifications values={values} set={set} />}
          {section === "teams" && <Teams onChangeGroups={() => setGrouping(true)} />}
          {section === "privacy" && values && <Privacy values={values} set={set} />}
        </div>
      </div>
      {grouping && <ChangeGroups onClose={() => setGrouping(false)} />}
    </div>
  );
}
