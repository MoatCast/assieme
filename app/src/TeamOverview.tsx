// Team overview (Paper "Main window": team overview, all quiet with the notice, first team): what Assieme did first,
// then a compact map of how the agents work together, the band, the daily limit and other teams' work to review.
import { useEffect, useState } from "react";
import { getSettings, runFound, setSetting, type ReviewWaiting, type TeamMap } from "./bridge";
import DailyLimits from "./DailyLimits";
import FixRow, { AssiemeMark } from "./FixRow";
import { tokens } from "./listModel";
import Stage, { StageToggle, useStageShown } from "./Stage";
import { AgentsMap, GroupsMap } from "./TeamMap";
import "./TeamOverview.css";

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function runningFor(min: number | null | undefined) {
  if (min == null) return "";
  if (min < 60) return `running for ${plural(Math.max(1, min), "minute")}`;
  return `running for ${plural(Math.floor(min / 60), "hour")}`;
}

/** A notice with Assieme's mark: the one-time "fixes run themselves", or a herdr team not handed over yet. */
function AssiemeNotice({ children, action }: { children: React.ReactNode; action: React.ReactNode }) {
  return (
    <div className="overview-notice">
      <AssiemeMark />
      <span className="grow">{children}</span>
      {action}
    </div>
  );
}

export default function TeamOverview({ team, map, waiting, onSelectAgent, onSeeAll, onReview, onRefresh, onOpenFixes }: {
  team: string;
  map: TeamMap;
  waiting: ReviewWaiting[];
  onSelectAgent: (role: string) => void;
  onSeeAll: () => void;
  onReview: (team: string, role: string) => void;
  onRefresh: () => void;
  onOpenFixes: () => void;
}) {
  const [notice, setNotice] = useState(false);
  const [fullMap, setFullMap] = useState(false);
  const [limits, setLimits] = useState(false);
  const [group, setGroup] = useState<string | null>(null);
  const stageShown = useStageShown();

  useEffect(() => {
    let stopped = false;
    getSettings().then((s) => !stopped && setNotice(!!s?.fixes_notice), () => {});
    return () => { stopped = true; };
  }, []);

  const dismiss = async () => {
    setNotice(false);
    await setSetting("fixes_notice", false).catch(() => {}); // it shows again next time: only a convenience
  };
  const handOver = async () => {
    await runFound(team, true).catch(() => {});
    onRefresh();
  };

  const fixed = map.fixes_today?.fixed ?? 0;
  const checking = map.fixes_today?.checking ?? 0;
  const live = map.panes.filter((p) => p.state !== "exited").length;
  const budget = map.budget ?? null;
  const cap = budget?.allowed ?? budget?.limit ?? null;
  const today = map.done.filter((r) => r.by === "auto");
  const others = waiting.filter((w) => w.team !== team);
  const checkingRoles = map.done.filter((r) => r.outcome === "checking" && r.agent).map((r) => r.agent!);
  const opened = map.groups?.find((g) => g.name === group) ?? null;
  const stage = stageShown && !map.unreachable && map.nodes.length > 0;

  return (
    <div className="overview">
      {notice && (
        <AssiemeNotice action={<button type="button" className="notice-ok" onClick={dismiss}>OK</button>}>
          <b>Assieme now fixes problems on its own:</b> stuck agents, full memory, refused commands. If a fix doesn't work, it
          pauses the agent and asks you. <button type="button" className="link-accent" onClick={onOpenFixes}>Settings › Fixes</button>
        </AssiemeNotice>
      )}
      {map.found && !map.run_by_assieme && (
        <AssiemeNotice action={<button type="button" className="notice-ok" onClick={handOver}>Let Assieme run this team</button>}>
          Assieme can fix this team's problems on its own too. Until then they stay with you.
        </AssiemeNotice>
      )}
      <div className="overview-head">
        <div className="overview-title">
          <h2>Team overview</h2>
          <p>{[`${plural(live, "agent")} on ${team}`, runningFor(map.running_min)].filter(Boolean).join(" · ")}</p>
          <div className="overview-numbers" data-testid="numbers">
            {fixed ? <span><b>{fixed}</b> {fixed === 1 ? "problem" : "problems"} fixed today</span> : <span><b>0</b> problems so far</span>}
            {checking ? <span><b>{checking}</b> being checked</span> : <span>No problem open</span>}
            {map.spend && <span><b>{tokens(map.spend.tokens)}</b> tokens today</span>}
          </div>
          {budget && (
            <div className="overview-limit">
              {cap ? (
                <>
                  <i className="limit-bar" role="img" aria-label={`${Math.min(100, Math.round((100 * budget.used) / cap))}% of today’s limit used`}>
                    <i className={budget.used >= cap ? "full" : ""} style={{ width: `${Math.min(100, (100 * budget.used) / cap)}%` }} />
                  </i>
                  <span>Daily limit: {tokens(budget.used)} of {tokens(cap)} tokens</span>
                  <button type="button" className="link-accent" onClick={() => setLimits(true)}>Change</button>
                </>
              ) : <button type="button" className="link-accent" onClick={() => setLimits(true)}>Set a daily limit</button>}
            </div>
          )}
        </div>
        {stage && <div className="overview-stage"><Stage team={team} nodes={map.nodes} groups={map.groups ?? []} /></div>}
        {!map.unreachable && <StageToggle />}
      </div>
      <div className="overview-did" data-testid="assieme-today">
        <div className="overview-did-head">
          <span className="grow"><b>What Assieme did today</b> <span>· {fixed} fixed, {checking} being checked</span></span>
          <button type="button" className="link-accent" onClick={onSeeAll}>See all in Activity ›</button>
        </div>
        {today.slice(0, 3).map((r, i) => <FixRow key={r.id ?? i} row={r} onChanged={onRefresh} />)}
        {!today.length && (
          <p className="overview-empty">No problems so far. When an agent loops, repeats a refused command or runs low on memory, Assieme
            fixes it on its own and lists the result here. If a fix doesn't work, it pauses the agent and asks you.</p>
        )}
      </div>
      <div className="overview-map-section">
        <div className="overview-map-head">
          <span className="grow">How they work together</span>
          {opened && <button type="button" className="link-accent" onClick={() => setGroup(null)}>‹ All groups</button>}
          <button type="button" className="link-accent" onClick={() => setFullMap(!fullMap)}>{fullMap ? "Show less ›" : "Open the full map ›"}</button>
        </div>
        <div className={fullMap ? "overview-map" : "overview-map compact"} data-testid="overview-map">
          {map.groups && !opened ? (
            <GroupsMap team={team} groups={map.groups} onOpen={setGroup} />
          ) : (
            <AgentsMap team={team} compact={!fullMap} checking={checkingRoles} edges={map.edges} waits={map.waits} onPick={onSelectAgent}
              nodes={opened ? map.nodes.filter((n) => opened.roles.includes(n.role)) : map.nodes} />
          )}
        </div>
        <span className="grow" />
        {others.length > 0 && (
          <div className="overview-others">
            <span className="grow">
              <span>Ready to review in other teams:</span>{" "}
              {others.map((w, i) => <span key={`${w.team}/${w.role}`}>{i ? ", " : ""}{`${w.role} · ${w.team} · ${plural(w.ahead, "commit")}`}</span>)}
            </span>
            <button type="button" className="btn-outline small" onClick={() => onReview(others[0].team, others[0].role)}>Review changes</button>
          </div>
        )}
      </div>
      {limits && budget && <DailyLimits team={team} budget={budget} onClose={() => { setLimits(false); onRefresh(); }} />}
    </div>
  );
}
