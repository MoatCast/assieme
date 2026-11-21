// One window (spec 2026-11-20): the list of agents on the left, the panel for the selected row on the right.
import { useEffect, useMemo, useRef, useState } from "react";
import type { MergeResult, Pane, Starting, TeamMap } from "./bridge";
import Activity from "./Activity";
import AgentList, { VIEWS, type Selection, type View } from "./AgentList";
import AgentPanel from "./AgentPanel";
import Board from "./Board";
import CommandPalette, { type PaletteAction } from "./CommandPalette";
import Offline from "./Offline";
import PlanBars from "./PlanBars";
import Unreachable, { awayTitle } from "./Unreachable";
import Review from "./Review";
import { failedStart, isGroup, listModel, ran, today, type ListModel, type Row } from "./listModel";
import TeamOverview from "./TeamOverview";
import Terminals from "./Terminals";
import { useShortcuts } from "./useShortcuts";
import { useTeamMap } from "./useTeamMap";
import "./Window.css";

export type OpenLink = { team: string; role: string | null; view: "review" | null; nonce: number };

const FIXED_SHOWS_MIN = 10;

const rows = (m: ListModel): Row[] => [...m.needs, ...[...m.working, ...m.done].flatMap((x) => (isGroup(x) ? x.rows : [x]))];

/** The Needs-you row that has waited longest, else Team overview. */
export function initialSelection(model: ListModel): Selection {
  const first = model.needs[0]; // listModel orders Needs you by wait
  return first ? { kind: "agent", role: first.role } : { kind: "view", view: "overview" };
}

/** Minutes from "HH:MM" today to now; null when it can't be read. */
function minutesSince(hhmm: string | null, now: Date) {
  const [h, m] = (hhmm ?? "").split(":").map(Number);
  if (!hhmm || Number.isNaN(h) || Number.isNaN(m)) return null;
  const at = new Date(now);
  at.setHours(h, m, 0, 0);
  return (now.getTime() - at.getTime()) / 60000;
}

/** Fixes that worked in the last 10 minutes and whose row the person hasn't selected since: role → "HH:MM". */
function fixedRecently(map: TeamMap, dismissed: Set<string>, now: Date) {
  const out: Record<string, string> = {};
  for (const r of map.done) {
    if (!r.agent || r.outcome !== "fixed" || r.day !== today(now) || out[r.agent] || dismissed.has(r.id ?? "")) continue;
    const m = minutesSince(r.outcome_at, now);
    if (m !== null && m >= 0 && m <= FIXED_SHOWS_MIN) out[r.agent] = r.outcome_at!;
  }
  return out;
}

/** What changed since the last poll, said politely: new Needs-you rows and fix outcomes. */
function changes(before: TeamMap, after: TeamMap, beforeModel: ListModel, afterModel: ListModel) {
  const said: string[] = [];
  const had = new Set(beforeModel.needs.map((r) => r.role));
  for (const r of afterModel.needs) if (!had.has(r.role)) said.push(`${r.role} needs you`);
  const was = new Map(before.done.map((r) => [r.id, r.outcome]));
  for (const r of after.done) {
    if (!r.agent || was.get(r.id) === r.outcome) continue;
    if (r.outcome === "fixed") said.push(`${r.agent}: Fixed by Assieme`);
    if (r.outcome === "still_stuck") said.push(`${r.agent}: still stuck after Assieme's fix`);
  }
  return said.join(". ");
}

/** A row with no pane yet (an agent still starting) still opens a panel. */
const bare = (role: string): Pane => ({ role, agent: "claude", address: "", state: "working", since_s: null, detail: "", live: false, fix: null });

export default function Window({ team, starting, open, pollMs = 5000, boardKey = 0, paletteKey = 0, onNewTeam, onNewTask, onMerged, onDiscarded,
  onOpenFixes = () => {}, onBringOver = () => {}, onSettings = () => {}, onAddAgent }: {
  team: string | null;
  starting: Starting[];
  /** A link from outside (menu bar, notification): select that agent, or open its review. */
  open: OpenLink | null;
  pollMs?: number;
  boardKey?: number;
  /** Command Palette in the View menu: each change opens the palette. */
  paletteKey?: number;
  onNewTeam: () => void;
  onNewTask: () => void;
  /** A merge from the panel's review: the app-wide notice with Undo. */
  onMerged: (m: MergeResult) => void;
  onDiscarded: (text: string) => void;
  /** The empty panel's "Bring your team over". */
  onBringOver?: () => void;
  /** Settings › Fixes, from Team overview's one-time notice. */
  onOpenFixes?: () => void;
  /** ⌘, and the palette's Settings. */
  onSettings?: () => void;
  /** The palette's "Add a builder to this team"; left out while no team runs. */
  onAddAgent?: () => void;
}) {
  const { map, waiting, offline, refresh } = useTeamMap(team, pollMs);
  const [chosen, setChosen] = useState<{ team: string; sel: Selection } | null>(null);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [said, setSaid] = useState("");
  const [intoPanel, setIntoPanel] = useState(0);
  const [full, setFull] = useState(false);
  const [toList, setToList] = useState(0);
  const [reviewing, setReviewing] = useState<{ team: string; role: string; ahead: number } | null>(null);
  const [merged, setMerged] = useState<{ role: string; text: string } | null>(null);
  const [palette, setPalette] = useState(false);
  const list = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const last = useRef<{ map: TeamMap; model: ListModel } | null>(null);
  const applied = useRef<number | null>(null); // the link from outside already acted on

  const now = new Date();
  const recent = map ? fixedRecently(map, dismissed, now) : {};
  const model = useMemo(() => {
    if (!map) return null;
    const m = listModel(map, waiting, starting, recent);
    return map.unreachable ? { ...m, verdict: awayTitle(map.unreachable).title } : m;
  },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [map, waiting, starting, JSON.stringify(recent)]);
  const sel = chosen && chosen.team === team ? chosen.sel : null;

  const select = (s: Selection) => {
    if (!team) return;
    setChosen({ team, sel: s });
    setReviewing(null);
    setMerged(null);
    if (s.kind === "agent" && map) {
      const ids = map.done.filter((r) => r.agent === s.role && r.outcome === "fixed").map((r) => r.id ?? "");
      if (ids.some((id) => !dismissed.has(id))) setDismissed(new Set([...dismissed, ...ids]));
    }
  };

  // Another team: no review, merge line or full size carries over (declared before the link, which may open a review).
  useEffect(() => {
    setReviewing(null);
    setMerged(null);
    setFull(false);
  }, [team]);

  // A link from outside names its team. A review opens for that team at once; an agent is selected once that team's
  // map is in, and never a same-named agent of another team. A link acts once.
  useEffect(() => {
    if (!open || applied.current === open.nonce || !open.role) return;
    const here = !!model && team === open.team && rows(model).some((r) => r.role === open.role);
    if (open.view === "review") {
      applied.current = open.nonce;
      setReviewing({ team: open.team, role: open.role, ahead: waiting.find((x) => x.team === open.team && x.role === open.role)?.ahead ?? 0 });
      if (here && team) setChosen({ team, sel: { kind: "agent", role: open.role } });
      return;
    }
    if (!model || team !== open.team) return;
    applied.current = open.nonce;
    if (here) {
      setChosen({ team, sel: { kind: "agent", role: open.role } });
      setReviewing(null);
    }
  }, [model, team, open, waiting]);

  // A team opens on its own rule; a selected agent that stops hands the panel to Team overview.
  useEffect(() => {
    if (!model || !team) return;
    if (!sel) return setChosen((prev) => (prev && prev.team === team ? prev : { team, sel: initialSelection(model) })); // a link may have chosen
    if (sel.kind === "agent" && !rows(model).some((r) => r.role === sel.role))
      setChosen({ team, sel: { kind: "view", view: "overview" } });
  }, [model, team, sel]);

  useEffect(() => {
    if (!map || !model) return;
    const prev = last.current;
    if (prev && prev.map.team === map.team && prev.map !== map) {
      const text = changes(prev.map, map, prev.model, model);
      if (text) setSaid(text);
    }
    last.current = { map, model };
  }, [map, model]);

  // ⌘L leaves the terminal for the list. Escape is left alone: agents use it to interrupt. (⌘1–⌘9 go to agents and the
  // views to the ⌘K palette in the power-tools build.) A sheet open on top keeps its keys.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.metaKey || e.altKey || e.ctrlKey || document.querySelector("[aria-modal='true']")) return;
      if (e.key.toLowerCase() === "l") {
        e.preventDefault();
        e.stopPropagation();
        setFull(false);
        setToList((n) => n + 1);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [team]);

  // ⌘1–⌘9 in list order, ⌘J the next agent that needs you (wrapping), ⌘N, ⌘, and ⌘K for the palette.
  const listed = model ? rows(model) : [];
  const goAgent = (role: string) => {
    select({ kind: "agent", role });
    setToList((n) => n + 1);
  };
  const nextNeeds = () => {
    const needs = model?.needs ?? [];
    if (!needs.length) return;
    const i = needs.findIndex((r) => sel?.kind === "agent" && r.role === sel.role);
    goAgent(needs[(i + 1) % needs.length].role);
  };
  useShortcuts({
    palette: () => setPalette(true),
    agent: (i) => { if (listed[i]) goAgent(listed[i].role); },
    needsYou: nextNeeds,
    newTeam: onNewTeam,
    settings: onSettings,
  }, !palette);
  const actions: PaletteAction[] = [
    ...(team ? [{ label: "Go to what needs you", key: "⌘J", run: nextNeeds }] : []),
    ...(team && onAddAgent && !map?.stopped ? [{ label: "Add a builder to this team", run: onAddAgent }] : []),
    { label: "New team", key: "⌘N", run: onNewTeam },
    ...(team ? VIEWS.map((v) => ({ label: v.label, run: () => select({ kind: "view", view: v.view }) })) : []),
    { label: "Settings", key: "⌘,", run: onSettings },
  ];
  useEffect(() => { if (paletteKey) setPalette(true); }, [paletteKey]);
  const paletteShown = palette && (
    <CommandPalette agents={listed.map((row, i) => ({ row, key: i < 9 ? `⌘${i + 1}` : null }))} actions={actions}
      onAgent={goAgent} onClose={() => setPalette(false)} />
  );

  useEffect(() => {
    if (toList) list.current?.querySelector<HTMLElement>("[aria-current='true']")?.focus();
  }, [toList]);

  // Return: the card's first button when the panel has a card, else the terminal.
  useEffect(() => {
    if (!intoPanel) return;
    const p = panel.current;
    (p?.querySelector<HTMLElement>("[data-card] button") ?? p?.querySelector<HTMLElement>("[data-terminal]"))?.focus();
  }, [intoPanel]);

  const entrySelection = (el: Element | null): Selection | null => {
    const v = el?.getAttribute("data-entry") ?? "";
    if (v.startsWith("agent:")) return { kind: "agent", role: v.slice(6) };
    if (v.startsWith("view:")) return { kind: "view", view: v.slice(5) as View };
    return null;
  };
  const onListKey = (e: React.KeyboardEvent) => {
    const entries = [...(list.current?.querySelectorAll<HTMLElement>("[data-entry]") ?? [])];
    const i = entries.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = entries[Math.min(entries.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)))];
      next?.focus();
      const s = entrySelection(next ?? null);
      if (s) select(s);
    } else if (e.key === "Enter") {
      const s = entrySelection(document.activeElement);
      if (!s) return;
      e.preventDefault();
      select(s);
      setIntoPanel((n) => n + 1);
    }
  };


  // Review opens in the panel; Back returns to the agent (spec "The panel").
  const onReview = (t: string, role: string) =>
    setReviewing({ team: t, role, ahead: waiting.find((x) => x.team === t && x.role === role)?.ahead ?? 0 });

  const reviewPanel = reviewing && (
    <Review key={`${reviewing.team}/${reviewing.role}`} team={reviewing.team} role={reviewing.role}
      from={sel?.kind === "agent" ? sel.role : reviewing.role} onBack={() => setReviewing(null)}
      onMerged={(m) => {
        setMerged({ role: m.role, text: `Merged into ${m.base} · ${reviewing.ahead === 1 ? "1 commit" : `${reviewing.ahead} commits`}` });
        setReviewing(null);
        onMerged(m);
        refresh();
      }}
      onDiscarded={(text) => { setReviewing(null); onDiscarded(text); refresh(); }} />
  );

  if (!team) {
    const empty: ListModel = { verdict: "No team is running", counts: "Your agents show here once a team starts.", assieme: null,
      needs: [], working: [], done: [], stale: false };
    return (
      <div className="window">
        <div className="window-list"><AgentList model={empty} selected={{ kind: "view", view: "overview" }} onSelect={() => {}} /></div>
        <div className="window-panel">
          {reviewPanel || (
            <div className="panel-empty">
              <h2>Start a team of agents</h2>
              <p>Pick a folder and describe the work. Assieme starts the agents, each in its own terminal and branch, and fixes problems on its own while they work.</p>
              <div className="panel-empty-buttons">
                <button type="button" className="btn-primary" onClick={onNewTeam}>New team</button>
                <button type="button" className="btn-secondary" onClick={onBringOver}>Bring your team over</button>
              </div>
              {waiting.length > 0 && (
                <div className="panel-empty-review">
                  <h3>Ready to review from earlier teams</h3>
                  {waiting.map((w) => (
                    <div className="panel-empty-row" key={`${w.team}/${w.role}`}>
                      <span className="grow"><code>{w.role}</code><small>{`${w.team} · ${w.ahead === 1 ? "1 commit" : `${w.ahead} commits`}`}</small></span>
                      <button type="button" className="btn-secondary" onClick={() => onReview(w.team, w.role)}>Review changes</button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
        {paletteShown}
      </div>
    );
  }
  if (!map || !model) return <div className="window"><div className="window-panel">{offline && <Offline team />}</div></div>;
  const live = map.panes.filter((p) => p.state !== "exited" && p.reachable !== false).length; // terminals All terminals can show
  const fixed = map.fixes_today?.fixed ?? 0;
  const views: Record<View, string> = {
    overview: fixed ? `${fixed} fixed today` : "",
    terminals: live ? `${live} running` : "",
    board: map.open_tasks ? `${map.open_tasks} open task${map.open_tasks === 1 ? "" : "s"}` : "",
    activity: map.last_at ? `Last at ${map.last_at}` : "",
  };
  const selectedPane = sel?.kind === "agent" ? map.panes.find((p) => p.role === sel.role) ?? bare(sel.role) : null;

  return (
    <div className="window">
      <div ref={list} className="window-list" hidden={full && sel?.kind === "agent"} onKeyDown={onListKey}>
        {sel && <AgentList model={model} selected={sel} views={views} onSelect={select}><PlanBars limits={map.limits ?? {}} /></AgentList>}
      </div>
      <div ref={panel} className="window-panel">
        {offline && <div className="panel-offline"><Offline team /></div>}
        {map.unreachable && <div className="panel-away"><Unreachable line={map.unreachable} onRetried={refresh} /></div>}
        {reviewPanel}
        {/* until the person picks another row: the merge usually stops the agent, and the panel then moves to Team overview */}
        {!reviewing && merged && (selectedPane?.role === merged.role || sel?.kind === "view") && <p className="panel-merged" role="status">{merged.text}</p>}
        {!reviewing && selectedPane && (
          <AgentPanel key={`${team}/${selectedPane.role}`} team={team} pane={selectedPane} full={full} onFullSize={setFull}
            fixes={map.done.filter((r) => r.agent === selectedPane.role && r.day === today())}
            waiters={map.waits.filter((x) => x.to === selectedPane.role).map((x) => x.from)}
            failed={failedStart(starting, team, selectedPane.role, map.panes)}
            review={waiting.find((x) => x.team === team && x.role === selectedPane.role) ?? null}
            onChanged={refresh} onReview={onReview} />
        )}
        {!reviewing && sel?.kind === "view" && sel.view === "overview" && map.stopped && (
          <div className="panel-empty">
            <h2>{`All ${map.panes.length === 1 ? "1 agent" : `${map.panes.length} agents`} stopped at ${map.run?.stopped_at ?? ""}.`.replace(" at .", ".")}</h2>
            {map.run && <p>{[ran(map.run.minutes), `Assieme fixed ${map.run.fixed === 1 ? "1 problem" : `${map.run.fixed} problems`}`,
              `${map.run.tasks_done === 1 ? "1 task" : `${map.run.tasks_done} tasks`} done`].join(" · ")}</p>}
            <div className="panel-empty-buttons">
              <button type="button" className="btn-secondary" onClick={() => select({ kind: "view", view: "activity" })}>Open Activity</button>
            </div>
          </div>
        )}
        {!reviewing && sel?.kind === "view" && sel.view === "overview" && !map.stopped && (
          <TeamOverview team={team} map={map} waiting={waiting} onRefresh={refresh} onReview={onReview} onOpenFixes={onOpenFixes}
            onSelectAgent={(role) => select({ kind: "agent", role })} onSeeAll={() => select({ kind: "view", view: "activity" })} />
        )}
        {!reviewing && sel?.kind === "view" && sel.view === "terminals" && (
          <Terminals team={team} pollMs={3000} onNewTeam={onNewTeam} onReview={onReview} onSelect={(role) => select({ kind: "agent", role })}
            checking={Object.fromEntries(map.done.filter((r) => r.agent && r.outcome === "checking").reverse().map((r) => [r.agent!, r]))} />
        )}
        {!reviewing && sel?.kind === "view" && sel.view === "board" && (
          <Board team={team} pollMs={5000} refreshKey={boardKey} onNewTeam={onNewTeam} onNewTask={onNewTask}
            onOpenTerminal={(role) => select({ kind: "agent", role })} onReview={onReview} />
        )}
        {!reviewing && sel?.kind === "view" && sel.view === "activity" && <Activity team={team} />}
      </div>
      <div role="status" aria-live="polite" className="sr-only">{said}</div>
      {paletteShown}
    </div>
  );
}
