import { useEffect, useState } from "react";
import { setupStatus, type SetupStatus } from "./bridge";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The setup status, asked every pollMs, one call at a time, only while `enabled`:
 *  asking starts the agents' own status commands, so it runs only while a setup step or an install needs it. */
export function useSetupStatus(enabled: boolean, pollMs = 3000) {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const s = await setupStatus();
        if (!stopped) {
          setStatus(s);
          setError("");
        }
      } catch (e) {
        if (!stopped) setError(message(e));
      }
      if (!stopped) timer = setTimeout(poll, pollMs);
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [enabled, pollMs]);

  return { status, error };
}
