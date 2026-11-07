import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Snapshot } from "./bridge";

const snap = vi.hoisted(() => ({ current: null as Snapshot | null }));
vi.mock("./FirstRun", async () => {
  const { useEffect } = await import("react");
  return {
    default: ({ onSnapshot }: { onSnapshot?: (s: Snapshot) => void }) => {
      useEffect(() => { if (snap.current) onSnapshot?.(snap.current); }, [onSnapshot]);
      return <div>first run screen</div>;
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
