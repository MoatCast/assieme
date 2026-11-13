import { useEffect, useRef, useState, type ReactNode } from "react";
import { paneAct, reviewWaiting, teamMap, tipApply, type ActivityRow, type Pane, type PaneButton, type ReviewWaiting, type Starting, type TeamMap } from "./bridge";
import { UndoButton } from "./Activity";
import AgentDetail from "./AgentDetail";
import DailyLimits from "./DailyLimits";
import Offline from "./Offline";
import { AgentsMap, GroupsMap } from "./TeamMap";
import { ago } from "./Terminals";
import Unreachable, { awayTitle } from "./Unreachable";
import "./Team.css";

export const SAYS: Partial<Record<Pane["state"], string>> = {
  asking: "is asking you", stuck: "seems stuck", waiting: "has work waiting",
};

const UNSETTLED = ["needs-you", "not-reporting", "exited"];

export function tokens(n: number) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M`; // 10M, as Paper writes it
  if (n >= 1e3) return `${Math.round(n / 1e3)}K`;
  return String(n);
}

/** One fix's buttons, used on the card and in agent detail. A button works once. */
export function FixButtons({ team, pane, onChanged, onOpenTerminal, more }: {
  team: string;
  pane: Pick<Pane, "role" | "fix" | "reachable">;
  onChanged: () => void;
  onOpenTerminal: (role: string) => void;
  /** Shown in place of "Open terminal". */
  more?: ReactNode;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [pressed, setPressed] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const fix = pane.fix;
  const press = async (b: PaneButton) => {
    if (b.kind === "reply") return onOpenTerminal(pane.role);
    if (!fix || pressed === fix.id || pending) return;
    setPending(b.id);
    try {
      const r = await paneAct({ team, role: pane.role, fix: fix.id, button: b.id });
      setNote(r.ok ? "" : r.detail);
      if (r.ok) setPressed(fix.id);
      onChanged();
    } catch (e) {
      setNote(String(e));
    } finally {
      setPending(null);
    }
  };
  const buttons = fix?.buttons ?? [];
  return (
    <>
      {note && <p className="card-note">{note}</p>}
      <div className="card-buttons">
        {buttons.map((b, i) => (
          <button key={b.id} type="button" disabled={pending === b.id || (b.kind !== "reply" && pressed === fix?.id)}
            className={b.kind === "reply" ? "btn-quiet" : i === 0 ? "btn-primary" : "btn-secondary"}
            onClick={() => press(b)}>{b.kind === "reply" ? "Write a reply" : b.label}</button>
        ))}
        {more ?? (pane.reachable !== false && !buttons.some((b) => b.kind === "reply") && (
          <button type="button" className="btn-quiet" onClick={() => onOpenTerminal(pane.role)}>Open terminal</button>
        ))}
      </div>
    </>
  );
}

function Card({ team, pane, onChanged, onOpenTerminal, onChangeLimit }: {
  team: string;
  pane: Pane;
  onChanged: () => void;
  onOpenTerminal: (role: string) => void;
  onChangeLimit: () => void;
}) {
  if (pane.fix?.budget) {
    // One card for a pause at a daily limit, whoever it holds: Paper "v2 — Team: paused at the limit".
    return (
      <div className="card needs" data-testid={`card-paused-${pane.fix.budget}`}>
        <div className="card-head">
          <span className="dot" />
          <span className="card-name">{pane.fix.who}</span>
          <span className="card-says">{["is paused", ago(pane.since_s)].filter(Boolean).join(" · ")}</span>
        </div>
        <div className="card-body">
          <div className="card-title">{pane.fix.text}</div>
          <p className="card-sub">{pane.fix.detail}</p>
          <FixButtons team={team} pane={pane} onChanged={onChanged} onOpenTerminal={onOpenTerminal}
            more={<button type="button" className="btn-secondary" onClick={onChangeLimit}>Change limit</button>} />
        </div>
      </div>
    );
  }
  const asking = pane.state === "asking";
  const said = SAYS[pane.state];
  const title = asking ? `“${pane.fix!.text}”` : pane.detail || pane.fix!.text;
  return (
    <div className={`card ${said ? "needs" : ""}`} data-testid={`card-${pane.role}`}>
      <div className="card-head">
        <span className="dot" />
        <span className="card-name">{pane.role}</span>
        <span className="card-says">{[said ?? "could use a hand", ago(pane.since_s)].filter(Boolean).join(" · ")}</span>
      </div>
      <div className="card-body">
        <div className="card-title">{title}</div>
        {asking && pane.detail && <p className="card-sub">{pane.detail}</p>}
        {pane.command && <pre className="card-code">{pane.command}</pre>}
        {pane.reachable === false && <p className="card-sub">It isn’t in a terminal Assieme can reach, so this one’s yours to do there.</p>}
        <FixButtons team={team} pane={pane} onChanged={onChanged} onOpenTerminal={onOpenTerminal} />
      </div>
    </div>
  );
}

function DoneRow({ row, onChanged }: { row: ActivityRow; onChanged: () => void }) {
  const [note, setNote] = useState("");
  return (
    <div className="done-row">
      <span className="done-at">{row.at}</span>
      <span className="done-title">{note || row.title}</span>
      {row.undo && <UndoButton row={row} className="link" onDone={onChanged} onNote={setNote} />}
    </div>
  );
}

/** The Team tab: the map of who wakes whom, and what needs you. */
export default function Team({ team, pollMs = 5000, starting = [], onNewTeam, onOpenTerminal, onSeeAll, onReview }: {
  team: string | null;
  pollMs?: number;
  starting?: Starting[];
  onNewTeam: () => void;
  onOpenTerminal: (role: string) => void;
  onReview: (team: string, role: string) => void;
  onSeeAll: () => void;
}) {
  const [map, setMap] = useState<TeamMap | null>(null);
  const [waiting, setWaiting] = useState<ReviewWaiting[]>([]);
  const [offline, setOffline] = useState(false);
  const [agent, setAgent] = useState<string | null>(null);
  const [opened, setOpened] = useState<string | null>(null);
  const [tipNote, setTipNote] = useState("");
  const [limits, setLimits] = useState(false);
  const refresh = useRef<() => void>(() => {});

  useEffect(() => {
    if (!team) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let busy = false;
    const poll = async () => {
      if (busy) return;
      busy = true;
      clearTimeout(timer);
      try {
        const [m, w] = await Promise.all([teamMap(team), reviewWaiting().catch(() => [] as ReviewWaiting[])]);
        if (!stopped) {
          setMap(m);
          setWaiting([...w.filter((x) => x.team === team), ...w.filter((x) => x.team !== team)]);
          setOffline(false);
        }
      } catch {
        if (!stopped) setOffline(true);
      }
      busy = false;
      if (!stopped) timer = setTimeout(poll, pollMs);
    };
    refresh.current = () => void poll();
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [team, pollMs]);

  const nothing = (text: string) => (
    <div className="team empty">
      <p>{text}</p>
      <button type="button" className="btn-primary" onClick={onNewTeam}>New team</button>
    </div>
  );
  if (!team) return nothing("No team is running. Start one from New team.");
  if (!map) return <div className="team">{offline && <Offline team />}</div>;
  if (map.stopped) return nothing(`${map.team} has stopped. Start a new team when you’re ready.`);

  const group = map.groups?.find((g) => g.name === opened) ?? null;
  // A pause at a daily limit is one question, however many agents it holds.
  const cards = map.panes.filter((p, i) => p.fix && (!p.fix.budget || map.panes.findIndex((q) => q.fix?.budget === p.fix!.budget) === i));
  const budget = map.budget ?? null;
  const cap = budget?.allowed ?? null;
  // After carrying on, the allowance sits just above today's use: two digits keep "1.57M of 1.58M" from reading "1.6M of 1.6M".
  const close = !!(cap && map.spend && cap >= 1e6 && tokens(cap) === tokens(map.spend.tokens));
  const amount = (n: number) => (close ? `${(n / 1e6).toFixed(2)}M` : tokens(n));
  const turnOn = async (id: string) => {
    const r = await tipApply(team, id).catch((e) => ({ ok: false, detail: String(e) }));
    setTipNote(r.ok ? "" : r.detail);
    refresh.current();
  };
  const here = starting.filter((s) => s.team === team);
  const away = map.unreachable; // its machine can't be reached: the last view from there, greyed
  const heading = away ? awayTitle(away) : null;

  return (
    <div className={away ? "team away" : "team"}>
      <div className="team-left">
        <div className="team-title">
          <h1>{heading ? heading.title : group ? `${group.name}: ${map.title.charAt(0).toLowerCase()}${map.title.slice(1)}` : map.title}</h1>
          <p>{heading ? heading.subtitle : group ? `${group.roles.length} of ${map.nodes.length} agents.` : map.subtitle}</p>
          {offline && <Offline team />}
          {here.flatMap((s, i) => {
            if (s.error) return [<p key={i} className="start-error">Couldn’t add the agent: {s.error}</p>];
            const stopped = (role: string) => map.panes.some((p) => p.role === role && p.state === "exited");
            return s.roles.filter((r) => !s.done || (UNSETTLED.includes(r.state) && !stopped(r.role))).map((r) => (
              <p key={`${i}-${r.role}`} className={s.done ? "start-error" : "start-note"}>
                {s.done ? `${r.role} needs you: ${r.detail || "it didn’t come up."}` : `Starting ${r.role}…`}
              </p>
            ));
          })}
        </div>
        <div className="well">
          {group && <button type="button" className="back-groups" onClick={() => setOpened(null)}>‹ All groups</button>}
          {map.groups && !group ? (
            <GroupsMap groups={map.groups} onOpen={setOpened} />
          ) : (
            <AgentsMap nodes={group ? map.nodes.filter((n) => group.roles.includes(n.role)) : map.nodes} edges={map.edges} waits={map.waits} onPick={setAgent} />
          )}
          <div className="spend">
            <span className="grow">
              {map.spend ? (
                <>Today: <b>{amount(map.spend.tokens)}{cap ? ` of ${amount(cap)}` : ""} tokens</b>. <b>{Math.round(map.spend.waste_share * 100)}%</b> went on wake-ups that did nothing.</>
              ) : map.spend_ready === false ? "" : "No tokens used yet today."}
            </span>
            {budget && (
              <span className="limit">
                {cap && <i className={`meter ${budget.used >= cap ? "full" : ""}`} role="img" aria-label={`${Math.min(100, Math.round((100 * budget.used) / cap))}% of today’s limit used`}><i style={{ width: `${Math.min(100, (100 * budget.used) / cap)}%` }} /></i>}
                <button type="button" className="link" onClick={() => setLimits(true)}>{budget.limit || budget.agents.some((a) => a.limit) ? "Change limit" : "Set a daily limit"}</button>
              </span>
            )}
            <span className="legend"><i className="solid" />woke</span>
            {map.waits.length > 0 && <span className="legend"><i className="dashed" />waiting on</span>}
          </div>
        </div>
      </div>
      <div className="team-right">
        {away && <Unreachable line={away} onRetried={() => refresh.current()} />}
        {cards.length === 0 && !away && (
          <div className="nothing">
            <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true" fill="none" stroke="var(--quiet-working)" strokeWidth="1.6" strokeLinecap="round">
              <circle cx="11" cy="11" r="9" /><path d="M7 11.5l3 3 5-6" />
            </svg>
            <b>Nothing waiting on you</b>
            <p>Questions and stuck agents show up here, each with a button to sort it out.</p>
          </div>
        )}
        {cards.map((p) => (
          <Card key={p.role} team={team} pane={p} onChanged={() => refresh.current()} onOpenTerminal={onOpenTerminal} onChangeLimit={() => setLimits(true)} />
        ))}
        {map.tips.slice(0, 1).map((t) => (
          <div className="tip" key={t.id}>
            <span className="grow">
              <b>Save about {Math.round(t.share * 100)}%.</b> Let <code>{t.from}</code>’s routine notes wait until <code>{t.to}</code> is free.
              {tipNote && <><br />{tipNote}</>}
            </span>
            <button type="button" onClick={() => turnOn(t.id)}>Turn on</button>
          </div>
        ))}
        {waiting.length > 0 && (
          <div className="ready">
            <b>Ready to review</b>
            {waiting.map((w) => (
              <div className="ready-row" key={`${w.team}/${w.role}`}>
                <span className="grow"><code>{w.role}</code><small>{w.team === team ? "" : `${w.team} · `}{w.ahead === 1 ? "1 commit" : `${w.ahead} commits`}</small></span>
                <button type="button" onClick={() => onReview(w.team, w.role)}>Review changes</button>
              </div>
            ))}
          </div>
        )}
        <div className="grow" />
        {map.done.length > 0 && (
          <div className="done">
            <div className="done-head"><b>Done for you</b><button type="button" className="link" onClick={onSeeAll}>See all</button></div>
            {map.done.map((r, i) => <DoneRow key={r.id ?? i} row={r} onChanged={() => refresh.current()} />)}
          </div>
        )}
      </div>
      {limits && budget && <DailyLimits team={team} budget={budget} onClose={() => { setLimits(false); refresh.current(); }} />}
      {agent && (
        <AgentDetail team={team} role={agent} onClose={() => setAgent(null)}
          onOpenTerminal={(r) => { setAgent(null); onOpenTerminal(r); }} onChanged={() => refresh.current()} />
      )}
    </div>
  );
}
