import { useEffect, useState } from "react";
import ConnectMachine, { AgentChecks } from "./ConnectMachine";
import InstallSheet from "./InstallSheet";
import { remoteCheck, remoteConnect, remoteMachines, remoteRepos, remoteTerminal, since, type Machine, type MachineCheck } from "./remote";
import type { AgentKind } from "./bridge";
import "./Remote.css";

type Clis = { claude: boolean; codex: boolean };
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** New team's "Where should the agents run?": this Mac, or a machine Assieme is set up on, with its line and agents.
 *  machine: null for this Mac, "" for a remote machine not chosen yet. onAgents: the agents installed there once it
 *  is ready, else null. From Paper "v2 — New team: remote machine" and "Connect a machine: other results". */
export default function RemoteMachine({ machine, onMachine, onAgents, pollMs = 5000 }: {
  machine: string | null;
  onMachine: (m: string | null) => void;
  onAgents: (a: Clis | null) => void;
  pollMs?: number;
}) {
  const [machines, setMachines] = useState<Machine[] | null>(null);
  const [check, setCheck] = useState<MachineCheck | null>(null);
  const [connecting, setConnecting] = useState<{ setUp?: string } | null>(null);
  const [installing, setInstalling] = useState<{ agent: AgentKind; start: "install" | "signin" } | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);

  const load = () => remoteMachines().then((ms) => {
    setMachines(ms);
    if (machine === "" && ms.length) onMachine(ms[0].name);
  }, (e) => setError(message(e)));
  useEffect(() => { if (machine !== null) void load(); }, [machine === null]); // eslint-disable-line react-hooks/exhaustive-deps

  // The chosen machine's line and agents; asked again while it isn't ready, so a reconnect shows by itself.
  useEffect(() => {
    if (!machine) return void onAgents(null);
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ask = async () => {
      try {
        const c = await remoteCheck(machine);
        if (stopped) return;
        setCheck(c);
        setError("");
        onAgents(c.ready && c.agents ? { claude: !!c.agents.claude?.installed, codex: !!c.agents.codex?.installed } : null);
        if (!c.ready) timer = setTimeout(ask, pollMs);
      } catch (e) {
        if (!stopped) setError(message(e));
      }
    };
    setCheck(null);
    onAgents(null);
    void ask();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [machine, tick]); // eslint-disable-line react-hooks/exhaustive-deps

  const status = () => {
    if (!check) return <span className="rm-muted">Checking {machine}…</span>;
    if (check.state === "needs_login")
      return <><span className="rm-waste">{machine} wants you to log in again. Its agents keep working there.</span><span className="rm-grow" />
        <button type="button" className="rm-act" onClick={() => remoteTerminal({ machine: machine! }).catch((e) => setError(message(e)))}>Open Terminal</button></>;
    if (check.state !== "connected")
      return <><span className="rm-waste">{check.state === "connecting" ? `Connecting to ${machine}…` : `Can’t reach ${machine}${check.since_ms ? ` since ${since(check.since_ms)}` : ""}. Its agents keep working there.`}</span><span className="rm-grow" />
        <button type="button" className="rm-act" onClick={() => remoteConnect(machine!).then(() => setTick((t) => t + 1), (e) => setError(message(e)))}>Try now</button></>;
    const connected = (
      <span className="rm-check">
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.2l2.3 2.3L9.5 3.8" fill="none" stroke="var(--quiet-working)" strokeWidth="1.6" strokeLinecap="round" /></svg>
        Connected
      </span>
    );
    if (!check.ready)
      return <>{connected}<span className="rm-check">Assieme isn’t set up there yet</span><span className="rm-grow" />
        <button type="button" className="rm-act" onClick={() => setConnecting({ setUp: check.target })}>Set it up</button></>;
    if (check.outdated)
      return <>{connected}<span className="rm-check">Assieme there is older than this one</span><span className="rm-grow" />
        <button type="button" className="rm-act" onClick={() => setConnecting({ setUp: check.target })}>Update</button></>;
    return <>{connected}<AgentChecks agents={check.agents!} onInstall={(agent, start) => setInstalling({ agent, start })} /></>;
  };

  const known = machines ?? [];
  return (
    <div className="field">
      <div className="label">Where should the agents run?</div>
      <div className="rm-where" role="radiogroup" aria-label="Where should the agents run?">
        <button type="button" role="radio" aria-checked={machine === null} className={machine === null ? "on" : ""} onClick={() => onMachine(null)}>
          <i className="rm-radio" />
          <span><b>This Mac</b> <em>· recommended</em><small>Nothing to set up</small></span>
        </button>
        <button type="button" role="radio" aria-checked={machine !== null} className={machine !== null ? "on" : ""}
          onClick={() => machine === null && onMachine(known[0]?.name ?? "")}>
          <i className="rm-radio" />
          <span><b>A remote machine</b><small>Keeps working when your laptop sleeps</small></span>
        </button>
      </div>
      {machine !== null && (
        <div className="rm-machine">
          <div className="rm-pick">
            <span className="rm-label">Machine</span>
            <select className="rm-select" aria-label="Machine" value={machine} disabled={!known.length}
              onChange={(e) => onMachine(e.target.value)}>
              {!known.length && <option value="">{machines ? "No machine yet" : "Looking…"}</option>}
              {known.map((m) => <option key={m.name} value={m.name}>{m.name}  {m.target === m.name ? "" : m.target}</option>)}
            </select>
            <button type="button" className={known.length ? "rm-link" : "rm-act"} onClick={() => setConnecting({})}>
              {known.length ? "Other ways to connect" : "Connect a machine"}
            </button>
          </div>
          {machine && <div className="rm-status" role="status">{status()}</div>}
          {error && <p className="error">{error}</p>}
        </div>
      )}
      {connecting && (
        <ConnectMachine setUp={connecting.setUp} onClose={() => { setConnecting(null); void load(); setTick((t) => t + 1); }}
          onUse={(m) => { setConnecting(null); void load(); onMachine(m); setTick((t) => t + 1); }} />
      )}
      {installing && machine && (
        <InstallSheet agent={installing.agent} start={installing.start} status={null} machine={machine}
          onClose={() => { setInstalling(null); setTick((t) => t + 1); }} />
      )}
    </div>
  );
}

/** The project on a remote machine: typed (the folder picker can't see there), with its git repositories offered. */
export function RemoteProject({ machine, value, onChange, onDone }: {
  machine: string;
  value: string;
  onChange: (p: string) => void;
  onDone: (p: string) => void;
}) {
  const [repos, setRepos] = useState<string[]>([]);
  useEffect(() => { void remoteRepos(machine).then(setRepos, () => setRepos([])); }, [machine]);
  return (
    <>
      <input className="grow rm-path" aria-label="Project folder" list="rm-repos" value={value}
        placeholder={`~/project on ${machine}`} onChange={(e) => onChange(e.target.value)}
        onBlur={(e) => e.target.value.trim() && onDone(e.target.value.trim())}
        onKeyDown={(e) => e.key === "Enter" && value.trim() && onDone(value.trim())} />
      <datalist id="rm-repos">{repos.map((r) => <option key={r} value={r} />)}</datalist>
      <span className="muted">on {machine}</span>
    </>
  );
}
