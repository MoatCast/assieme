import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { TeamMap } from "./bridge";
import { MAP, fixRow, pane, waitingFor } from "./windowFixtures";

const b = vi.hoisted(() => ({ getSettings: vi.fn(), setSetting: vi.fn(), runFound: vi.fn(), activityUndo: vi.fn(), teamBand: vi.fn() }));
vi.mock("./bridge", () => b);
vi.mock("./Stage", () => ({ default: () => <div data-testid="band" />, StageToggle: () => <button type="button">band toggle</button>, useStageShown: () => true }));
vi.mock("./DailyLimits", () => ({ default: () => <div data-testid="limits-sheet" /> }));

import TeamOverview from "./TeamOverview";

const node = (role: string) => ({ role, state: "working" as const, since_s: 60, agent: "claude" as const, parent: null, group: null });
const flush = () => act(async () => {});
const show = async (map: TeamMap, over: Partial<Parameters<typeof TeamOverview>[0]> = {}) => {
  const props = { team: "shop", map, waiting: [], onSelectAgent: vi.fn(), onSeeAll: vi.fn(), onReview: vi.fn(), onRefresh: vi.fn(), onOpenFixes: vi.fn(), ...over };
  render(<TeamOverview {...props} />);
  await flush();
  return props;
};
const SETTINGS = (notice: boolean) => ({ fixes: { nudge: "auto", compact: "auto", tell: "auto", quiet: "auto" }, fixes_notice: notice, practices: {},
  notify: { needs_you: true, stalls: true, finished: true }, show_commands: false, groups: {}, first_run_done: true });

beforeEach(() => {
  vi.resetAllMocks();
  b.getSettings.mockResolvedValue(SETTINGS(false));
  b.setSetting.mockResolvedValue(SETTINGS(false));
  b.runFound.mockResolvedValue({ ok: true });
});
afterEach(cleanup);

test("the header: agents, the numbers and the daily limit", async () => {
  await show(MAP({ panes: [pane("a"), pane("b")], fixes_today: { fixed: 2, checking: 1 }, spend: { tokens: 1_200_000, waste_share: 0.1 },
    budget: { limit: 2_000_000, allowed: 2_000_000, used: 1_200_000, at_limit: "pause", agents: [] } }));
  expect(screen.getByRole("heading", { name: "Team overview" })).toBeTruthy();
  expect(screen.getByText("2 agents on shop")).toBeTruthy();
  expect(screen.getByTestId("numbers").textContent).toBe("2 problems fixed today1 being checked1.2M tokens today");
  expect(screen.getByText("Daily limit: 1.2M of 2M tokens")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Change" }));
  expect(screen.getByTestId("limits-sheet")).toBeTruthy();
});

test("no fixes yet and none open", async () => {
  await show(MAP({ panes: [pane("a")] }));
  expect(screen.getByTestId("numbers").textContent).toBe("0 problems so farNo problem open");
});

test("what Assieme did: at most three rows, then See all in Activity", async () => {
  const done = ["a", "b", "c", "d"].map((r, i) => fixRow(r, "fixed", `13:0${i}`));
  const props = await show(MAP({ panes: [pane("a")], done, fixes_today: { fixed: 4, checking: 0 } }));
  const did = screen.getByTestId("assieme-today");
  expect(within(did).getByText("What Assieme did today")).toBeTruthy();
  expect(within(did).getByText("· 4 fixed, 0 being checked")).toBeTruthy();
  expect(within(did).getAllByText("✓ Fixed")).toHaveLength(3);
  fireEvent.click(within(did).getByRole("button", { name: "See all in Activity ›" }));
  expect(props.onSeeAll).toHaveBeenCalled();
});

test("before the first fix it says what Assieme will do", async () => {
  await show(MAP({ panes: [pane("a")] }));
  expect(screen.getByText(/^No problems so far\. When an agent loops, repeats a refused command or runs low on memory, Assieme fixes it on its own/)).toBeTruthy();
});

test("the one-time notice shows until OK", async () => {
  b.getSettings.mockResolvedValue(SETTINGS(true));
  const props = await show(MAP({ panes: [pane("a")] }));
  expect(screen.getByText(/stuck agents, full memory, refused commands/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Settings › Fixes" }));
  expect(props.onOpenFixes).toHaveBeenCalled();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "OK" })); });
  expect(b.setSetting).toHaveBeenCalledWith("fixes_notice", false);
  expect(screen.queryByText(/stuck agents, full memory, refused commands/)).toBeNull();
});

test("a herdr team not handed over yet can be", async () => {
  const props = await show(MAP({ found: true, run_by_assieme: false, panes: [pane("a")] }));
  expect(screen.getByText("Assieme can fix this team's problems on its own too. Until then they stay with you.")).toBeTruthy();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Let Assieme run this team" })); });
  expect(b.runFound).toHaveBeenCalledWith("shop", true);
  expect(props.onRefresh).toHaveBeenCalled();
});

test("a map node selects its agent; the map opens full height", async () => {
  const props = await show(MAP({ panes: [pane("a"), pane("b")], nodes: [node("a"), node("b")] }));
  fireEvent.click(screen.getByTestId("node-b"));
  expect(props.onSelectAgent).toHaveBeenCalledWith("b");
  const strip = screen.getByTestId("overview-map");
  expect(strip.className).toContain("compact");
  fireEvent.click(screen.getByRole("button", { name: "Open the full map ›" }));
  expect(strip.className).not.toContain("compact");
});

test("other teams' work to review sits at the bottom, only when there is some", async () => {
  const props = await show(MAP({ panes: [pane("a")] }), { waiting: [waitingFor("a", 2), waitingFor("docs-writer", 3, "website")] });
  expect(screen.getByText("Ready to review in other teams:")).toBeTruthy();
  expect(screen.getByText("docs-writer · website · 3 commits")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  expect(props.onReview).toHaveBeenCalledWith("website", "docs-writer");
  cleanup();
  await show(MAP({ panes: [pane("a")] }), { waiting: [waitingFor("a", 2)] });
  expect(screen.queryByText("Ready to review in other teams:")).toBeNull();
});

test("the savings tip is gone", async () => {
  await show(MAP({ panes: [pane("a")], tips: [{ id: "a>b", from: "a", to: "b", share: 0.2 }] }));
  expect(screen.queryByText(/Save about/)).toBeNull();
});
