import { useEffect, useRef, useState } from "react";
import { boardGet, type BoardCard, type BoardData, type Pane } from "./bridge";
import Offline from "./Offline";
import { FixButtons, SAYS, tokens } from "./Team";
import { ago } from "./Terminals";
import "./Board.css";

export const COLUMNS = [["todo", "To do"], ["doing", "Doing"], ["review", "Review"], ["done", "Done"]] as const;
const DOING: Record<Pane["state"], string> = {
  asking: "is asking you", stuck: "seems stuck", waiting: "has work waiting", working: "working", idle: "not working right now", exited: "has stopped",
};
const MERGED_GLOW_S = 120;
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Who left notes on a branch, as "reviewer left 2 comments". */
function noted(authors: string[]) {
  return [...new Set(authors)].map((a) => ({ author: a, n: authors.filter((x) => x === a).length }));
}

function Task({ team, card, onChanged, onOpenTerminal, onReview }: {
  team: string;
  card: BoardCard;
  onChanged: () => void;
  onOpenTerminal: (role: string) => void;
  onReview: (team: string, role: string) => void;
}) {
  const agent = card.status === "doing" ? card.agent ?? null : null;
  const branch = card.status === "review" ? card.branch ?? null : null;
  const needs = !!agent && !!SAYS[agent.state];
  const done = card.status === "done";
  const facts = agent ? [DOING[agent.state], ago(agent.since_s)]
    : branch ? [plural(branch.files, "file"), `+${branch.add} −${branch.del}`]
    : done ? [card.note, card.moved_s < 60 ? "just now" : `${ago(card.moved_s)} ago`] : [];
  const line = facts.filter(Boolean).join(" · ");
  const classes = ["task", needs && "needs", done && "finished", done && card.note.startsWith("merged into") && card.moved_s < MERGED_GLOW_S && "merged"];
  return (
    <div className={classes.filter(Boolean).join(" ")} data-testid={`task-${card.id}`}>
      <div className="task-title">{card.title}</div>
      {card.needs_you && <div className="task-waits"><i />{card.note || "Waiting for your answer"}</div>}
      {(card.role || line) && (
        <div className="task-who">
          {card.role && <span className="chip">{card.role}</span>}
          {line && <span className={needs ? "says" : ""}>{line}</span>}
        </div>
      )}
      {!card.role && !done && <div className="task-quiet">Not assigned yet</div>}
      {!done && !card.needs_you && card.note && <div>{card.note}</div>}
      {agent?.state === "asking" && agent.fix && <div>“{agent.fix.text}”</div>}
      {branch && (branch.tests || branch.notes.length > 0) && (
        <div className="task-checks">
          {branch.tests && (
            <div>
              {branch.tests.state === "ok" ? (
                <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3 3 7-7" fill="none" stroke="var(--quiet-working)" strokeWidth="2" strokeLinecap="round" /></svg>
              ) : <i className={branch.tests.state === "bad" ? "bad" : ""} />}
              {branch.tests.text}
            </div>
          )}
          {noted(branch.notes).map(({ author, n }) => (
            <div key={author}><i /><span><span className="mono">{author}</span> left {plural(n, "comment")}</span></div>
          ))}
        </div>
      )}
      {agent && needs && agent.fix && <FixButtons team={team} pane={agent} onChanged={onChanged} onOpenTerminal={onOpenTerminal} />}
      {branch && branch.ahead > 0 && card.role && (
        <button type="button" className="btn-primary" onClick={() => onReview(team, card.role!)}>Review changes</button>
      )}
    </div>
  );
}

/** The Board tab: what the team is doing, as cards the agents and the person move through four columns. */
export default function Board({ team, pollMs = 5000, refreshKey = 0, onNewTeam, onOpenTerminal, onReview }: {
  team: string | null;
  pollMs?: number;
  /** Changes when a card was added from outside, so the board reads again at once. */
  refreshKey?: number;
  onNewTeam: () => void;
  onOpenTerminal: (role: string) => void;
  onReview: (team: string, role: string) => void;
}) {
  const [data, setData] = useState<BoardData | null>(null);
  const [offline, setOffline] = useState(false);
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
        const d = await boardGet(team);
        if (!stopped) {
          setData(d);
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
  }, [team, pollMs, refreshKey]);

  if (!team) {
    return (
      <div className="team empty">
        <p>No team is running. Start one from New team.</p>
        <button type="button" className="btn-primary" onClick={onNewTeam}>New team</button>
      </div>
    );
  }
  if (!data) return <div className="board">{offline && <Offline team />}</div>;

  const cards = data.cards;
  const planners = [...new Set(cards.map((c) => c.by).filter((by) => by !== "you"))];
  const count = (s: string) => cards.filter((c) => c.status === s).length;
  return (
    <div className="board">
      {offline && <Offline team />}
      <div className="board-body">
        <div className="board-head">
          <h1>{data.title || "Tasks"}</h1>
          {cards.length === 0 ? <span>No tasks yet</span> : (
            <span>
              {plural(cards.length, "task")}
              {planners.length > 0 && <> · planned by <span className="mono">{planners.join(", ")}</span></>}
              {" · "}{count("done")} done
            </span>
          )}
          <span className="grow" />
          {data.spend && <span>Today: <b>{tokens(data.spend.tokens)} tokens</b></span>}
        </div>
        <div className="board-columns">
          {COLUMNS.map(([id, label]) => (
            <section key={id} className="board-col" aria-label={label}>
              <h2>{label} <span>{count(id)}</span></h2>
              {cards.filter((c) => c.status === id).map((c) => (
                <Task key={c.id} team={data.team} card={c} onChanged={() => refresh.current()} onOpenTerminal={onOpenTerminal} onReview={onReview} />
              ))}
              {id === "review" && cards.length > 0 && count(id) === 0 && (
                <div className="board-none">Nothing to review. Finished work lands here with its diff and checks.</div>
              )}
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
