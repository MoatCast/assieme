import { useEffect, useState } from "react";
import { activityList, activityUndo, type ActivityRow } from "./bridge";
import "./Activity.css";

const FILTERS = [["all", "All"], ["you", "By you"], ["auto", "Automatic"]] as const;
type Filter = (typeof FILTERS)[number][0];

function dayLabel(day: string, now = new Date()) {
  const pad = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (day === pad(now)) return "Today";
  if (day === pad(yesterday)) return "Yesterday";
  return day;
}

/** Everything Assieme did, and why, with Undo where it really undoes something. */
export default function Activity({ team, pollMs = 10000 }: { team: string | null; pollMs?: number }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [rows, setRows] = useState<ActivityRow[] | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const r = await activityList(team, filter);
        if (!stopped) setRows(r.rows);
      } catch {
        // the last list stays on screen
      }
      if (!stopped) timer = setTimeout(poll, pollMs);
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [team, filter, pollMs, tick]);

  const undo = async (id: string) => {
    if (pending) return;
    setPending(id);
    const r = await activityUndo(id).catch((e) => ({ ok: false, detail: String(e) }));
    setNotes((n) => ({ ...n, [id]: r.ok ? "" : r.detail }));
    setPending(null);
    setTick((t) => t + 1);
  };

  const days = new Map<string, ActivityRow[]>();
  for (const r of rows ?? []) days.set(r.day, [...(days.get(r.day) ?? []), r]);

  return (
    <div className="activity">
      <div className="activity-sheet">
        <div className="activity-head">
          <div>
            <h1>Activity</h1>
            <p>Everything Assieme did, and why. Where something can be undone, you have 24 hours.</p>
          </div>
          <div className="activity-filters">
            {FILTERS.map(([id, label]) => (
              <button key={id} type="button" className={filter === id ? "on" : ""} aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}</button>
            ))}
          </div>
        </div>
        {rows && rows.length === 0 && <p className="activity-empty">Nothing yet.</p>}
        {[...days].map(([day, list]) => (
          <div className="activity-day" key={day}>
            <div className="activity-day-name">{dayLabel(day)}</div>
            <div className="activity-rows">
              {list.map((r, i) => (
                <div className="activity-row" key={r.id ?? `${day}-${i}`} data-testid={`row-${r.id ?? i}`}>
                  <span className="activity-at">{r.at}</span>
                  <div className="activity-text">
                    <b>{r.title}</b>
                    {r.reason && <span>{r.reason}</span>}
                    {r.id && notes[r.id] && <span className="activity-note">{notes[r.id]}</span>}
                  </div>
                  {r.undo && r.id && (
                    <button type="button" disabled={pending === r.id} onClick={() => undo(r.id!)}>{r.undo.label}</button>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
