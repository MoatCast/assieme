import { useEffect, useState } from "react";
import Shell from "./Shell";
import { serviceInstall, serviceRunning, windowShow } from "./bridge";
import "./tokens.css";

type Ask = "checking" | "ask" | "starting" | "done" | { error: string };

export default function App() {
  const [ask, setAsk] = useState<Ask>("checking");

  // The window starts hidden (tauri.conf.json) and shows once the page is drawn in its theme: no white flash.
  useEffect(() => void windowShow().catch(() => {}), []);

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
      <Shell />
    </>
  );
}
