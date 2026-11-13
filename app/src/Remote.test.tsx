import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const b = vi.hoisted(() => ({
  teamPresets: vi.fn(), checkProject: vi.fn(), startTeam: vi.fn(), confirmReplace: vi.fn(),
  setupCommands: vi.fn(), terminalState: vi.fn(), installStop: vi.fn(),
}));
vi.mock("./bridge", async (orig) => ({ ...(await orig<typeof import("./bridge")>()), ...b }));
const r = vi.hoisted(() => ({
  remoteHosts: vi.fn(), remoteMachines: vi.fn(), remoteCheck: vi.fn(), remoteTest: vi.fn(), remoteSetup: vi.fn(),
  remoteJob: vi.fn(), remoteConnect: vi.fn(), remoteTerminal: vi.fn(), remoteRepos: vi.fn(),
  checkProjectOn: vi.fn(), startTeamOn: vi.fn(), installAgentOn: vi.fn(), signinAgentOn: vi.fn(), setupStatusOn: vi.fn(),
}));
vi.mock("./remote", async (orig) => ({ ...(await orig<typeof import("./remote")>()), ...r }));
vi.mock("./Terminal", () => ({ default: () => <div /> }));

import ConnectMachine from "./ConnectMachine";
import RemoteMachine from "./RemoteMachine";
import NewTeam from "./NewTeam";
import Unreachable, { awayTitle } from "./Unreachable";

const flush = () => act(async () => {});
const ROLE = { name: "builder", agent: "codex" as const, job: "builds", checkout: "branch" as const, parent: null, brief: "You build." };
const PRESETS = [{ id: "one-agent", label: "One agent", roles: [ROLE] }];
const PRACTICES = { groups: [{ id: "safety", title: "Safe by default", summary: "s", rules: ["a"] }], basics: [] };
const MODELS = { claude: { models: [], efforts: [] }, codex: { models: [], efforts: [] } };
const GPU = { name: "gpu-box", target: "gpu-box", state: "connected" as const };
const READY = { ...GPU, ready: true, outdated: false, git: true,
  agents: { claude: { installed: true, signed_in: true }, codex: { installed: false, signed_in: false } } };

beforeEach(() => {
  vi.clearAllMocks();
  b.teamPresets.mockResolvedValue({ presets: PRESETS, practices: PRACTICES, last: null, models: MODELS });
  b.setupCommands.mockResolvedValue({ claude: { name: "Claude Code", install: "x", signin: "y", status: "z" }, codex: { name: "Codex", install: "x", signin: "y", status: "z" } });
  r.remoteMachines.mockResolvedValue([GPU]);
  r.remoteCheck.mockResolvedValue(READY);
  r.remoteRepos.mockResolvedValue(["~/checkout-service"]);
  r.checkProjectOn.mockResolvedValue({ ok: true });
  r.startTeamOn.mockResolvedValue({ team: "checkout-service" });
  r.remoteHosts.mockResolvedValue({ ssh: [{ target: "gpu-box", address: "dev@10.0.4.12", from: "ssh" }, { target: "devbox", address: "dev:2222", from: "ssh" }], tailscale: [],
    machines: [{ name: "devbox", target: "devbox", state: "connected", set_up: true }] });
});
afterEach(cleanup);

test("New team on a remote machine: its agents, a project typed there, and the start goes there", async () => {
  const onClose = vi.fn();
  render(<NewTeam clis={{ claude: true, codex: true }} onClose={onClose} />);
  await flush();
  fireEvent.click(screen.getByRole("radio", { name: /A remote machine/ }));
  await flush();
  await flush();
  expect((screen.getByLabelText("Machine") as HTMLSelectElement).value).toBe("gpu-box");
  screen.getByText("Connected");
  screen.getByText("Claude Code signed in");
  screen.getByText("Codex not installed");
  screen.getByRole("button", { name: "Install Codex" });
  // Codex isn't there: the agent becomes Claude Code, the only kind on gpu-box.
  expect(screen.queryByRole("button", { name: "Codex" })).toBeNull();
  const path = screen.getByLabelText("Project folder");
  fireEvent.change(path, { target: { value: "~/checkout-service" } });
  fireEvent.blur(path);
  await flush();
  expect(r.checkProjectOn).toHaveBeenCalledWith("~/checkout-service", "gpu-box");
  fireEvent.change(screen.getByLabelText("What should the team work on?"), { target: { value: "Fix login" } });
  fireEvent.click(screen.getByText("Start with recommended"));
  await flush();
  expect(r.startTeamOn).toHaveBeenCalledWith(expect.objectContaining({ project: "~/checkout-service", task: "Fix login",
    roles: [{ ...ROLE, agent: "claude", kind: "builder", level: "safe" }] }), "gpu-box");
  expect(b.startTeam).not.toHaveBeenCalled();
  expect(onClose).toHaveBeenCalled();
});

test("no machine yet: Start waits and Connect a machine opens the sheet", async () => {
  r.remoteMachines.mockResolvedValue([]);
  render(<NewTeam clis={{ claude: true, codex: true }} onClose={() => {}} />);
  await flush();
  fireEvent.click(screen.getByRole("radio", { name: /A remote machine/ }));
  await flush();
  screen.getByText("No machine yet");
  screen.getByText("Connect a machine to continue.");
  fireEvent.click(screen.getByRole("button", { name: "Connect a machine" }));
  await flush();
  screen.getByRole("dialog", { name: "Connect a machine" });
});

test("a machine that can't be reached says so, and Try now dials it", async () => {
  r.remoteCheck.mockResolvedValue({ ...GPU, state: "unreachable", since_ms: Date.UTC(2026, 10, 13, 14, 2), ready: false });
  r.remoteConnect.mockResolvedValue(GPU);
  render(<NewTeam clis={{ claude: true, codex: true }} onClose={() => {}} />);
  await flush();
  fireEvent.click(screen.getByRole("radio", { name: /A remote machine/ }));
  await flush();
  await flush();
  screen.getByText(/Can’t reach gpu-box since .*Its agents keep working there\./);
  screen.getByText("Waiting for gpu-box to be ready.");
  fireEvent.click(screen.getByRole("button", { name: "Try now" }));
  await flush();
  expect(r.remoteConnect).toHaveBeenCalledWith("gpu-box");
});

test("Connect a machine: pick a host, test, set up with progress, then use it", async () => {
  const onUse = vi.fn();
  render(<ConnectMachine onClose={() => {}} onUse={onUse} pollMs={1} />);
  await flush();
  screen.getByText("2 found");
  screen.getByText("Ready"); // devbox is set up already, and reached
  expect(screen.queryByText("Dev container or Codespace")).toBeNull(); // step 24, after the MVP
  expect(screen.queryByText("New cloud machine")).toBeNull();
  expect((screen.getByRole("button", { name: "Test connection" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("radio", { name: /gpu-box/ }));
  r.remoteTest.mockResolvedValueOnce({ result: "not_set_up", target: "gpu-box" });
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
  await flush();
  expect(r.remoteTest).toHaveBeenCalledWith("gpu-box");
  screen.getByText("Connected to gpu-box");
  screen.getByText(/Nothing runs as admin\./);
  const steps = (state: string) => (["runtime", "assieme", "brain"] as const).map((id, i) => ({ id, state: i === 0 ? "done" : i === 1 ? state : "waiting" }));
  r.remoteSetup.mockResolvedValue({ job: { target: "gpu-box", machine: "gpu-box", done: false, error: null, steps: steps("doing") } });
  r.remoteJob.mockResolvedValueOnce({ job: { target: "gpu-box", machine: "gpu-box", done: false, error: null, steps: steps("doing") } });
  fireEvent.click(screen.getByRole("button", { name: "Set up gpu-box" }));
  await flush();
  screen.getByText("Runtime installed");
  screen.getByText("Installing Assieme…");
  expect((screen.getByRole("button", { name: "Use gpu-box" }) as HTMLButtonElement).disabled).toBe(true);
  r.remoteJob.mockResolvedValue({ job: { target: "gpu-box", machine: "gpu-box", done: true, error: null, steps: steps("done") } });
  r.remoteTest.mockResolvedValue({ ...READY, result: "ready", target: "gpu-box" });
  await screen.findByText("gpu-box is ready");
  fireEvent.click(screen.getByRole("button", { name: "Use gpu-box" }));
  expect(onUse).toHaveBeenCalledWith("gpu-box");
});

test("a host typed by hand with its port is one ssh target", async () => {
  render(<ConnectMachine onClose={() => {}} onUse={() => {}} />);
  await flush();
  fireEvent.change(screen.getByLabelText("Host"), { target: { value: "dev@10.0.4.12" } });
  fireEvent.change(screen.getByLabelText("Port"), { target: { value: "2222" } });
  r.remoteTest.mockResolvedValue({ result: "unreachable", target: "dev@10.0.4.12:2222", why: "ssh: connect to host 10.0.4.12 port 2222: Operation timed out" });
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
  await flush();
  expect(r.remoteTest).toHaveBeenCalledWith("dev@10.0.4.12:2222");
  screen.getByText("Can’t reach 10.0.4.12"); // an IP address stays whole, as hc names it
  expect(screen.getByText(/Operation timed out/).className).toBe("rm-detail"); // the raw error is the small second line
  expect(screen.queryByRole("button", { name: "Open Terminal" })).toBeNull();
  screen.getByRole("button", { name: "Test again" });
});

test("a password or a new host key is the person's, in Terminal; a changed key can't be tested past", async () => {
  render(<ConnectMachine onClose={() => {}} onUse={() => {}} />);
  await flush();
  fireEvent.click(screen.getByRole("radio", { name: /devbox/ }));
  r.remoteTest.mockResolvedValueOnce({ result: "needs_login", target: "devbox" });
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
  await flush();
  screen.getByText("devbox asks for a password");
  r.remoteTerminal.mockResolvedValue({ ok: true });
  fireEvent.click(screen.getByRole("button", { name: "Open Terminal" }));
  expect(r.remoteTerminal).toHaveBeenCalledWith({ target: "devbox" });
  r.remoteTest.mockResolvedValueOnce({ result: "new_host_key", target: "devbox" });
  fireEvent.click(screen.getByRole("button", { name: "Test again" }));
  await flush();
  screen.getByText("This Mac hasn’t connected to devbox before");
  screen.getByText(/never accepts a host key for you/);
  r.remoteTest.mockResolvedValueOnce({ result: "host_key_changed", target: "devbox" });
  fireEvent.click(screen.getByRole("button", { name: "Test again" }));
  await flush();
  screen.getByText("devbox isn’t the machine it was");
  expect(screen.queryByRole("button", { name: "Test again" })).toBeNull();
  screen.getByRole("button", { name: "Open Terminal" });
});

test("a set-up that fails says why in words, with Try again", async () => {
  r.remoteSetup.mockResolvedValue({ job: { target: "gpu-box", machine: "gpu-box", done: true,
    error: "Couldn't install Assieme on gpu-box: this machine needs curl to install uv",
    steps: [{ id: "runtime", state: "done" }, { id: "assieme", state: "failed" }, { id: "brain", state: "waiting" }] } });
  render(<ConnectMachine onClose={() => {}} onUse={() => {}} setUp="gpu-box" />);
  await flush();
  screen.getByText("Couldn’t install Assieme on gpu-box");
  screen.getByText("this machine needs curl to install uv");
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(r.remoteSetup).toHaveBeenCalledTimes(2);
});

test("the team view while its machine is away: reconnecting, or a login in Terminal", async () => {
  const line = { name: "gpu-box", target: "gpu-box", state: "unreachable" as const, since_ms: Date.UTC(2026, 10, 13, 14, 2) };
  expect(awayTitle(line).title).toBe("Can’t reach gpu-box");
  expect(awayTitle(line).subtitle).toMatch(/^Since \d\d:\d\d\. Its agents keep working there; this is what they were doing then\.$/);
  const retried = vi.fn();
  r.remoteConnect.mockResolvedValue(line);
  render(<Unreachable line={line} onRetried={retried} />);
  screen.getByText("Reconnecting to gpu-box…");
  fireEvent.click(screen.getByRole("button", { name: "Try now" }));
  await flush();
  expect(r.remoteConnect).toHaveBeenCalledWith("gpu-box");
  expect(retried).toHaveBeenCalled();
  cleanup();
  r.remoteTerminal.mockResolvedValue({ ok: true });
  render(<Unreachable line={{ ...line, state: "needs_login" }} onRetried={retried} />);
  screen.getByText("gpu-box wants you to log in again");
  fireEvent.click(screen.getByRole("button", { name: "Open Terminal" }));
  await flush();
  expect(r.remoteTerminal).toHaveBeenCalledWith({ machine: "gpu-box" });
});

test("a machine check that fails is asked again, not left on Checking…", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  r.remoteCheck.mockRejectedValueOnce(new Error("Can't reach gpu-box: timed out")).mockResolvedValue(READY);
  render(<RemoteMachine machine="gpu-box" onMachine={() => {}} onAgents={() => {}} pollMs={50} />);
  await flush();
  await act(async () => { await vi.advanceTimersByTimeAsync(60); });
  await flush();
  expect(r.remoteCheck).toHaveBeenCalledTimes(2);
  screen.getByText("Claude Code signed in");
  vi.useRealTimers();
});

test("every kind of agent on the machine is passed on, not only Claude Code and Codex", async () => {
  const onAgents = vi.fn();
  r.remoteCheck.mockResolvedValue({ ...READY, agents: { ...READY.agents, gemini: { installed: true, signed_in: true } } });
  render(<RemoteMachine machine="gpu-box" onMachine={() => {}} onAgents={onAgents} />);
  await flush();
  expect(onAgents).toHaveBeenLastCalledWith({ claude: true, codex: false, gemini: true });
});
