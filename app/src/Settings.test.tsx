import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const b = vi.hoisted(() => ({
  getSettings: vi.fn(), setSetting: vi.fn(), quietPairs: vi.fn(), quietSet: vi.fn(), listTeams: vi.fn(), stopTeam: vi.fn(),
  serviceInstall: vi.fn(), serviceRunning: vi.fn(), teamPresets: vi.fn(), setGroups: vi.fn(),
  assistantsStatus: vi.fn(), assistantsConnect: vi.fn(), assistantsDisconnect: vi.fn(),
}));
vi.mock("./bridge", () => b);

import ChangeGroups from "./ChangeGroups";
import Settings from "./Settings";

const VALUES = {
  fixes: { nudge: "ask", compact: "auto", tell: "ask" }, practices: {}, notify: { needs_you: true, stalls: true, finished: true },
  show_commands: false, groups: {}, first_run_done: true,
};
const flush = () => act(async () => {});
const open = async (section?: string) => {
  const calls = { close: vi.fn(), first: vi.fn() };
  render(<Settings team="shop" onClose={calls.close} onShowFirstRun={calls.first} />);
  await flush();
  if (section) {
    fireEvent.click(screen.getByRole("button", { name: section }));
    await flush();
  }
  return calls;
};

beforeEach(() => {
  vi.resetAllMocks();
  b.getSettings.mockResolvedValue(VALUES);
  b.setSetting.mockImplementation(async (k: string, v: unknown) => ({ ...VALUES, fixes: { ...VALUES.fixes, [k.split(".")[1]]: v } }));
  b.quietPairs.mockResolvedValue({ pairs: [], agents: [{ role: "a", cwd: "/w/a" }, { role: "b", cwd: "/w/b" }], held: 0 });
  b.quietSet.mockResolvedValue({ ok: true });
  b.listTeams.mockResolvedValue({ teams: [], found_agents: [] });
  b.serviceRunning.mockResolvedValue(true);
  b.assistantsStatus.mockResolvedValue({ clients: [], problems: [] });
  b.teamPresets.mockResolvedValue({ practices: {
    groups: [{ id: "messages", title: "Fewer, cheaper messages", summary: "Short notes.", rules: ["1", "2", "3"] }],
    basics: [{ title: "One coordinator", detail: "does outside work" }],
  } });
});
afterEach(cleanup);

test("six sections, and the sheet has no max width of its own", async () => {
  await open();
  for (const s of ["General", "Fixes", "Team practices", "Notifications", "Teams", "Privacy"]) expect(screen.getByRole("button", { name: s })).toBeTruthy();
  expect(document.querySelector(".settings-sheet")!.className).toBe("settings-sheet");
});

test("Back and Escape close", async () => {
  const calls = await open();
  fireEvent.click(screen.getByRole("button", { name: "‹ Back" }));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(calls.close).toHaveBeenCalledTimes(2);
});

test("General: running, then stopped with Start it", async () => {
  const calls = await open();
  expect(screen.getByText("Running")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Start it" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Show First run" }));
  expect(calls.first).toHaveBeenCalled();
  cleanup();
  b.serviceRunning.mockResolvedValue(false);
  b.serviceInstall.mockResolvedValue(undefined);
  await open();
  expect(screen.getByText("Stopped")).toBeTruthy();
  expect(screen.getByText(/nothing is watching them/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Start it" }));
  await flush();
  expect(b.serviceInstall).toHaveBeenCalled();
});

test("a fix's label follows its switch", async () => {
  await open("Fixes");
  expect(screen.getAllByText("Ask first").length).toBe(2);
  expect(screen.getByText("Automatic")).toBeTruthy();
  fireEvent.click(screen.getByRole("switch", { name: "Ask stuck agents to try another way" }));
  await flush();
  expect(b.setSetting).toHaveBeenCalledWith("fixes.nudge", "auto");
  expect(screen.getAllByText("Automatic").length).toBe(2);
});

test("the Fixes line no longer promises Undo for everything", async () => {
  await open("Fixes");
  expect(screen.getByText(/tell you after in “Done for you”/)).toBeTruthy();
  expect(screen.queryByText(/undo anything/i)).toBeNull();
});

test("pairs: Remove, and Add a pair from this team's agents", async () => {
  b.quietPairs.mockResolvedValue({ pairs: [{ from_cwd: "/w/a", to_cwd: "/w/b", label: "a to b", saved: 0.04 }], agents: [{ role: "a", cwd: "/w/a" }, { role: "b", cwd: "/w/b" }], held: 0 });
  await open("Fixes");
  expect(b.quietPairs).toHaveBeenCalledWith("shop");
  expect(screen.getByText("a → b")).toBeTruthy();
  expect(screen.getByText("saved about 4% today")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  await flush();
  expect(b.quietSet).toHaveBeenCalledWith({ from_cwd: "/w/a", to_cwd: "/w/b", label: "a to b", on: false });
  fireEvent.click(screen.getByRole("button", { name: "Add a pair" }));
  fireEvent.change(screen.getByLabelText("From"), { target: { value: "/w/b" } });
  fireEvent.change(screen.getByLabelText("To"), { target: { value: "/w/a" } });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  await flush();
  expect(b.quietSet).toHaveBeenLastCalledWith({ from_cwd: "/w/b", to_cwd: "/w/a", label: "b to a", on: true });
});

test("Add a pair needs a team in Assieme", async () => {
  b.quietPairs.mockResolvedValue({ pairs: [], agents: [], held: 0 });
  await open("Fixes");
  expect((screen.getByRole("button", { name: /Start a team/ }) as HTMLButtonElement).disabled).toBe(true);
});

test("practices: group switches, no chevrons or Improve button, See all shows basics", async () => {
  await open("Team practices");
  fireEvent.click(screen.getByRole("switch", { name: "Fewer, cheaper messages" }));
  await flush();
  expect(b.setSetting).toHaveBeenCalledWith("practices.messages", false);
  expect(screen.getByText("3 rules")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Improve this team" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "See all" }));
  expect(screen.getByText("One coordinator")).toBeTruthy();
});

test("notifications: all three switches work", async () => {
  await open("Notifications");
  fireEvent.click(screen.getByRole("switch", { name: "When a team stalls" }));
  await flush();
  expect(b.setSetting).toHaveBeenCalledWith("notify.stalls", false);
  fireEvent.click(screen.getByRole("switch", { name: "When work finishes or stops" }));
  await flush();
  expect(b.setSetting).toHaveBeenCalledWith("notify.finished", false);
});

test("Stop asks first; Keep it running changes nothing", async () => {
  b.listTeams.mockResolvedValue({ teams: [{ name: "shop", kind: "assieme", agents: 6 }, { name: "docs", kind: "found", agents: 1 }], found_agents: [] });
  b.stopTeam.mockResolvedValue({ stopped: 6 });
  await open("Teams");
  expect(screen.getByText("Started in Assieme · 6 agents running")).toBeTruthy();
  expect(screen.getByText("Found on this Mac · 1 agent, working alone")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Stop docs" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Stop shop" }));
  fireEvent.click(screen.getByRole("button", { name: "Keep it running" }));
  expect(b.stopTeam).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Stop shop" }));
  expect(screen.getByText(/ends its 6 agents/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Stop the team" }));
  await flush();
  expect(b.stopTeam).toHaveBeenCalledWith("shop");
});

test("privacy: the switch, and plain words about what's kept", async () => {
  await open("Privacy");
  fireEvent.click(screen.getByRole("switch", { name: "Show commands in full" }));
  await flush();
  expect(b.setSetting).toHaveBeenCalledWith("show_commands", true);
  expect(screen.getByText(/has no server of its own/)).toBeTruthy();
  expect(screen.getByText(/Not what your agents said or asked/)).toBeTruthy();
  expect(screen.queryByText(/Everything stays/)).toBeNull();
});

test("a failed change shows, and the switch stays as it was", async () => {
  b.setSetting.mockRejectedValue(new Error("no such setting"));
  await open("Privacy");
  fireEvent.click(screen.getByRole("switch", { name: "Show commands in full" }));
  await flush();
  expect(screen.getByText(/no such setting/)).toBeTruthy();
  expect((screen.getByRole("switch", { name: "Show commands in full" }) as HTMLInputElement).checked).toBe(false);
});

const FOUND = { teams: [], found_agents: [
  { name: "planner", cwd: "/w/one", group: "checkout" }, { name: "writer", cwd: "/w/two", group: "docs" },
] };

test("Change the groups: lists found agents, and Save keeps other choices", async () => {
  b.listTeams.mockResolvedValue(FOUND);
  b.getSettings.mockResolvedValue({ ...VALUES, groups: { "/w/old": "kept" } });
  b.setGroups.mockResolvedValue(VALUES);
  const close = vi.fn();
  render(<ChangeGroups onClose={close} />);
  await flush();
  fireEvent.change(screen.getByLabelText("Group for writer"), { target: { value: "checkout" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await flush();
  expect(b.setGroups).toHaveBeenCalledWith({ "/w/old": "kept", "/w/two": "checkout" });
  expect(close).toHaveBeenCalled();
});

test("Change the groups: New group is a menu option that asks for a name", async () => {
  b.listTeams.mockResolvedValue(FOUND);
  b.getSettings.mockResolvedValue(VALUES);
  b.setGroups.mockResolvedValue(VALUES);
  render(<ChangeGroups onClose={vi.fn()} />);
  await flush();
  const menu = screen.getByLabelText("Group for writer") as HTMLSelectElement;
  fireEvent.change(menu, { target: { value: [...menu.options].find((o) => o.text === "+ New group")!.value } });
  const name = screen.getByLabelText("New group for writer");
  fireEvent.change(name, { target: { value: "mine" } });
  fireEvent.keyDown(name, { key: "Enter" });
  expect((screen.getByLabelText("Group for writer") as HTMLSelectElement).value).toBe("mine");
  expect([...(screen.getByLabelText("Group for planner") as HTMLSelectElement).options].map((o) => o.text)).toContain("mine");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await flush();
  expect(b.setGroups).toHaveBeenCalledWith({ "/w/two": "mine" });
});

test("Change the groups: Cancel changes nothing, and an empty list says so", async () => {
  const close = vi.fn();
  render(<ChangeGroups onClose={close} />);
  await flush();
  expect(screen.getByText(/No agents started outside Assieme/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(close).toHaveBeenCalled();
  expect(b.setGroups).not.toHaveBeenCalled();
});

const CLIENTS = (connected: boolean) => ({
  problems: [],
  clients: [
    { id: "claude", label: "Claude Code", connected }, { id: "codex", label: "Codex", connected },
    { id: "desktop", label: "the Claude app", connected },
  ],
});

test("Connect connects every assistant, and Disconnect takes it back", async () => {
  b.assistantsStatus.mockResolvedValue(CLIENTS(false));
  b.assistantsConnect.mockResolvedValue(CLIENTS(true));
  b.assistantsDisconnect.mockResolvedValue(CLIENTS(false));
  await open();
  screen.getByText("Let Claude Code, Codex and the Claude app see your teams and help design new ones. They ask you before changing anything.");
  expect(screen.queryByText("Connected")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  await flush();
  expect(b.assistantsConnect).toHaveBeenCalledOnce();
  screen.getByText("Connected");
  screen.getByText("Claude Code, Codex and the Claude app can see your teams and help design new ones.");
  fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
  await flush();
  expect(b.assistantsDisconnect).toHaveBeenCalledOnce();
  screen.getByRole("button", { name: "Connect" });
});

test("Connecting partly says what went wrong and offers Connect again", async () => {
  b.assistantsStatus.mockResolvedValue(CLIENTS(false));
  b.assistantsConnect.mockResolvedValue({ problems: ["Codex: no such command"], clients: [
    { id: "claude", label: "Claude Code", connected: true }, { id: "codex", label: "Codex", connected: false },
  ] });
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  await flush();
  screen.getByText("Codex: no such command");
  screen.getByRole("button", { name: "Connect" });
});

test("With no assistant installed there's nothing to press", async () => {
  b.assistantsStatus.mockResolvedValue({ clients: [], problems: [] });
  await open();
  screen.getByText(/Install Claude Code or Codex/);
  expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
});

test("While the Claude app is open the others still connect, and it says how to add it", async () => {
  const clients = (connected: boolean) => ({ problems: [], clients: [
    { id: "claude", label: "Claude Code", connected }, { id: "desktop", label: "the Claude app", connected: false, blocked: true },
  ] });
  b.assistantsStatus.mockResolvedValue(clients(false));
  b.assistantsConnect.mockResolvedValue(clients(true));
  await open();
  screen.getByText("Let Claude Code see your teams and help design new ones. They ask you before changing anything. To add the Claude app, quit it, then come back here.");
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  await flush();
  screen.getByText("Connected");
  screen.getByText("Claude Code can see your teams and help design new ones. To add the Claude app, quit it, then come back here.");
});

test("With only the Claude app, and it open, there's nothing to press yet", async () => {
  b.assistantsStatus.mockResolvedValue({ problems: [], clients: [{ id: "desktop", label: "the Claude app", connected: false, blocked: true }] });
  await open();
  screen.getByText(/quit it, then come back here/);
  expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
});

test("Privacy says what assistants can read and how long reports and drafts stay", async () => {
  await open("Privacy");
  screen.getByText("What an agent said is blocking it, until its next turn. A team an assistant drafted, until it opens in New team.");
  screen.getByText("Once connected, an assistant can read your agents’ terminals when asked. What it reads goes wherever it normally sends your work.");
});

test("A service older than the app is said in plain words, with the fix", async () => {
  b.assistantsStatus.mockRejectedValueOnce("no method 'assistants.status'");
  b.assistantsStatus.mockResolvedValue(CLIENTS(false));
  b.serviceInstall.mockResolvedValue(undefined);
  await open();
  screen.getByText(/background service is out of date/);
  expect(screen.queryByText(/no method/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Update it" }));
  await flush();
  expect(b.serviceInstall).toHaveBeenCalledOnce();
  screen.getByRole("button", { name: "Connect" });
  expect(screen.queryByRole("button", { name: "Update it" })).toBeNull();
});

test("A failed update says why and can be tried again", async () => {
  b.assistantsStatus.mockRejectedValue("no method 'assistants.status'");
  b.serviceInstall.mockRejectedValue("launchctl failed");
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Update it" }));
  await flush();
  screen.getByText("launchctl failed");
  screen.getByRole("button", { name: "Update it" });
});
