// One poll feeds the list and the panel: the team's map and every team's work to review (this team's first).
import { useCallback, useEffect, useRef, useState } from "react";
import { reviewWaiting, teamMap, type ReviewWaiting, type TeamMap } from "./bridge";

export function useTeamMap(team: string | null, pollMs: number) {
  const [got, setGot] = useState<{ team: string; map: TeamMap } | null>(null);
  const [waiting, setWaiting] = useState<ReviewWaiting[]>([]);
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
        const [m, w] = await Promise.all([teamMap(team), reviewWaiting().catch(() => [] as ReviewWaiting[])]);
        if (!stopped) {
          setGot({ team, map: m });
          setWaiting([...w.filter((x) => x.team === team), ...w.filter((x) => x.team !== team)]);
          setOffline(false);
        }
      } catch {
        if (!stopped) setOffline(true); // the last map stays: rows keep their strength (Review Focus 3)
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

  // Another team's map never shows under this team's name, even for the moment before the new poll answers.
  const map = got && got.team === team ? got.map : null;
  return { map, waiting, offline, refresh: useCallback(() => refresh.current(), []) };
}
