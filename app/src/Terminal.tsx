import { useEffect, useRef, useState } from "react";
import { Terminal as Xterm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebglAddon } from "@xterm/addon-webgl";
import { CanvasAddon } from "@xterm/addon-canvas";
import "@xterm/xterm/css/xterm.css";
import { openTerminal, readTerminal, type TerminalHandle } from "./bridge";

type Props = { session: string; address: string };

const BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];

/** One agent's terminal. `assieme` agents stream live (watch first, "Type here" to type); others are read-only. */
export default function Terminal({ session, address }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [typing, setTyping] = useState(false);
  const [slowDrawing, setSlowDrawing] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [finished, setFinished] = useState(false);
  const live = session === "assieme";

  useEffect(() => {
    const term = new Xterm({ fontFamily: "Geist Mono, ui-monospace, monospace", fontSize: 12, convertEol: false });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(box.current!);
    const useCanvas = () => {
      term.loadAddon(new CanvasAddon());
      setSlowDrawing(true);
    };
    try {
      const webgl = new WebglAddon();
      webgl.onContextLoss(() => {
        webgl.dispose();
        useCanvas();
      });
      term.loadAddon(webgl);
    } catch {
      useCanvas();
    }
    try {
      fit.fit();
    } catch {
      /* not laid out yet */
    }

    let handle: TerminalHandle | null = null;
    let stopped = false;
    let attempt = 0;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const input = term.onData((d) => {
      if (typing && handle) void handle.input(d);
    });

    const connect = async () => {
      try {
        handle = await openTerminal(address, term.cols, term.rows, !typing, (b) => term.write(b), () => {
          if (stopped) return;
          void handle?.close(); // release the bridge's side of the lost stream
          handle = null;
          setReconnecting(true);
          retry = setTimeout(connect, BACKOFF_MS[Math.min(attempt++, BACKOFF_MS.length - 1)]);
        });
        if (stopped) void handle.close();
        else {
          attempt = 0;
          setReconnecting(false);
        }
      } catch (e) {
        if (stopped) return;
        if (String(e).includes("finished")) {
          setReconnecting(false);
          setFinished(true); // nothing to reattach to: stop retrying
          return;
        }
        setReconnecting(true);
        retry = setTimeout(connect, BACKOFF_MS[Math.min(attempt++, BACKOFF_MS.length - 1)]);
      }
    };
    if (live) void connect();
    else void readTerminal(session, address).then((text) => term.write(text.replace(/\r?\n/g, "\r\n")));

    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => {
      try {
        fit.fit();
        void handle?.resize(term.cols, term.rows);
      } catch {
        /* hidden */
      }
    });
    observer?.observe(box.current!);

    return () => {
      stopped = true;
      clearTimeout(retry);
      observer?.disconnect();
      input.dispose();
      void handle?.close();
      term.dispose();
    };
  }, [session, address, typing, live]);

  return (
    <div className="terminal">
      <div className="terminal-bar">
        {live && !typing && (
          <button type="button" onClick={() => setTyping(true)}>
            Type here
          </button>
        )}
        {reconnecting && <span className="terminal-note">Reconnecting…</span>}
        {finished && <span className="terminal-note">This agent has finished.</span>}
        {slowDrawing && <span className="terminal-note">Fast drawing is off for this terminal.</span>}
        {!live && <span className="terminal-note">Read-only: this agent runs in your own herdr.</span>}
      </div>
      <div ref={box} className="terminal-screen" />
    </div>
  );
}
