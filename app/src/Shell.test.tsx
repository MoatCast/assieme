import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Snapshot } from "./bridge";

const snap = vi.hoisted(() => ({ current: null as Snapshot | null, report: null as ((s: Snapshot) => void) | null }));
vi.mock("./FirstRun", async () => {
  const { useEffect } = await import("react");
  return {
    default: ({ onSnapshot, onOpenTeam, onOpenSettings }: { onSnapshot?: (s: Snapshot) => void; onOpenTeam?: () => void; onOpenSettings?: (s: string) => void }) => {
      useEffect(() => {
        snap.report = onSnapshot ?? null;
        if (snap.current) onSnapshot?.(snap.current);
      }, [onSnapshot]);
      return <div><span>first run screen</span><button type="button" onClick={onOpenTeam}>Open my team</button><button type="button" onClick={() => onOpenSettings?.("privacy")}>privacy link</button></div>;
    },
  };
});
const undo = vi.hoisted(() => vi.fn());
vi.mock("./Window", () => ({
  default: ({ team, open, boardKey, onNewTeam, onNewTask, onMerged, onDiscarded, onBringOver }: {
    team: string | null; open: { role: string | null; view: string | null } | null; boardKey: number;
    onNewTeam: () => void; onNewTask: () => void; onMerged: (m: object) => void; onDiscarded: (t: string) => void; onBringOver: () => void;
  }) => (
    <div>
      <span>window for {team ?? "none"}</span><span>read {boardKey}</span>{open?.role && <span>selected {open.role}</span>}
      <span data-testid="open-view">{open?.view ?? ""}</span>
      <button type="button" onClick={onNewTeam}>empty new team</button>
      <button type="button" onClick={onNewTask}>board new task</button>
      <button type="button" onClick={onBringOver}>bring over</button>
      <button type="button" onClick={() => onMerged({ role: "builder-1", base: "main", problem: null, undo: "u1" })}>merge</button>
      <button type="button" onClick={() => onMerged({ role: "builder-1", base: "main", problem: "Couldn't clean up its copy: locked", undo: "u2" })}>merge, archive fails</button>
      <button type="button" onClick={() => onDiscarded("Discarded builder-1's work.")}>discard</button>
    </div>
  ),
}));
vi.mock("./BringOver", () => ({ default: ({ onImported }: { onImported: (d: { id: string; why: string }) => void }) => (
  <button type="button" onClick={() => onImported({ id: "d2", why: "from conductor" })}>import it</button>) }));
vi.mock("./Review", () => ({
  default: ({ team, role, onBack, onMerged, onDiscarded }: {
    team: string; role: string; onBack: () => void;
    onMerged: (m: object) => void; onDiscarded: (t: string) => void;
  }) => (
    <div>
      <span>review of {team}/{role}</span>
      <button type="button" onClick={onBack}>back</button>
      <button type="button" onClick={() => onMerged({ role, base: "main", problem: null, undo: "u1" })}>merge</button>
      <button type="button" onClick={() => onMerged({ role, base: "main", problem: "Couldn't clean up its copy: locked", undo: "u2" })}>merge, archive fails</button>
      <button type="button" onClick={() => onDiscarded("Discarded builder-1's work.")}>discard</button>
    </div>
  ),
  Notice: ({ children, ms, onDone, action }: { children: React.ReactNode; ms: number; onDone: () => void; action?: React.ReactNode }) => (
    <div>
      <span data-testid="notice">{children}</span>{action}
      <button type="button" onClick={onDone}>notice ends after {ms}</button>
    </div>
  ),
}));
vi.mock("./NewTeam", () => ({ default: ({ draft, onClose }: { draft?: { why: string } | null; onClose: () => void }) => (
  <div><span>new team screen{draft ? `, drafted: ${draft.why}` : ""}</span><button type="button" onClick={onClose}>close new team</button></div>
) }));
vi.mock("./Settings", () => ({ default: ({ onClose, onShowFirstRun, section, onOpenTeam }: { onClose: () => void; onShowFirstRun: () => void; section?: string; onOpenTeam: (t: string, r: string | null) => void }) => (
  <div><span>settings screen{section ? ` at ${section}` : ""}</span><button type="button" onClick={onClose}>close settings</button><button type="button" onClick={onShowFirstRun}>first run again</button>
    <button type="button" onClick={() => onOpenTeam("gamma", "r")}>open nightly review</button></div>
) }));
vi.mock("./Board", () => ({
  NewTask: ({ team, roles, onClose }: { team: string; roles: string[]; onClose: (added: boolean, note?: string) => void }) => (
    <div><span>new task for {team}: {roles.join(",")}</span><button type="button" onClick={() => onClose(true, "b wasn't told.")}>task added</button></div>
  ),
}));
vi.mock("./AddAgent", () => ({ default: ({ team, roles }: { team: string; roles: string[] }) => <div>add agent to {team}: {roles.join(",")}</div> }));
const setSetting = vi.hoisted(() => vi.fn());
const takeDraft = vi.hoisted(() => vi.fn());
let opened: (r: { team: string; role: string | null; view: string | null }) => void = () => {};
let settingsAsked: () => void = () => {};
let newTeamAsked: () => void = () => {};
vi.mock("./bridge", () => ({
  setSetting, reviewUndo: undo, takeDraft, windowTheme: () => Promise.resolve(),
  onOpen: (run: typeof opened) => { opened = run; return Promise.resolve(() => {}); },
  onOpenSettings: (run: () => void) => { settingsAsked = run; return Promise.resolve(() => {}); },
  onNewTeam: (run: () => void) => { newTeamAsked = run; return Promise.resolve(() => {}); },
  onPalette: () => Promise.resolve(() => {}),
}));

import Shell, { liveTeams } from "./Shell";

const row = (team: string, role: string, status = "idle", state = "live") =>
  ({ team, role, address: `${team}-${role}`, state, status, briefed: true });
const SNAP = (teams: ReturnType<typeof row>[], over: Partial<Snapshot> = {}): Snapshot => ({
  at: "", clis: { claude: true, codex: true, asm: true, hc: true, herdr: false }, agents: [], alerts: [], queue: [], stalls: [],
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
  expect(screen.queryByLabelText("Team")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Open my team" }));
  await flush();
  expect(setSetting).toHaveBeenCalledWith("first_run_done", true);
  expect(screen.getByText("window for none")).toBeTruthy();
});

test("opens on the window after that, on the neediest team", async () => {
  render(<Shell />);
  await flush();
  expect(screen.getByText("window for beta")).toBeTruthy();
});

test("no tabs: the window holds every view, and New task lives on the Board", async () => {
  render(<Shell />);
  await flush();
  expect(screen.queryByRole("tablist")).toBeNull();
  expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.queryByRole("button", { name: "New task" })).toBeNull();
  expect(screen.getByRole("button", { name: "Add agent" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "board new task" }));
  expect(screen.getByText("new task for beta: b")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "task added" }));
  expect(screen.queryByText("new task for beta: b")).toBeNull();
  expect(screen.getByText("read 1")).toBeTruthy(); // the board reads again at once
  expect(screen.getByTestId("notice").textContent).toBe("b wasn't told.");
});

test("a pick sticks while new teams appear, until that team stops", async () => {
  render(<Shell />);
  await flush();
  fireEvent.change(screen.getByLabelText("Team"), { target: { value: "gamma" } });
  expect(screen.getByText("window for gamma")).toBeTruthy();
  act(() => snap.report!(SNAP([row("alpha", "a", "blocked"), row("gamma", "c")])));
  expect(screen.getByText("window for gamma")).toBeTruthy();
  act(() => snap.report!(SNAP([row("alpha", "a", "blocked")])));
  expect(screen.getByText("window for alpha")).toBeTruthy();
});

test("the team picker lists found teams too", async () => {
  snap.current = SNAP([row("alpha", "a")], { found_teams: ["docs"] });
  render(<Shell />);
  await flush();
  const options = [...(screen.getByLabelText("Team") as HTMLSelectElement).options].filter((o) => !o.disabled).map((o) => o.value);
  expect(options).toEqual(["alpha", "docs", "new"]);
});

test("New team… in the team picker opens New team and keeps the team shown", async () => {
  snap.current = SNAP([row("alpha", "a")]);
  render(<Shell />);
  await flush();
  fireEvent.change(screen.getByLabelText("Team"), { target: { value: "new" } });
  expect(screen.getByText("new team screen")).toBeTruthy();
  expect((screen.getByLabelText("Team") as HTMLSelectElement).value).toBe("alpha");
});

test("New Team (⌘N) in the File menu opens New team", async () => {
  snap.current = SNAP([row("alpha", "a")]);
  render(<Shell />);
  await flush();
  act(() => newTeamAsked());
  expect(screen.getByText("new team screen")).toBeTruthy();
});

test("found teams alone still open, and started teams come first", () => {
  expect(liveTeams(SNAP([], { found_teams: ["docs"] }))).toEqual({ names: ["docs"], neediest: "docs" });
  expect(liveTeams(SNAP([row("a", "x")], { found_teams: ["a", "docs"] })).names).toEqual(["a", "docs"]);
  // A scheduled run's team stays in the picker after its agents stop, so its Activity can still be read.
  expect(liveTeams(SNAP([row("a", "x")], { found_teams: ["docs"], scheduled_teams: ["nightly", "a"] })).names).toEqual(["a", "docs", "nightly"]);
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

test("Privacy on First run opens Settings at Privacy, and the gear opens it plain", async () => {
  snap.current = SNAP([], { first_run_done: false });
  render(<Shell />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "privacy link" }));
  expect(screen.getByText("settings screen at privacy")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "close settings" }));
  fireEvent.click(screen.getByRole("button", { name: "Open my team" }));
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(screen.getByText("settings screen")).toBeTruthy();
});

test("First run has its own Settings button, named so it can be found", async () => {
  snap.current = SNAP([], { first_run_done: false });
  render(<Shell />);
  await flush();
  expect(screen.getByText("first run screen")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(screen.getByText("settings screen")).toBeTruthy();
});

test("Settings… in the app menu opens Settings from any screen", async () => {
  snap.current = SNAP([], { first_run_done: false });
  render(<Shell />);
  await flush();
  act(() => settingsAsked());
  expect(screen.getByText("settings screen")).toBeTruthy();
});

test("no team at all still reaches the window", async () => {
  snap.current = SNAP([]);
  render(<Shell />);
  await flush();
  expect(screen.getByText("window for none")).toBeTruthy();
  expect(screen.queryByLabelText("Team")).toBeNull();
});

test("the empty window's New team button opens New team", async () => {
  snap.current = SNAP([]);
  render(<Shell />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "empty new team" }));
  expect(screen.getByText("new team screen")).toBeTruthy();
});

test("the team shown stays fixed while another team becomes neediest", async () => {
  snap.current = SNAP([row("alpha", "a"), row("beta", "b", "blocked")]);
  render(<Shell />);
  await flush();
  expect(screen.getByText("window for beta")).toBeTruthy();
  // Switching under the person would move focus, and typing, to another team's agent.
  act(() => snap.report!(SNAP([row("alpha", "a", "blocked"), row("alpha", "b", "blocked"), row("beta", "b", "blocked")])));
  expect(screen.getByText("window for beta")).toBeTruthy();
});

async function openReview() {
  render(<Shell />);
  await flush();
}

test("a merge returns to the window with the notice, and Undo reports back", async () => {
  undo.mockResolvedValue({ role: "builder-1", base: "main" });
  await openReview();
  fireEvent.click(screen.getByRole("button", { name: "merge" }));
  expect(screen.getByTestId("notice").textContent).toBe("Merged builder-1 into main. Its copy was cleaned up.");
  expect(screen.getByText("window for beta")).toBeTruthy();
  expect(screen.getByRole("button", { name: "notice ends after 120000" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await act(async () => {});
  expect(undo).toHaveBeenCalledWith("u1");
  expect(screen.getByTestId("notice").textContent).toBe("Undone. builder-1's work is back, ready to review.");
});

test("a refused Undo says why, and an archive problem is shown", async () => {
  undo.mockRejectedValue("main has moved on since the merge, so it can't be undone here.");
  await openReview();
  fireEvent.click(screen.getByRole("button", { name: "merge, archive fails" }));
  expect(screen.getByTestId("notice").textContent).toContain("Couldn't clean up its copy: locked");
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await act(async () => {});
  expect(screen.getByTestId("notice").textContent).toContain("moved on");
});

test("a discard returns with its notice, which goes by itself", async () => {
  await openReview();
  fireEvent.click(screen.getByRole("button", { name: "discard" }));
  expect(screen.getByTestId("notice").textContent).toBe("Discarded builder-1's work.");
  fireEvent.click(screen.getByRole("button", { name: "notice ends after 8000" }));
  expect(screen.queryByTestId("notice")).toBeNull();
});

test("an assistant's draft opens in New team, once", async () => {
  takeDraft.mockReset();
  takeDraft.mockResolvedValue({ id: "d1", why: "small change" });
  snap.current = SNAP([], { draft: "d1" });
  render(<Shell />);
  await act(async () => {});
  expect(takeDraft).toHaveBeenCalledWith("d1");
  screen.getByText("new team screen, drafted: small change");
  takeDraft.mockResolvedValue(null); // the brain gave it away already
  fireEvent.click(screen.getByRole("button", { name: "close new team" }));
  await act(async () => {});
  expect(screen.queryByText(/new team screen/)).toBeNull();
});

test("a link from the menu bar switches the team, then selects the agent or opens its review in the panel", async () => {
  render(<Shell />);
  await act(async () => {});
  expect(screen.getByText("window for beta")).toBeTruthy();
  await act(async () => opened({ team: "alpha", role: "a", view: "terminals" }));
  expect(screen.getByText("window for alpha")).toBeTruthy();
  expect(screen.getByText("selected a")).toBeTruthy();
  expect(screen.getByTestId("open-view").textContent).toBe("");
  await act(async () => opened({ team: "gamma", role: "c", view: "review" }));
  expect(screen.getByText("window for gamma")).toBeTruthy();
  expect(screen.getByTestId("open-view").textContent).toBe("review");
  expect(screen.queryByText(/^review of/)).toBeNull(); // no full-page review any more
});

test("Settings opens a scheduled run's review in the panel", async () => {
  render(<Shell />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  fireEvent.click(screen.getByRole("button", { name: "open nightly review" }));
  expect(screen.queryByText("settings screen")).toBeNull();
  expect(screen.getByText("selected r")).toBeTruthy();
  expect(screen.getByTestId("open-view").textContent).toBe("review");
});

test("Bring your team over from the empty window opens its sheet, and an import opens New team", async () => {
  snap.current = SNAP([]);
  render(<Shell />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "bring over" }));
  fireEvent.click(screen.getByRole("button", { name: "import it" }));
  expect(screen.getByText("new team screen, drafted: from conductor")).toBeTruthy();
});
