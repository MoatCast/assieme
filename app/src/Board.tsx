import { useEffect, useRef, useState } from "react";
import { boardAdd, boardGet, boardMove, boardRemove, type BoardCard, type BoardData, type BoardStatus, type Pane } from "./bridge";
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

/** A card's "⋯": move it to another column, or remove it. Paper "v2 — Board: card menu". */
function CardMenu({ team, card, onChanged, onError }: { team: string; card: BoardCard; onChanged: () => void; onError: (text: string) => void }) {
  const [open, setOpen] = useState(false);
  const run = async (change: Promise<unknown>) => {
    setOpen(false);
    try {
      await change;
      onError("");
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
    onChanged();
  };
  return (
    <div className="task-menu-wrap" onKeyDown={(e) => e.key === "Escape" && setOpen(false)}>
      <button type="button" className={`task-more ${open ? "on" : ""}`} aria-label={`More for ${card.title}`} aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen(!open)}>
        <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" fill="currentColor"><circle cx="2.5" cy="7" r="1.3" /><circle cx="7" cy="7" r="1.3" /><circle cx="11.5" cy="7" r="1.3" /></svg>
      </button>
      {open && (
        <>
          <div className="task-menu-away" onClick={() => setOpen(false)} />
          <div className="task-menu" role="menu" aria-label={`Move ${card.title}`}>
            <div className="task-menu-label">Move to</div>
            {COLUMNS.map(([id, label]) => (
              <button key={id} type="button" role="menuitemradio" aria-checked={card.status === id} disabled={card.status === id}
                onClick={() => run(boardMove(team, card.id, id as BoardStatus))}>
                {label}
                {card.status === id && <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>}
              </button>
            ))}
            <hr />
            <button type="button" role="menuitem" className="remove" onClick={() => run(boardRemove(team, card.id))}>Remove</button>
          </div>
        </>
      )}
    </div>
  );
}

/** The New task sheet. Paper "v2 — Board: new task". */
export function NewTask({ team, roles, onClose }: {
  team: string;
  roles: string[];
  /** `note`: something the person should know, such as an agent that could not be told. */
  onClose: (added: boolean, note?: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [role, setRole] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const adding = useRef(false); // a second Enter before React re-renders must not add a second card

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (adding.current) return;
    adding.current = true;
    setPending(true);
    setError("");
    try {
      const got = await boardAdd(team, title, role || null);
      onClose(true, role && !got.told ? `${role} isn’t running, so it wasn’t told about the new task.` : undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      adding.current = false;
      setPending(false);
    }
  };

  return (
    <>
      <div className="dim" onClick={() => onClose(false)} />
      <form className="sheet" role="dialog" aria-label="New task" style={{ gap: 20 }} onSubmit={add}>
        <div className="sheet-head">
          <div>
            <h2>New task</h2>
            <p>A card on {team}’s board.</p>
          </div>
          <button type="button" className="sheet-close" aria-label="Close" onClick={() => onClose(false)}>
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M5 5l10 10M15 5L5 15" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <label className="field">
          <span>What needs doing</span>
          <input autoFocus value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="field">
          <span>Give it to</span>
          <select className={role ? "mono" : ""} value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="">Not assigned yet</option>
            {roles.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <small>Or “Not assigned yet”: the planner, or you, can hand it out later.</small>
        </label>
        <div className="sheet-foot">
          {error && <p className="error">{error}</p>}
          <p>{role
            ? <>The card goes in To do, and <span style={{ fontFamily: "var(--quiet-font-mono)" }}>{role}</span> is told about it now. It moves the card itself as it works.</>
            : "The card goes in To do. Nobody is told about it until it is given to an agent."}</p>
          <div className="sheet-actions">
            <button type="button" className="btn-quiet" onClick={() => onClose(false)}>Cancel</button>
            <button type="submit" className="btn-primary" disabled={pending || !title.trim()}>{pending ? "Adding…" : "Add task"}</button>
          </div>
        </div>
      </form>
    </>
  );
}

function Task({ team, card, onChanged, onOpenTerminal, onReview }: {
  team: string;
  card: BoardCard;
  onChanged: () => void;
  onOpenTerminal: (role: string) => void;
  onReview: (team: string, role: string) => void;
}) {
  const [error, setError] = useState("");
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
      <CardMenu team={team} card={card} onChanged={onChanged} onError={setError} />
      {error && <p className="card-note">{error}</p>}
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
export default function Board({ team, pollMs = 5000, refreshKey = 0, onNewTeam, onNewTask, onOpenTerminal, onReview }: {
  team: string | null;
  pollMs?: number;
  /** Changes when a card was added from outside, so the board reads again at once. */
  refreshKey?: number;
  onNewTeam: () => void;
  onNewTask: () => void;
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
              {id === "todo" && cards.length === 0 && (
                <div className="board-none">
                  No tasks yet. Agents add a card for each piece of work as they plan it, and move it along as they go.
                  <button type="button" className="btn-secondary" onClick={onNewTask}>New task</button>
                </div>
              )}
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
