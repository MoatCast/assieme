import { useEffect, useState } from "react";
import { serviceInstall } from "./bridge";

/** Shown while the background service doesn't answer: a short drop stays quiet, a long one offers the fix. */
export default function Offline({ afterMs = 15000 }: { afterMs?: number }) {
  const [long, setLong] = useState(afterMs === 0);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (long) return;
    const timer = setTimeout(() => setLong(true), afterMs);
    return () => clearTimeout(timer);
  }, [afterMs, long]);

  if (!long) return <div className="offline">Reconnecting…</div>;

  const start = async () => {
    setStarting(true);
    setError("");
    try {
      await serviceInstall(); // the view's own poll hides this once the service answers
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="offline offline-long" role="alert">
      <p>Assieme’s service isn’t answering. Your agents keep running, but nothing is watching them.</p>
      <button type="button" disabled={starting} onClick={start}>Start it</button>
      {error && <p className="offline-error">It didn’t start: {error}</p>}
    </div>
  );
}
