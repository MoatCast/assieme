import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { containerBuild, containerOpen, containerStatus, openLink, setupStatus, terminalState, type AgentKind, type ContainerState } from "./bridge";
import ConnectSheet from "./ConnectSheet";
import { AGENT_KINDS, kindName } from "./kinds";
import "./AgentMenu.css";

const GET_DOCKER = "https://docs.docker.com/desktop/setup/install/mac-install/";
const toolName = (t: ContainerState["tool"]) => (t === "container" ? "Apple’s container tool" : "Docker");

const Chevron = () => (
  <svg width="10" height="10" viewBox="0 0 12 12" aria-hidden="true">
    <path d="M3 4.5l3 3 3-3" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.4" strokeLinecap="round" />
  </svg>
);
const Tick = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
    <path d="M3 8.5l3 3 7-7" fill="none" stroke="var(--quiet-ink)" strokeWidth="1.8" strokeLinecap="round" />
  </svg>
);
export const Box = () => (
  <svg width="13" height="13" viewBox="0 0 14 14" aria-label="In a container" role="img">
    <path d="M7 1.8l4.7 2.6v5.2L7 12.2 2.3 9.6V4.4L7 1.8zM2.5 4.5L7 7l4.5-2.5M7 7v5" fill="none"
      stroke="var(--quiet-slate)" strokeWidth="1.2" strokeLinejoin="round" />
  </svg>
);

/** Which agent runs it, Paper "v2 — Add agent: choosing which agent" and New team's agent pills: installed kinds
 *  first, the others under "Not on this Mac yet" with Install…. New team's pill also holds "Run in a container". */
export default function AgentMenu({ value, installed, look, container, team, onChange, onInstall }: {
  value: AgentKind;
  installed: Partial<Record<AgentKind, boolean>>;
  look: "pill" | "field";
  /** Only New team's pill offers the container here; Add agent has its own "Where it works". */
  container?: boolean;
  team?: string;
  onChange: (c: { agent?: AgentKind; container?: boolean }) => void;
  onInstall: (k: AgentKind) => void;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && (e.stopPropagation(), setOpen(false));
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc, true);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc, true); };
  }, [open]);

  const here = AGENT_KINDS.filter((k) => installed[k.id]);
  const missing = AGENT_KINDS.filter((k) => !installed[k.id]);
  const pick = (c: { agent?: AgentKind; container?: boolean }) => { onChange(c); setOpen(false); };
  const [connecting, setConnecting] = useState(false);
  // Antigravity's team tools come from Assieme's plugin: until it's in, picking it asks to connect first.
  const choose = async (agent: AgentKind) => {
    if (agent !== "antigravity" || agent === value) return pick({ agent });
    setOpen(false);
    const connected = await setupStatus().then((s) => !!s.agents.antigravity?.connected, () => false);
    if (connected) onChange({ agent });
    else setConnecting(true);
  };

  return (
    <div className={`agent-menu-box ${look}`} ref={box}>
      <button type="button" className={look === "pill" ? "agent-pill" : "agent-field"} aria-haspopup="menu"
        aria-expanded={open} aria-label={`Agent: ${kindName(value)}${container ? ", in a container" : ""}`}
        onClick={() => setOpen(!open)}>
        <span className="agent-pill-name">{kindName(value)}</span>
        {look === "pill" && container && <Box />}
        <Chevron />
      </button>
      {open && (
        <div className="agent-menu" role="menu">
          {here.map((k) => (
            <button key={k.id} type="button" role="menuitemradio" aria-checked={k.id === value}
              onClick={() => void choose(k.id)}>
              <span className="slot">{k.id === value && <Tick />}</span>
              <span className="what">{k.name}</span>
              <span className="side">{k.maker}</span>
            </button>
          ))}
          {missing.length > 0 && (
            <>
              <div className="sep" />
              <div className="group-label">Not on this Mac yet</div>
              {missing.map((k) => (
                <button key={k.id} type="button" role="menuitem" className="absent"
                  onClick={() => { setOpen(false); onInstall(k.id); }}>
                  <span className="slot" />
                  <span className="what">{k.name}</span>
                  <span className="side install">Install…</span>
                </button>
              ))}
            </>
          )}
          {look === "pill" && (
            <>
              <div className="sep" />
              <button type="button" role="menuitemcheckbox" aria-checked={!!container} className="tall"
                onClick={() => pick({ container: !container })}>
                <span className="slot">{container && <Tick />}</span>
                <span className="what">
                  Run in a container
                  <small>Sees its copy of {team || "the project"} and nothing else.</small>
                </span>
              </button>
            </>
          )}
        </div>
      )}
      {connecting && createPortal(
        <ConnectSheet onClose={() => setConnecting(false)}
          onConnected={() => { setConnecting(false); onChange({ agent: "antigravity" }); }} />, document.body)}
    </div>
  );
}

/** The one line under "A container", Paper "v2 — Container: states". onReady: whether an agent can start in one now. */
export function ContainerLine({ agent, onReady, pollMs = 3000 }: { agent: AgentKind; onReady?: (ready: boolean) => void; pollMs?: number }) {
  const [state, setState] = useState<ContainerState | null>(null);
  const [build, setBuild] = useState<string | null>(null); // the build terminal while it runs
  const [error, setError] = useState("");
  const ready = !!state?.running && state.image_ready;
  useEffect(() => { onReady?.(ready); }, [ready]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const s = await containerStatus(agent);
        if (stopped) return;
        setState(s);
        if (build && (s.image_ready || (await terminalState(build)) === "finished")) {
          if (!s.image_ready) setError("The build didn’t finish. Its terminal shows why; try again.");
          setBuild(null);
        }
      } catch (e) {
        if (!stopped) setError(e instanceof Error ? e.message : String(e));
      }
      if (!stopped) timer = setTimeout(poll, pollMs);
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [agent, build, pollMs]);

  const act = async (f: () => Promise<unknown>) => {
    setError("");
    try { await f(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };

  if (!state) return null;
  const tool = toolName(state.tool);
  return (
    <div className="container-line">
      {!state.tool ? (
        <div className="cl-row top">
          <span className="cl-dot mute" />
          <span className="cl-text">Containers need Docker, and it isn’t on this Mac.
            <button type="button" className="cl-link" onClick={() => void openLink(GET_DOCKER)}>How to get one</button>
          </span>
        </div>
      ) : !state.running ? (
        <div className="cl-row">
          <span className="cl-dot waste" />
          <span className="cl-text">{tool} is on this Mac but not running.</span>
          {state.tool === "docker" && <button type="button" className="cl-button" onClick={() => void act(containerOpen)}>Open Docker</button>}
        </div>
      ) : build ? (
        <div className="cl-row">
          <span className="cl-dot working" />
          <span className="cl-text">Building the container… Add the agent when this is done.</span>
        </div>
      ) : !state.image_ready ? (
        <div className="cl-row">
          <span className="cl-dot mute" />
          <span className="cl-text">Uses {tool}. The first time, it downloads and builds the container, about 1.5 GB.</span>
          <button type="button" className="cl-button" onClick={() => void act(async () => setBuild((await containerBuild(agent)).address))}>
            Download and build
          </button>
        </div>
      ) : (
        <div className="cl-row">
          <svg className="cl-tick" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            <path d="M3 7.2l2.6 2.6L11 4.4" fill="none" stroke="var(--quiet-working)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="cl-text">Uses {tool}, already on this Mac. Takes a little longer to start.</span>
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
