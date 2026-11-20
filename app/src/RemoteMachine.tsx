import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import ConnectMachine, { AgentChecks } from "./ConnectMachine";
import InstallSheet from "./InstallSheet";
import { remoteCheck, remoteConnect, remoteMachines, remoteRepos, remoteTerminal, since, type Machine, type MachineCheck } from "./remote";
import type { AgentKind } from "./bridge";
import StepPanel, { useClickGuard, type Clis, type PanelProps } from "./StepPanel";
import "./Remote.css";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** New team's Where step body: push buttons for this Mac and each machine Assieme is set up on, with the chosen
 *  machine's line and agents. machine: null for this Mac. onAgents: the agents installed there once it
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

  const load = () => remoteMachines().then(setMachines, (e) => setError(message(e)));
  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
        // Every kind there, so another kind of agent only widens the type.
        // signed out there stops at its sign-in screen, so it counts as not ready (its row offers Sign in)
        onAgents(c.ready && c.agents ? Object.fromEntries(Object.entries(c.agents).map(([k, a]) => [k, !!a?.installed && !!a?.signed_in])) as Clis : null);
        if (!c.ready) timer = setTimeout(ask, pollMs);
      } catch (e) {
        if (stopped) return;
        setError(message(e));
        timer = setTimeout(ask, pollMs); // a failed check is asked again, never left on Checking…
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
    <div className="field rm-field">
      <div className="rm-where">
        <button type="button" className={machine === null ? "option on" : "option"} aria-pressed={machine === null} onClick={() => onMachine(null)}>
          <span className="text"><b>This Mac</b><small>Ready now</small></span>
        </button>
        {known.map((m) => (
          <button type="button" key={m.name} className={machine === m.name ? "option on" : "option"} aria-pressed={machine === m.name}
            onClick={() => onMachine(m.name)}>
            <span className="text"><b>{m.name}</b><small>{m.target === m.name ? "Keeps working while this Mac sleeps" : m.target}</small></span>
          </button>
        ))}
      </div>
      <button type="button" className="link" onClick={() => setConnecting({})}>Connect a machine…</button>
      {machine && <div className="rm-machine"><div className="rm-status" role="status">{status()}</div></div>}
      {error && <p className="error">{error}</p>}
      {connecting && createPortal(
        <ConnectMachine setUp={connecting.setUp} onClose={() => { setConnecting(null); void load(); setTick((t) => t + 1); }}
          onUse={(m) => { setConnecting(null); void load(); onMachine(m); setTick((t) => t + 1); }} />, document.body,
      )}
      {installing && machine && createPortal(
        <InstallSheet agent={installing.agent} start={installing.start} status={null} machine={machine}
          onClose={() => { setInstalling(null); setTick((t) => t + 1); }} />, document.body,
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
  useEffect(() => { if (machine) void remoteRepos(machine).then(setRepos, () => setRepos([])); }, [machine]);
  return (
    <>
      <input className="grow rm-path" aria-label="Project folder" list="rm-repos" value={value} disabled={!machine}
        placeholder={machine ? `~/project on ${machine}` : "Choose a machine first"} onChange={(e) => onChange(e.target.value)}
        onBlur={(e) => e.target.value.trim() && onDone(e.target.value.trim())}
        onKeyDown={(e) => e.key === "Enter" && value.trim() && onDone(value.trim())} />
      <datalist id="rm-repos">{repos.map((r) => <option key={r} value={r} />)}</datalist>
      {machine && <span className="muted">on {machine}</span>}
    </>
  );
}

/** New team, Where: this Mac moves on by itself; a remote machine waits for Continue, since its check is a network wait. */
export function WhereStep({ panel, machine, there, onMachine, onAgents, onNext, onCancel, pollMs }: {
  panel: PanelProps; machine: string | null; there: Clis | null; onMachine: (m: string | null) => void;
  onAgents: (a: Clis | null) => void; onNext: () => void; onCancel: () => void; pollMs?: number;
}) {
  const guard = useClickGuard(panel.at);
  return (
    <StepPanel {...panel} title="Where should the agents run?" hint="Choosing This Mac moves to the next step." footer={<>
      <span className="safe" />
      <button type="button" className="quiet" onClick={onCancel}>Cancel</button>
      {machine !== null && <button type="button" className="primary" disabled={!there} onClick={onNext}>Continue</button>}
    </>}>
      <RemoteMachine machine={machine} onAgents={onAgents} pollMs={pollMs} onMachine={(m) => {
        if (guard()) return;
        onMachine(m);
        if (m === null) onNext();
      }} />
    </StepPanel>
  );
}
