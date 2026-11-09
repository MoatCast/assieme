import { useEffect, useRef, useState } from "react";
import { snapshot, type Agent, type Snapshot } from "./bridge";
import ChangeGroups from "./ChangeGroups";
import NewTeam from "./NewTeam";
import Offline from "./Offline";
import Terminal from "./Terminal";
import "./FirstRun.css";

type Open = { session: string; address: string; name: string };

const STATES: Record<string, string> = {
  starting: "Starting…", running: "Running", "needs-you": "Needs you", "not-reporting": "Not reporting",
  exited: "Exited", "already-running": "Already running",
};
const plural = (n: number) => (n === 1 ? "1 agent" : `${n} agents`);
const folderName = (cwd: string | null) => (cwd ? cwd.replace(/\/+$/, "").split("/").pop() || cwd : "Other folders");
const groupName = (a: Agent) => a.group ?? folderName(a.cwd);

function Check({ ok }: { ok: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      {ok ? (
        <path d="M3 8.5l3 3 7-7" fill="none" stroke="var(--quiet-working)" strokeWidth="1.8" strokeLinecap="round" />
      ) : (
        <circle cx="8" cy="8" r="5.5" fill="none" stroke="var(--quiet-control)" strokeWidth="1.5" />
      )}
    </svg>
  );
}

/** First run: the agents found on this Mac, grouped by project folder, and a live terminal for any of them. */
/** onSnapshot: the shell reuses this poll for its team list (one snapshot poll for the whole app). */
export default function FirstRun({ pollMs = 5000, onSnapshot, onOpenTeam }: {
  pollMs?: number;
  onSnapshot?: (s: Snapshot) => void;
  onOpenTeam?: () => void;
}) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [offline, setOffline] = useState(false);
  const [open, setOpen] = useState<Open | null>(null);
  const [creating, setCreating] = useState(false);
  const [grouping, setGrouping] = useState(false);
  const report = useRef(onSnapshot);
  report.current = onSnapshot;

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // One call at a time: the next poll is scheduled only after this one answers.
    const poll = async () => {
      try {
        const s = await snapshot();
        if (!stopped) {
          setSnap(s);
          report.current?.(s);
          setOffline(false);
        }
      } catch {
        if (!stopped) setOffline(true);
      }
      if (!stopped) timer = setTimeout(poll, pollMs);
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [pollMs]);

  const agents = snap?.agents ?? [];
  const count = (source: string) => agents.filter((a) => a.source === source).length;
  const groups = new Map<string, Agent[]>();
  // An agent of a running Assieme team is listed under that team, not found a second time by its folder.
  const inTeams = new Set((snap?.teams ?? []).filter((t) => t.state === "live").map((t) => t.cwd));
  for (const a of agents) if (!inTeams.has(a.cwd ?? undefined)) groups.set(groupName(a), [...(groups.get(groupName(a)) ?? []), a]);
  const teams = new Map<string, Snapshot["teams"]>();
  for (const t of (snap?.teams ?? []).filter((t) => t.state === "live"))
    teams.set(t.team, [...(teams.get(t.team) ?? []), t]);

  const clis = [
    { key: "claude", name: "Claude Code", found: !!snap?.clis.claude, note: plural(count("claude-code")) },
    { key: "codex", name: "Codex", found: !!snap?.clis.codex, note: plural(count("codex")) },
    { key: "herdr", name: "herdr", found: !!snap?.clis.herdr, note: "connected, so fixes can be sent" },
  ];

  // A team shows here while it starts, if its start failed, or while an agent needs you or didn't come up.
  const settled = ["running", "already-running"];
  const starting = (snap?.starting ?? []).filter(
    (t) => !t.done || t.error || t.roles.some((r) => !settled.includes(r.state)),
  );

  return (
    <div className="first-run">
      {creating && (
        <NewTeam clis={snap ? { claude: !!snap.clis.claude, codex: !!snap.clis.codex } : null} onClose={() => setCreating(false)} />
      )}
      <section className="intro">
        <div className="eyebrow">Welcome to Assieme</div>
        <h1>{!snap ? (offline ? "Can’t reach Assieme’s service." : "Looking for your agents…") : agents.length ? "We found your agents. There’s nothing to set up." : "No agents found yet."}</h1>
        <p className="lede">
          Assieme reads what Claude Code and Codex already save on this Mac. It tells you when an agent needs you, and
          helps your team waste less.
        </p>
        <div className="clis">
          {clis.map((c) => (
            <div className="cli" key={c.key} data-testid={`cli-${c.key}`}>
              <Check ok={c.found} />
              <span className="cli-name">{c.name}</span>
              <span className="cli-note">{!snap ? (offline ? "" : "checking…") : c.found ? c.note : "not found"}</span>
            </div>
          ))}
        </div>
        {offline && <Offline />}
        {onOpenTeam && (snap || !offline) && (
          <div className="open-team">
            <button type="button" onClick={onOpenTeam}>Open my team</button>
            <span>and notify me when an agent needs me</span>
          </div>
        )}
        <p className="footnote">Assieme runs on this Mac and has no server of its own. Settings, Privacy shows what it keeps.</p>
      </section>

      <section className="panel">
        {open ? (
          <div className="viewer">
            <div className="viewer-head">
              <button type="button" className="back" onClick={() => setOpen(null)}>
                ‹ All projects
              </button>
              <span className="viewer-name">{open.name}</span>
            </div>
            <Terminal key={`${open.session}:${open.address}`} session={open.session} address={open.address} />
          </div>
        ) : (
          <>
            <div className="panel-top">
              <div className="panel-label">Grouped into teams by project folder</div>
              <button type="button" className="new-team" onClick={() => setCreating(true)}>New team</button>
            </div>
            {starting.map((t) => (
              <div className="project" key={`starting-${t.team}`} data-testid={`starting-${t.team}`}>
                <div className="project-head">
                  <span className="project-name">{t.team}</span>
                  <span className="project-count">{t.error ? "" : `starting · ${plural(t.roles.length)}`}</span>
                </div>
                {t.error ? (
                  <p className="start-error">Couldn't start: {t.error}</p>
                ) : (
                  <ul className="starting">
                    {t.roles.map((r) => (
                      <li key={r.role} className={`state-${r.state}`}>
                        <span className="role">{r.role}</span>
                        <span className="state">{STATES[r.state] ?? r.state}</span>
                        {r.detail && <pre className="detail">{r.detail}</pre>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
            {[...teams].map(([name, rows]) => (
              <div className="project" key={`team-${name}`} data-testid={`team-${name}`}>
                <div className="project-head">
                  <span className="project-name">{name}</span>
                  <span className="project-count">started by Assieme · {plural(rows.length)}</span>
                </div>
                <div className="chips">
                  {rows.map((r) => (
                    <button type="button" className="chip" key={r.address}
                      onClick={() => setOpen({ session: "assieme", address: r.address, name: r.role ?? r.address })}>
                      {r.role ?? r.address}
                    </button>
                  ))}
                </div>
              </div>
            ))}
            {[...groups].map(([name, list]) => (
              <div className="project" key={name} data-testid={`project-${name}`}>
                <div className="project-head">
                  <span className="project-name">{name}</span>
                  <span className="project-count">{list.length === 1 ? "1 agent, working alone" : plural(list.length)}</span>
                </div>
                <div className="chips">
                  {list.map((a) =>
                    a.herdr_address ? (
                      <button type="button" className="chip" key={`${a.agent}-${a.herdr_address}`}
                        onClick={() => setOpen({ session: "default", address: a.herdr_address!, name: a.agent })}>
                        {a.agent}
                      </button>
                    ) : (
                      <span className="chip quiet" key={a.agent} title="No terminal to show: this agent isn't running in herdr">
                        {a.agent}
                      </span>
                    ),
                  )}
                </div>
              </div>
            ))}
          </>
        )}
        {!open && agents.length > 0 && (
          <p className="change-groups">Not right? <button type="button" onClick={() => setGrouping(true)}>Change the groups</button></p>
        )}
      </section>
      {grouping && <ChangeGroups onClose={() => setGrouping(false)} />}
    </div>
  );
}
