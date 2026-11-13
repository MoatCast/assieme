import { useEffect, useState } from "react";
import InstallSheet from "./InstallSheet";
import {
  remoteHosts, remoteJob, remoteSetup, remoteTerminal, remoteTest,
  type Host, type Machine, type SetupJob, type TestResult,
} from "./remote";
import type { AgentKind } from "./bridge";
import useModal from "./useModal";
import "./Remote.css";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
/** `gpu-box` for `dev@gpu-box.tail1.ts.net:2222`: what hc names a machine. IP addresses stay whole, as in hc. */
export function shortName(target: string) {
  const host = target.replace(/^.*@/, "").replace(/:\d+$/, "");
  return /^[\d.]+$/.test(host) || host.includes(":") ? host : host.split(".")[0] || target;
}
const NAMES: Record<AgentKind, string> = { claude: "Claude Code", codex: "Codex" };

export function Tick() {
  return (
    <svg className="rm-icon" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="10" fill="var(--quiet-working)" />
      <path d="M6 10.2l2.6 2.6L14 7.4" fill="none" stroke="var(--quiet-on-ink)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Warn({ red }: { red?: boolean }) {
  const c = red ? "var(--quiet-needs-you)" : "var(--quiet-waste)";
  return (
    <svg className="rm-icon" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="9.2" fill="none" stroke={c} strokeWidth="1.6" />
      <path d="M10 5.5v5.5" stroke={c} strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="10" cy="14.2" r="1.1" fill={c} />
    </svg>
  );
}

/** Claude Code and Codex there, with the one action the first missing one needs. */
export function AgentChecks({ agents, onInstall }: {
  agents: Record<AgentKind, { installed: boolean; signed_in: boolean }>;
  onInstall: (agent: AgentKind, start: "install" | "signin") => void;
}) {
  const kinds = Object.keys(NAMES) as AgentKind[];
  const todo = kinds.find((k) => !agents[k]?.installed || !agents[k]?.signed_in);
  return (
    <>
      {kinds.map((k) => {
        const a = agents[k] ?? { installed: false, signed_in: false };
        const ok = a.installed && a.signed_in;
        return (
          <span key={k} className="rm-check">
            {ok ? <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.2l2.3 2.3L9.5 3.8" fill="none" stroke="var(--quiet-working)" strokeWidth="1.6" strokeLinecap="round" /></svg>
              : <i className="rm-dot" />}
            {NAMES[k]} {ok ? "signed in" : a.installed ? "not signed in" : "not installed"}
          </span>
        );
      })}
      <span className="rm-grow" />
      {todo && (
        <button type="button" className="rm-act" onClick={() => onInstall(todo, agents[todo]?.installed ? "signin" : "install")}>
          {agents[todo]?.installed ? `Sign in to ${NAMES[todo]}` : `Install ${NAMES[todo]}`}
        </button>
      )}
    </>
  );
}

const STEP_LABELS: Record<SetupJob["steps"][number]["id"], [string, string]> = {
  runtime: ["Installing the runtime…", "Runtime installed"],
  assieme: ["Installing Assieme…", "Assieme installed"],
  brain: ["Starting Assieme there…", "Assieme started"],
};

/** Connect a machine: pick a host (SSH config, Tailscale or typed), test it, set Assieme up there.
 *  From Paper "v2 — Other ways to connect" and "v2 — Connect a machine: …". `setUp` starts setting that target up. */
export default function ConnectMachine({ onClose, onUse, setUp, pollMs = 1000 }: {
  onClose: () => void;
  onUse: (machine: string) => void;
  setUp?: string;
  pollMs?: number;
}) {
  const [hosts, setHosts] = useState<{ ssh: Host[]; tailscale: Host[]; machines: Machine[] } | null>(null);
  const [open, setOpen] = useState<"ssh" | "tailscale" | null>("ssh");
  const [picked, setPicked] = useState("");
  const [typed, setTyped] = useState("");
  const [port, setPort] = useState("22");
  const [result, setResult] = useState<TestResult | null>(null);
  const [job, setJob] = useState<SetupJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [installing, setInstalling] = useState<{ agent: AgentKind; start: "install" | "signin" } | null>(null);

  const chosen = typed.trim() ? (port.trim() && port.trim() !== "22" ? `${typed.trim()}:${port.trim()}` : typed.trim()) : picked;
  const target = job?.target ?? result?.target ?? chosen;
  const name = job?.machine ?? result?.name ?? shortName(target);

  const test = async (t: string) => {
    setBusy(true);
    setError("");
    try {
      setResult(await remoteTest(t));
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  const setup = async (t: string) => {
    setError("");
    try {
      setJob((await remoteSetup(t)).job);
    } catch (e) {
      setError(message(e));
    }
  };
  const terminal = () => remoteTerminal(result?.name && result.result === "needs_login" && hosts?.machines.some((m) => m.name === result.name)
    ? { machine: result.name } : { target }).catch((e) => setError(message(e)));

  useEffect(() => {
    remoteHosts().then(setHosts, (e) => setError(message(e)));
    if (setUp) void setup(setUp);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Set-up runs in the brain: ask how far it is until it is done, then test again for the agents there.
  useEffect(() => {
    if (!job || job.done) return;
    const timer = setTimeout(async () => {
      const next = (await remoteJob(job.target).catch(() => ({ job }))).job ?? job;
      setJob(next);
      if (next.done && !next.error) {
        setJob(null);
        await test(next.target);
      }
    }, pollMs);
    return () => clearTimeout(timer);
  }, [job]); // eslint-disable-line react-hooks/exhaustive-deps

  const { ref, onKeyDown } = useModal(() => onClose(), [result?.result, job?.done]);
  // A host already added says what it is, in the machine row's words.
  const there = new Map(hosts?.machines.map((m) => [m.target,
    !m.set_up ? "Not set up yet" : m.state === "connected" ? "Ready" : "Can’t reach"]));

  const sheet = (body: React.ReactNode, note: string, actions: React.ReactNode) => (
    <div className="modal-dim">
      <div className="modal connect" role="dialog" aria-modal="true" aria-label="Connect a machine" tabIndex={-1} ref={ref} onKeyDown={onKeyDown}>
        <div className="modal-head">
          <h2>Connect a machine</h2>
          <p className="big">Agents run there and keep going when your laptop sleeps. Everything goes through SSH; nothing is opened to the internet.</p>
        </div>
        {body}
        {error && <p className="rm-error" role="status">{error}</p>}
        <div className="sheet-actions">
          <span className="rm-note">{note}</span>
          {actions}
        </div>
      </div>
      {installing && result?.name && (
        <InstallSheet agent={installing.agent} start={installing.start} status={null} machine={result.name}
          onClose={() => { setInstalling(null); void test(result.target); }} />
      )}
    </div>
  );

  // Setting up, or a set-up that failed.
  if (job) {
    const failed = job.done && !!job.error;
    return sheet(
      failed ? (
        <div className="rm-card">
          <div className="rm-title"><Warn /><b>Couldn’t install Assieme on {name}</b></div>
          <p className="rm-text">{(job.error ?? "").replace(/^Couldn.t install Assieme on [^:]+: /, "")}</p>
        </div>
      ) : (
        <div className="rm-steps" aria-live="polite">
          <div className="rm-step is-done"><Tick /><span className="rm-grow">Connected to {name}</span><code>{target}</code></div>
          {job.steps.map((s) => (
            <div key={s.id} className={`rm-step is-${s.state}`}>
              {s.state === "done" ? <Tick /> : (
                <svg className="rm-icon" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
                  <circle cx="10" cy="10" r="8.5" fill="none" stroke="var(--quiet-line-strong)" strokeWidth={s.state === "doing" ? 2.4 : 1.6} />
                  {s.state === "doing" && <path className="rm-spin" d="M10 1.5a8.5 8.5 0 0 1 8.5 8.5" fill="none" stroke="var(--quiet-accent)" strokeWidth="2.4" strokeLinecap="round" />}
                </svg>
              )}
              <span className="rm-grow">
                <span className="rm-step-name">{s.state === "done" ? STEP_LABELS[s.id][1] : STEP_LABELS[s.id][0].replace("…", s.state === "doing" ? "…" : "")}</span>
              </span>
            </div>
          ))}
        </div>
      ),
      failed ? "" : "You can close this window. Setting up keeps going.",
      failed ? (
        <>
          <button type="button" className="btn-quiet" onClick={onClose}>Close</button>
          <button type="button" className="btn-primary" onClick={() => setup(job.target)}>Try again</button>
        </>
      ) : (
        <>
          <button type="button" className="btn-quiet" onClick={onClose}>Close</button>
          <button type="button" className="btn-primary" disabled>Use {name}</button>
        </>
      ),
    );
  }

  if (result) {
    const back = <button type="button" className="btn-quiet" onClick={() => setResult(null)}>Back</button>;
    const again = (primary = false) => (
      <button type="button" className={primary ? "btn-primary" : "btn-secondary"} disabled={busy} onClick={() => test(result.target)}>
        {busy ? "Testing…" : "Test again"}
      </button>
    );
    const openTerminal = <button type="button" className="btn-primary" onClick={terminal}>Open Terminal</button>;
    const r = result.result;
    if (r === "ready")
      return sheet(
        <div className="rm-card">
          <div className="rm-title"><Tick /><b>{name} is ready</b></div>
          {result.agents && <div className="rm-checks"><AgentChecks agents={result.agents} onInstall={(agent, start) => setInstalling({ agent, start })} /></div>}
          {result.outdated && <p className="rm-text">Assieme there is older than this one. <button type="button" className="rm-act" onClick={() => setup(result.target)}>Update it</button></p>}
        </div>,
        "",
        <>{back}<button type="button" className="btn-primary" onClick={() => onUse(name)}>Use {name}</button></>,
      );
    if (r === "not_set_up" || r === "connected")
      return sheet(
        <div className="rm-card">
          <div className="rm-title"><Tick /><b className="rm-grow">Connected to {name}</b><code>{result.target}</code></div>
          <p className="rm-text">Assieme isn’t set up there yet. Setting it up downloads what it needs (the runtime, uv and Python) into your home folder on {name}. Nothing runs as admin.</p>
          <div className="rm-adds">
            <span><b>Runtime</b>Keeps the agents’ terminals alive there</span>
            <span><b>Assieme</b>Watches the team there</span>
            <span><b>Then</b>It checks for Claude Code and Codex there</span>
          </div>
        </div>,
        "Takes a minute or two. Your SSH keys stay on this Mac.",
        <>{back}<button type="button" className="btn-primary" onClick={() => setup(result.target)}>Set up {name}</button></>,
      );
    const cases: Record<string, [React.ReactNode, React.ReactNode, React.ReactNode]> = {
      needs_login: [<Warn />, `${name} asks for a password`,
        "Assieme logs in with your SSH keys and agent, and never types a password. Log in once in Terminal to set up a key there, then test again."],
      new_host_key: [<Warn />, `This Mac hasn’t connected to ${name} before`,
        `SSH wants you to check ${name}’s fingerprint once. Accept it in Terminal, then test again. Assieme never accepts a host key for you.`],
      host_key_changed: [<Warn red />, `${name} isn’t the machine it was`,
        `Its host key changed. That happens after a reinstall, or if another machine is pretending to be ${name}. Check with whoever runs ${name} before you trust it again.`],
    };
    const known = cases[r];
    return sheet(
      <div className={`rm-card ${r === "host_key_changed" ? "red" : ""}`}>
        <div className="rm-title">{known ? known[0] : <Warn red />}<b>{known ? known[1] : `Can’t reach ${name}`}</b></div>
        {known ? <p className="rm-text">{known[2]}</p> : (
          <p className="rm-text">Check that {name} is on, and on your network or tailnet.{result.why && <small className="rm-detail">{result.why}</small>}</p>
        )}
      </div>,
      "",
      <>{back}{known && r !== "host_key_changed" && again()}{known ? openTerminal : again(true)}</>,
    );
  }

  const row = (h: Host) => (
    <button key={h.target} type="button" role="radio" aria-checked={!typed && picked === h.target}
      className={`rm-host ${!typed && picked === h.target ? "on" : ""}`} onClick={() => { setTyped(""); setPicked(h.target); }}>
      <i className="rm-radio" />
      <code>{h.name ?? h.target}</code>
      <span className="rm-grow">{h.address}</span>
      {there.get(h.target) && <span className={there.get(h.target) === "Ready" ? "rm-ok" : "rm-muted"}>{there.get(h.target)}</span>}
      {h.from === "tailscale" && !h.online && <span className="rm-muted">Offline</span>}
    </button>
  );
  const group = (id: "ssh" | "tailscale", icon: React.ReactNode, title: string, sub: string, count: string, list: Host[]) => (
    <>
      <button type="button" className="rm-way" aria-expanded={open === id} disabled={!list.length} onClick={() => setOpen(open === id ? null : id)}>
        <span className="rm-badge">{icon}</span>
        <span className="rm-grow"><b>{title}</b><small>{sub}</small></span>
        <span className={list.length ? "rm-ok" : "rm-muted"}>{count}</span>
      </button>
      {open === id && list.length > 0 && <div className="rm-hosts" role="radiogroup" aria-label={title}>{list.map(row)}</div>}
    </>
  );
  const ssh = hosts?.ssh ?? [];
  const tail = hosts?.tailscale ?? [];
  return sheet(
    <div className="rm-ways">
      {group("ssh", <code>ssh</code>, "From your SSH config",
        ssh.length ? ssh.slice(0, 3).map((h) => h.target).join(", ") + (ssh.length > 3 ? `, ${ssh.length - 3} more` : "") : "No hosts in ~/.ssh/config",
        hosts ? `${ssh.length} found` : "…", ssh)}
      {group("tailscale", (
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="var(--quiet-ink)">
          {[[3, 3], [8, 3], [13, 3], [3, 8], [8, 8], [13, 8], [8, 13]].map(([x, y]) => <circle key={`${x}${y}`} cx={x} cy={y} r="1.6" />)}
        </svg>
      ), "Tailscale", "Machines on your private network, by name",
        tail.length ? `${tail.filter((h) => h.online).length} online` : "Not on this Mac", tail)}
      <div className="rm-hand">
        <div className="rm-hand-head">
          <span className="rm-badge plain">
            <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M7 2v10M2 7h10" fill="none" stroke="var(--quiet-ink)" strokeWidth="1.5" strokeLinecap="round" /></svg>
          </span>
          <b>Enter a host by hand</b>
        </div>
        <div className="rm-hand-fields">
          <input className="rm-input grow" aria-label="Host" value={typed} placeholder="user@address, or a Tailscale name"
            onChange={(e) => setTyped(e.target.value)} />
          <input className="rm-input port" aria-label="Port" value={port} inputMode="numeric" onChange={(e) => setPort(e.target.value)} />
          <span className="rm-keys">Uses your SSH keys and agent</span>
        </div>
      </div>
    </div>,
    "Next: Assieme installs its small service there and checks for Claude Code and Codex.",
    <>
      <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
      <button type="button" className="btn-primary" disabled={!chosen || busy} onClick={() => test(chosen)}>{busy ? "Testing…" : "Test connection"}</button>
    </>,
  );
}
