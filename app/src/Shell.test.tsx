import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Snapshot } from "./bridge";

const snap = vi.hoisted(() => ({ current: null as Snapshot | null, report: null as ((s: Snapshot) => void) | null }));
vi.mock("./FirstRun", async () => {
  const { useEffect } = await import("react");
  return {
    default: ({ onSnapshot, onOpenTeam }: { onSnapshot?: (s: Snapshot) => void; onOpenTeam?: () => void }) => {
      useEffect(() => {
        snap.report = onSnapshot ?? null;
        if (snap.current) onSnapshot?.(snap.current);
      }, [onSnapshot]);
      return <div><span>first run screen</span><button type="button" onClick={onOpenTeam}>Open my team</button></div>;
    },
  };
});
vi.mock("./Terminals", () => ({
  default: ({ team, onNewTeam }: { team: string | null; onNewTeam: () => void }) => (
    <div>
      <span>terminals for {team ?? "none"}</span>
      <button type="button" onClick={onNewTeam}>empty new team</button>
    </div>
  ),
}));
vi.mock("./NewTeam", () => ({ default: () => <div>new team screen</div> }));
vi.mock("./Team", () => ({
  default: ({ team, onOpenTerminal }: { team: string | null; onOpenTerminal: (r: string) => void }) => (
    <div><span>team tab for {team ?? "none"}</span><button type="button" onClick={() => onOpenTerminal("a")}>open a</button></div>
  ),
}));
vi.mock("./Activity", () => ({ default: ({ team }: { team: string | null }) => <div>activity for {team}</div> }));
vi.mock("./Settings", () => ({ default: ({ onClose, onShowFirstRun }: { onClose: () => void; onShowFirstRun: () => void }) => (
  <div><span>settings screen</span><button type="button" onClick={onClose}>close settings</button><button type="button" onClick={onShowFirstRun}>first run again</button></div>
) }));
vi.mock("./AddAgent", () => ({ default: ({ team, roles }: { team: string; roles: string[] }) => <div>add agent to {team}: {roles.join(",")}</div> }));
const setSetting = vi.hoisted(() => vi.fn());
vi.mock("./bridge", () => ({ setSetting }));

import Shell, { liveTeams } from "./Shell";

const row = (team: string, role: string, status = "idle", state = "live") =>
  ({ team, role, address: `${team}-${role}`, state, status, briefed: true });
const SNAP = (teams: ReturnType<typeof row>[], over: Partial<Snapshot> = {}): Snapshot => ({
  at: "", clis: { claude: true, codex: true, hc: true, herdr: false }, agents: [], alerts: [], queue: [], stalls: [],
  teams, starting: [], first_run_done: true, ...over,
});

const flush = () => act(async () => {});
beforeEach(() => {
  setSetting.mockReset();
  setSetting.mockResolvedValue({});
  snap.current = SNAP([row("alpha", "a"), row("beta", "b", "blocked"), row("gamma", "c")]);
});
afterEach(cleanup);

test("opens on First run until it's done, with no top bar", async () => {
  snap.current = SNAP([], { first_run_done: false });
  render(<Shell />);
  await flush();
  expect(screen.getByText("first run screen")).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Team" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Open my team" }));
  await flush();
  expect(setSetting).toHaveBeenCalledWith("first_run_done", true);
  expect(screen.getByRole("tab", { name: "Team" }).getAttribute("aria-selected")).toBe("true");
});

test("opens on Team after that, on the neediest team", async () => {
  render(<Shell />);
  await flush();
  expect(screen.getByText("team tab for beta")).toBeTruthy();
});

test("tabs switch between Team, Terminals and Activity", async () => {
  render(<Shell />);
  await flush();
  fireEvent.click(screen.getByRole("tab", { name: "Terminals" }));
  expect(screen.getByText("terminals for beta")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
  expect(screen.getByText("activity for beta")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Team" }));
  expect(screen.getByText("team tab for beta")).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Board" })).toBeNull();
});

test("Team's Open terminal goes to Terminals", async () => {
  render(<Shell />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "open a" }));
  expect(screen.getByText("terminals for beta")).toBeTruthy();
});

test("a pick sticks while new teams appear, until that team stops", async () => {
  render(<Shell />);
  await flush();
  fireEvent.change(screen.getByLabelText("Team"), { target: { value: "gamma" } });
  expect(screen.getByText("team tab for gamma")).toBeTruthy();
  act(() => snap.report!(SNAP([row("alpha", "a", "blocked"), row("gamma", "c")])));
  expect(screen.getByText("team tab for gamma")).toBeTruthy();
  act(() => snap.report!(SNAP([row("alpha", "a", "blocked")])));
  expect(screen.getByText("team tab for alpha")).toBeTruthy();
});

test("the team picker lists found teams too", async () => {
  snap.current = SNAP([row("alpha", "a")], { found_teams: ["docs"] });
  render(<Shell />);
  await flush();
  const options = [...(screen.getByLabelText("Team") as HTMLSelectElement).options].map((o) => o.value);
  expect(options).toEqual(["alpha", "docs"]);
});

test("found teams alone still open, and started teams come first", () => {
  expect(liveTeams(SNAP([], { found_teams: ["docs"] }))).toEqual({ names: ["docs"], neediest: "docs" });
  expect(liveTeams(SNAP([row("a", "x")], { found_teams: ["a", "docs"] })).names).toEqual(["a", "docs"]);
});

test("Add agent is there for a team started in Assieme, not a found one", async () => {
  snap.current = SNAP([row("alpha", "a")], { found_teams: ["docs"] });
  render(<Shell />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Add agent" }));
  expect(screen.getByText("add agent to alpha: a")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Team"), { target: { value: "docs" } });
  expect(screen.queryByRole("button", { name: "Add agent" })).toBeNull();
});

test("Settings opens from the gear, closes, and can show First run", async () => {
  render(<Shell />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(screen.getByText("settings screen")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "close settings" }));
  expect(screen.queryByText("settings screen")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  fireEvent.click(screen.getByRole("button", { name: "first run again" }));
  expect(screen.getByText("first run screen")).toBeTruthy();
});

test("no team at all still reaches the tabs", async () => {
  snap.current = SNAP([]);
  render(<Shell />);
  await flush();
  expect(screen.getByText("team tab for none")).toBeTruthy();
  expect(screen.queryByLabelText("Team")).toBeNull();
});

test("the empty Terminals view's New team button opens New team", async () => {
  snap.current = SNAP([]);
  render(<Shell />);
  await flush();
  fireEvent.click(screen.getByRole("tab", { name: "Terminals" }));
  fireEvent.click(screen.getByRole("button", { name: "empty new team" }));
  expect(screen.getByText("new team screen")).toBeTruthy();
});

test("the team stays fixed while Terminals is open, however it was reached", async () => {
  for (const reach of ["tab", "card"]) {
    snap.current = SNAP([row("alpha", "a"), row("beta", "b", "blocked")]);
    render(<Shell />);
    await flush();
    fireEvent.click(reach === "tab" ? screen.getByRole("tab", { name: "Terminals" }) : screen.getByRole("button", { name: "open a" }));
    expect(screen.getByText("terminals for beta")).toBeTruthy();
    act(() => snap.report!(SNAP([row("alpha", "a", "blocked"), row("alpha", "b", "blocked"), row("beta", "b", "blocked")])));
    expect(screen.getByText("terminals for beta")).toBeTruthy();
    cleanup();
  }
});
