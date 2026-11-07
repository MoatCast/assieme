import { useEffect, useState } from "react";
import { snapshot, type Agent, type Snapshot } from "./bridge";
import Terminal from "./Terminal";
import "./FirstRun.css";

type Open = { session: string; address: string; name: string };

const plural = (n: number) => (n === 1 ? "1 agent" : `${n} agents`);
const folderName = (cwd: string | null) => (cwd ? cwd.replace(/\/+$/, "").split("/").pop() || cwd : "Other folders");

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
export default function FirstRun({ pollMs = 5000 }: { pollMs?: number }) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [offline, setOffline] = useState(false);
  const [open, setOpen] = useState<Open | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // One call at a time: the next poll is scheduled only after this one answers.
    const poll = async () => {
      try {
        const s = await snapshot();
        if (!stopped) {
          setSnap(s);
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
  for (const a of agents) groups.set(folderName(a.cwd), [...(groups.get(folderName(a.cwd)) ?? []), a]);
  const teams = new Map<string, Snapshot["teams"]>();
  for (const t of (snap?.teams ?? []).filter((t) => t.state === "live"))
    teams.set(t.team, [...(teams.get(t.team) ?? []), t]);

  const clis = [
    { key: "claude", name: "Claude Code", found: !!snap?.clis.claude, note: plural(count("claude")) },
    { key: "codex", name: "Codex", found: !!snap?.clis.codex, note: plural(count("codex")) },
    { key: "herdr", name: "herdr", found: !!snap?.clis.herdr, note: "connected, so fixes can be sent" },
  ];

  return (
    <div className="first-run">
      <section className="intro">
        <div className="eyebrow">Welcome to Assieme</div>
        <h1>{agents.length ? "We found your agents. There’s nothing to set up." : "No agents found yet."}</h1>
        <p className="lede">
          Assieme reads what Claude Code and Codex already save on this Mac. It tells you when an agent needs you, and
          helps your team waste less.
        </p>
        <div className="clis">
          {clis.map((c) => (
            <div className="cli" key={c.key} data-testid={`cli-${c.key}`}>
              <Check ok={c.found} />
              <span className="cli-name">{c.name}</span>
              <span className="cli-note">{c.found ? c.note : "not found"}</span>
            </div>
          ))}
        </div>
        {offline && <div className="offline">Reconnecting…</div>}
        <p className="footnote">Everything stays on this Mac. Agents’ messages stay hidden unless you choose to show them.</p>
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
            <div className="panel-label">Grouped into teams by project folder</div>
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
      </section>
    </div>
  );
}
