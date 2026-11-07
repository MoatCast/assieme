import { useEffect, useState } from "react";
import FirstRun from "./FirstRun";
import { serviceInstall, serviceRunning } from "./bridge";
import "./tokens.css";

type Ask = "checking" | "ask" | "starting" | "done" | { error: string };

export default function App() {
  const [ask, setAsk] = useState<Ask>("checking");

  useEffect(() => {
    serviceRunning()
      .then((up) => setAsk(up ? "done" : "ask"))
      .catch(() => setAsk("ask"));
  }, []);

  const start = async () => {
    setAsk("starting");
    try {
      await serviceInstall();
      setAsk("done");
    } catch (e) {
      setAsk({ error: String(e) });
    }
  };

  return (
    <>
      {(ask === "ask" || ask === "starting" || typeof ask === "object") && (
        <div className="service-ask" role="dialog">
          <p>Assieme runs a small background service so it can watch your agents while this window is closed. Start it?</p>
          {typeof ask === "object" && (
            <p className="service-error">
              It didn’t start: {ask.error}. Details are in ~/.local/state/assieme/brain.log.
            </p>
          )}
          <div className="service-actions">
            <button type="button" className="primary" disabled={ask === "starting"} onClick={start}>
              Start it
            </button>
            <button type="button" onClick={() => setAsk("done")}>
              Not now
            </button>
          </div>
        </div>
      )}
      <FirstRun pollMs={5000} />
    </>
  );
}
