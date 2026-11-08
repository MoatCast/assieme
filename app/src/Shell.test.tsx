import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Snapshot } from "./bridge";

const snap = vi.hoisted(() => ({ current: null as Snapshot | null, report: null as ((s: Snapshot) => void) | null }));
vi.mock("./FirstRun", async () => {
  const { useEffect } = await import("react");
  return {
    default: ({ onSnapshot }: { onSnapshot?: (s: Snapshot) => void }) => {
      useEffect(() => {
        snap.report = onSnapshot ?? null;
        if (snap.current) onSnapshot?.(snap.current);
      }, [onSnapshot]);
      return <div>first run screen</div>;
    },
  };
});
const undo = vi.hoisted(() => vi.fn());
vi.mock("./bridge", () => ({ reviewUndo: undo }));
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
vi.mock("./NewTeam", () => ({ default: () => <div>new team screen</div> }));

import Shell from "./Shell";

const row = (team: string, role: string, status = "idle", state = "live") =>
  ({ team, role, address: `${team}-${role}`, state, status, briefed: true });
const SNAP = (teams: ReturnType<typeof row>[]): Snapshot => ({
  at: "", clis: { claude: true, codex: true, hc: true, herdr: false }, agents: [], alerts: [], queue: [], stalls: [],
  teams, starting: [],
});

const flush = () => act(async () => {});
beforeEach(() => { snap.current = SNAP([row("alpha", "a"), row("beta", "b", "blocked"), row("gamma", "c")]); });
afterEach(cleanup);

test("tabs switch between First run and Terminals; picker hidden on First run", async () => {
  render(<Shell />);
  await flush();
  expect(screen.getByText("first run screen")).toBeTruthy();
  expect(screen.queryByLabelText("Team")).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "Terminals" }));
  expect(screen.getByText("terminals for beta")).toBeTruthy();
  expect(screen.getByLabelText("Team")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "First run" }));
  expect(screen.queryByText("terminals for beta")).toBeNull();
});

test("the picker lists live teams, opens on the one needing you most, and a pick sticks", async () => {
  render(<Shell />);
  await flush();
  fireEvent.click(screen.getByRole("tab", { name: "Terminals" }));
  const pick = screen.getByLabelText("Team") as HTMLSelectElement;
  expect(Array.from(pick.options).map((o) => o.value)).toEqual(["alpha", "beta", "gamma"]);
  expect(pick.value).toBe("beta");
  fireEvent.change(pick, { target: { value: "alpha" } });
  expect(screen.getByText("terminals for alpha")).toBeTruthy();
});

test("with no team needing you it opens on the last listed; no live team gives none", async () => {
  snap.current = SNAP([row("alpha", "a"), row("gamma", "c"), row("old", "x", "idle", "finished")]);
  render(<Shell />);
  await flush();
  fireEvent.click(screen.getByRole("tab", { name: "Terminals" }));
  expect(screen.getByText("terminals for gamma")).toBeTruthy();
  cleanup();
  snap.current = SNAP([]);
  render(<Shell />);
  await flush();
  fireEvent.click(screen.getByRole("tab", { name: "Terminals" }));
  expect(screen.getByText("terminals for none")).toBeTruthy();
});

test("New team from the empty Terminals opens New team", async () => {
  snap.current = SNAP([]);
  render(<Shell />);
  await flush();
  fireEvent.click(screen.getByRole("tab", { name: "Terminals" }));
  fireEvent.click(screen.getByText("empty new team"));
  expect(screen.getByText("new team screen")).toBeTruthy();
});

test("the team stays fixed while Terminals is open", async () => {
  render(<Shell />);
  await flush();
  fireEvent.click(screen.getByRole("tab", { name: "Terminals" }));
  expect(screen.getByText("terminals for beta")).toBeTruthy();
  act(() => snap.report!(SNAP([row("alpha", "a", "blocked"), row("alpha", "b", "blocked"), row("beta", "b", "blocked")])));
  expect(screen.getByText("terminals for beta")).toBeTruthy();
});

async function openReview() {
  render(<Shell />);
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
