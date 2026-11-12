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
vi.mock("./Terminals", () => ({
  default: ({ team, onNewTeam, onReview }: { team: string | null; onNewTeam: () => void; onReview: (t: string, r: string) => void }) => (
    <div>
      <span>terminals for {team ?? "none"}</span>
      <button type="button" onClick={onNewTeam}>empty new team</button>
      <button type="button" onClick={() => onReview("shop", "builder-1")}>open review</button>
    </div>
  ),
}));
vi.mock("./Review", () => ({
  default: ({ team, role, onBack, onMerged, onDiscarded }: {
    team: string; role: string; onBack: () => void;
    onMerged: (m: object) => void; onDiscarded: (t: string) => void;
  }) => (
    <div>
      <span>review of {team}/{role}</span>
      <button type="button" onClick={onBack}>back</button>
      <button type="button" onClick={() => onMerged({ role, base: "main", problem: null, undo: "u1" })}>merge</button>
      <button type="button" onClick={() => onMerged({ role, base: "main", problem: "Couldn't archive its worktree: locked", undo: "u2" })}>merge, archive fails</button>
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
vi.mock("./Team", () => ({
  default: ({ team, onOpenTerminal }: { team: string | null; onOpenTerminal: (r: string) => void }) => (
    <div><span>team tab for {team ?? "none"}</span><button type="button" onClick={() => onOpenTerminal("a")}>open a</button></div>
  ),
}));
vi.mock("./Activity", () => ({ default: ({ team }: { team: string | null }) => <div>activity for {team}</div> }));
vi.mock("./Settings", () => ({ default: ({ onClose, onShowFirstRun, section }: { onClose: () => void; onShowFirstRun: () => void; section?: string }) => (
  <div><span>settings screen{section ? ` at ${section}` : ""}</span><button type="button" onClick={onClose}>close settings</button><button type="button" onClick={onShowFirstRun}>first run again</button></div>
) }));
vi.mock("./Board", () => ({
  NewTask: ({ team, roles, onClose }: { team: string; roles: string[]; onClose: (added: boolean, note?: string) => void }) => (
    <div><span>new task for {team}: {roles.join(",")}</span><button type="button" onClick={() => onClose(true, "b wasn't told.")}>task added</button></div>
  ),
  default: ({ team, refreshKey, onReview, onOpenTerminal }: { team: string | null; refreshKey: number; onReview: (t: string, r: string) => void; onOpenTerminal: (r: string) => void }) => (
    <div>
      <span>board for {team ?? "none"}</span><span>read {refreshKey}</span>
      <button type="button" onClick={() => onReview("beta", "b")}>card review</button>
      <button type="button" onClick={() => onOpenTerminal("b")}>card terminal</button>
    </div>
  ),
}));
vi.mock("./AddAgent", () => ({ default: ({ team, roles }: { team: string; roles: string[] }) => <div>add agent to {team}: {roles.join(",")}</div> }));
const setSetting = vi.hoisted(() => vi.fn());
const takeDraft = vi.hoisted(() => vi.fn());
let opened: (r: { team: string; role: string | null; view: string | null }) => void = () => {};
vi.mock("./bridge", () => ({
  setSetting, reviewUndo: undo, takeDraft, windowTheme: () => Promise.resolve(),
  onOpen: (run: typeof opened) => { opened = run; return Promise.resolve(() => {}); },
}));

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
});

test("the Board tab sits after Team; its cards open Review and Terminals", async () => {
  render(<Shell />);
  await flush();
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Team", "Board", "Terminals", "Activity"]);
  fireEvent.click(screen.getByRole("tab", { name: "Board" }));
  expect(screen.getByText("board for beta")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add agent" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "New task" }));
  expect(screen.getByText("new task for beta: b")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "task added" }));
  expect(screen.queryByText("new task for beta: b")).toBeNull();
  expect(screen.getByText("read 1")).toBeTruthy(); // the board reads again at once
  expect(screen.getByTestId("notice").textContent).toBe("b wasn't told.");
  fireEvent.click(screen.getByRole("button", { name: "card review" }));
  expect(screen.getByText("review of beta/b")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "back" }));
  expect(screen.getByText("board for beta")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "card terminal" }));
  expect(screen.getByText("terminals for beta")).toBeTruthy();
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

async function openReview() {
  render(<Shell />);
  await flush();
  fireEvent.click(screen.getByRole("tab", { name: "Terminals" }));
  fireEvent.click(screen.getByRole("button", { name: "open review" }));
}

test("Review replaces the frame and goes back to Terminals", async () => {
  await openReview();
  expect(screen.getByText("review of shop/builder-1")).toBeTruthy();
  expect(screen.queryByRole("tablist")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "back" }));
  expect(screen.getByRole("button", { name: "open review" })).toBeTruthy();
});

test("a merge returns to Terminals with the notice, and Undo reports back", async () => {
  undo.mockResolvedValue({ role: "builder-1", base: "main" });
  await openReview();
  fireEvent.click(screen.getByRole("button", { name: "merge" }));
  expect(screen.getByTestId("notice").textContent).toBe("Merged builder-1 into main. Its worktree is archived.");
  expect(screen.getByRole("button", { name: "open review" })).toBeTruthy();
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
  expect(screen.getByTestId("notice").textContent).toContain("Couldn't archive its worktree: locked");
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

test("the menu bar can open an agent's terminal or its review", async () => {
  render(<Shell />);
  await act(async () => {});
  await act(async () => opened({ team: "shop", role: "planner", view: "terminals" }));
  expect(screen.queryByText("new team screen")).toBeNull();
  await act(async () => opened({ team: "shop", role: "builder-1", view: "review" }));
  expect(document.body.textContent).toContain("builder-1");
});
