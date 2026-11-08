import { useEffect, useRef, useState } from "react";
import { Terminal as Xterm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebglAddon } from "@xterm/addon-webgl";
import { CanvasAddon } from "@xterm/addon-canvas";
import "@xterm/xterm/css/xterm.css";
import { openTerminal, readTerminal, type TerminalHandle } from "./bridge";

/** typing: open ready to type (focus and Reply) instead of watch-only. */
type Props = { session: string; address: string; typing?: boolean };

const BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];

/** One agent's terminal. `assieme` agents stream live (watch first, "Type here" to type); others are read-only. */
// The dark terminal colours from Paper "v2 — Terminals"; every colour clears 4.5:1 on the background.
const THEME = {
  background: "#141b28", foreground: "#dce2eb", cursor: "#dce2eb", cursorAccent: "#141b28",
  selectionBackground: "#3a4a66", black: "#141b28", red: "#f39a87", green: "#7fd0a0", yellow: "#e5b865",
  blue: "#8fb3f0", magenta: "#d3a6e8", cyan: "#7fcfd6", white: "#dce2eb", brightBlack: "#9aa5b6", brightRed: "#f7b3a5",
  brightGreen: "#9fe0ba", brightYellow: "#efcb8a", brightBlue: "#aac7f5", brightMagenta: "#e0bff0", brightCyan: "#9fdfe4",
  brightWhite: "#ffffff",
};

export default function Terminal({ session, address, typing: startTyping = false }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [typing, setTyping] = useState(startTyping);
  const [slowDrawing, setSlowDrawing] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [finished, setFinished] = useState(false);
  const live = session === "assieme";

  useEffect(() => {
    const term = new Xterm({ fontFamily: "Geist Mono, ui-monospace, monospace", fontSize: 12.5, convertEol: false, theme: THEME });
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
          // A pane opened after the agent quit still shows how it ended.
          void readTerminal(session, address)
            .then((text) => !stopped && term.write(text.replace(/\r?\n/g, "\r\n")))
            .catch(() => {});
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
