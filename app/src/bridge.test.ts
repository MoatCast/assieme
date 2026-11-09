import { beforeEach, expect, test, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...a: unknown[]) => invoke(...a),
  Channel: class {
    onmessage: (m: unknown) => void = () => {};
  },
}));

const dialogOpen = vi.fn();
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: (...a: unknown[]) => dialogOpen(...a) }));

import * as bridge from "./bridge";

beforeEach(() => invoke.mockReset());

test("snapshot passes method name", async () => {
  invoke.mockResolvedValue({ teams: [] });
  await bridge.snapshot();
  expect(invoke).toHaveBeenCalledWith("brain_call", { method: "brain.snapshot", params: {} });
});

test("openTerminal streams bytes and routes input, resize and close to its id", async () => {
  invoke.mockResolvedValueOnce(7);
  const got: Uint8Array[] = [];
  const h = await bridge.openTerminal("t3", 100, 30, true, (b) => got.push(b), () => {});
  const args = invoke.mock.calls[0][1];
  expect(invoke.mock.calls[0][0]).toBe("terminal_open");
  expect([args.address, args.cols, args.rows, args.observe]).toEqual(["t3", 100, 30, true]);
  args.onData.onmessage(new Uint8Array([104, 105]).buffer);
  expect(Array.from(got[0])).toEqual([104, 105]);
  await h.input("hi");
  expect(invoke).toHaveBeenLastCalledWith("terminal_input", { id: 7, data: [104, 105] });
  await h.resize(120, 40);
  expect(invoke).toHaveBeenLastCalledWith("terminal_resize", { id: 7, cols: 120, rows: 40 });
  await h.close();
  expect(invoke).toHaveBeenLastCalledWith("terminal_close", { id: 7 });
});

test("readTerminal names the session", async () => {
  invoke.mockResolvedValue("text");
  expect(await bridge.readTerminal("default", "t1")).toBe("text");
  expect(invoke).toHaveBeenCalledWith("terminal_read", { session: "default", address: "t1" });
});

test("suggestTeam waits up to 3 minutes; other calls keep the default", async () => {
  invoke.mockResolvedValue({ roles: [], why: "" });
  const a = { project: "/p", size: "feature", review: "every", budget: "lean" };
  await bridge.suggestTeam(a);
  expect(invoke).toHaveBeenLastCalledWith("brain_call", { method: "team.suggest", params: a, timeoutS: 180 });
  const form = { project: "/p", task: "t", roles: [], practices: {} };
  await bridge.startTeam(form);
  expect(invoke).toHaveBeenLastCalledWith("brain_call", { method: "team.start", params: form, timeoutS: 120 });
  await bridge.checkProject("/p");
  expect(invoke).toHaveBeenLastCalledWith("brain_call", { method: "project.check", params: { path: "/p" } });
  await bridge.teamPresets();
  expect(invoke).toHaveBeenLastCalledWith("brain_call", { method: "team.presets", params: {} });
});

test("pickFolder asks for one folder", async () => {
  dialogOpen.mockResolvedValue("/Users/me/shop");
  expect(await bridge.pickFolder()).toBe("/Users/me/shop");
  expect(dialogOpen).toHaveBeenCalledWith({ directory: true, multiple: false });
  dialogOpen.mockResolvedValue(null);
  expect(await bridge.pickFolder()).toBeNull();
});

test("fieldOf splits the brain's field from its message", () => {
  expect(bridge.fieldOf("role:1: Names use x")).toEqual({ field: "role:1", message: "Names use x" });
  expect(bridge.fieldOf("project: Not git")).toEqual({ field: "project", message: "Not git" });
  expect(bridge.fieldOf("suggest: Couldn't")).toEqual({ field: "suggest", message: "Couldn't" });
  expect(bridge.fieldOf("no method 'x'")).toEqual({ field: "start", message: "no method 'x'" });
  expect(bridge.fieldOf(new Error("roles: Add at least one agent."))).toEqual({ field: "roles", message: "Add at least one agent." });
});

test("teamView and paneAct call the brain", async () => {
  invoke.mockResolvedValue({ team: "shop", panes: [], strip: [] });
  await bridge.teamView("shop");
  expect(invoke).toHaveBeenLastCalledWith("brain_call", { method: "team.view", params: { team: "shop" } });
  const a = { team: "shop", role: "planner", fix: "f1", button: "b2" };
  await bridge.paneAct(a);
  expect(invoke).toHaveBeenLastCalledWith("brain_call", { method: "pane.act", params: a });
});

test("setup calls use the brain's method names and arguments", async () => {
  invoke.mockResolvedValue({});
  const calls: [() => unknown, string, object][] = [
    [() => bridge.setupStatus(), "setup.status", {}], [() => bridge.setupCommands(), "setup.commands", {}],
    [() => bridge.installTools(), "setup.install_tools", {}],
    [() => bridge.installAgent("codex"), "setup.install_agent", { agent: "codex" }],
    [() => bridge.signinAgent("claude"), "setup.signin_agent", { agent: "claude" }],
    [() => bridge.terminalState("t9"), "setup.terminal_state", { address: "t9" }],
    [() => bridge.installStop("t9"), "setup.install_stop", { address: "t9" }],
  ];
  for (const [call, method, params] of calls) {
    await call();
    expect(invoke).toHaveBeenLastCalledWith("brain_call", { method, params });
  }
});

test("the new brain calls use the brain's method names", async () => {
  const { activityList, activityUndo, agentDetail, listTeams, quietPairs, quietSet, setGroups, setSetting, stopTeam, teamMap, tipApply, getSettings } = bridge;
  invoke.mockResolvedValue({});
  const calls: [() => unknown, string, object][] = [
    [() => teamMap("t"), "team.map", { team: "t" }], [() => agentDetail("t", "r"), "agent.detail", { team: "t", role: "r" }],
    [() => tipApply("t", "x"), "tip.apply", { team: "t", tip: "x" }], [() => stopTeam("t"), "team.stop", { team: "t" }],
    [() => listTeams(), "teams.list", {}], [() => activityList("t", "you"), "activity.list", { team: "t", filter: "you" }],
    [() => activityUndo("i"), "activity.undo", { id: "i" }], [() => getSettings(), "settings.get", {}],
    [() => setSetting("k", 1), "settings.set", { key: "k", value: 1 }], [() => quietPairs("t"), "quiet.pairs", { team: "t" }],
    [() => quietSet({ from_cwd: "a", to_cwd: "b", label: "l", on: true }), "quiet.set", { from_cwd: "a", to_cwd: "b", label: "l", on: true }],
    [() => setGroups({ a: "b" }), "groups.set", { groups: { a: "b" } }],
  ];
  for (const [call, method, params] of calls) {
    await call();
    expect(invoke).toHaveBeenLastCalledWith("brain_call", { method, params });
  }
});
