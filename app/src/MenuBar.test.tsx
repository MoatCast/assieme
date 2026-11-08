import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const pushed = vi.hoisted(() => ({ run: (_: unknown) => {} }));
const b = vi.hoisted(() => ({
  menuState: vi.fn(), paneAct: vi.fn(), windowOpen: vi.fn(), menuFit: vi.fn(() => Promise.resolve()),
  notificationsOff: vi.fn(() => Promise.resolve(false)), notificationSettingsOpen: vi.fn(() => Promise.resolve()),
  notificationsPause: vi.fn(), notificationsResume: vi.fn(),
  onMenuOpened: vi.fn(() => Promise.resolve(() => {})),
  onMenuState: vi.fn((run: (m: unknown) => void) => { pushed.run = run; return Promise.resolve(() => {}); }),
}));
vi.mock("./bridge", () => b);
vi.mock("./Terminals", () => ({ ago: (s: number | null) => (s ? `${Math.round(s / 60)}m` : "") }));
vi.mock("./Team", () => ({ tokens: (n: number) => `${n / 1e6}M` }));

import MenuBar from "./MenuBar";

const ASKING = {
  role: "planner", state: "asking", since_s: 5, text: "Behind a flag, or to main?", says: "", fix: "f1",
  buttons: [{ id: "a", label: "Behind a flag", kind: "answer" }, { id: "r", label: "Reply", kind: "reply" }],
};
const STUCK = { role: "builder-3", state: "stuck", since_s: 1080, text: "", says: "seems stuck", fix: "f2", buttons: [{ id: "t", label: "Try another way", kind: "action" }] };
const STATE = {
  count: 2, spend_tokens: 4200000, auto_fixes: 1, paused_until: null,
  teams: [{ name: "checkout-service", where: "This Mac", need: 2, items: [ASKING, STUCK] }, { name: "docs-site", where: "This Mac", need: 0, items: [] }],
};
const flush = () => act(async () => {});

beforeEach(() => {
  vi.clearAllMocks();
  b.menuState.mockResolvedValue(STATE);
  b.paneAct.mockResolvedValue({ ok: true, detail: "" });
});
afterEach(cleanup);

test("shows what needs you, every team, and today's spend", async () => {
  render(<MenuBar />);
  await flush();
  expect(screen.getByText("2 need you")).toBeTruthy();
  expect(screen.getByText(/“Behind a flag, or to main\?”/)).toBeTruthy();
  expect(screen.getByText(/seems stuck · 18m/)).toBeTruthy();
  expect(screen.getByText("docs-site")).toBeTruthy();
  expect(screen.getByText("Today: 4.2M tokens · 1 fix runs automatically")).toBeTruthy();
  expect(b.menuFit).toHaveBeenCalled();
});

test("an answer goes to the agent right here, and the panel refreshes", async () => {
  render(<MenuBar />);
  await flush();
  fireEvent.click(screen.getByText("Behind a flag"));
  await flush();
  expect(b.paneAct).toHaveBeenCalledWith({ team: "checkout-service", role: "planner", fix: "f1", button: "a" });
  expect(b.menuState).toHaveBeenCalledTimes(2);
  expect(b.windowOpen).not.toHaveBeenCalled();
});

test("a fix goes through the same call", async () => {
  render(<MenuBar />);
  await flush();
  fireEvent.click(screen.getByText("Try another way"));
  await flush();
  expect(b.paneAct).toHaveBeenCalledWith({ team: "checkout-service", role: "builder-3", fix: "f2", button: "t" });
});

test("Reply opens the app on that agent", async () => {
  render(<MenuBar />);
  await flush();
  fireEvent.click(screen.getByText("Reply"));
  await flush();
  expect(b.paneAct).not.toHaveBeenCalled();
  expect(b.windowOpen).toHaveBeenCalledWith({ team: "checkout-service", role: "planner", view: "terminals" });
});

test("a question that changed says so", async () => {
  b.paneAct.mockResolvedValue({ ok: false, detail: "This question changed. Take a look." });
  render(<MenuBar />);
  await flush();
  fireEvent.click(screen.getByText("Behind a flag"));
  await flush();
  expect(screen.getByRole("alert").textContent).toBe("This question changed. Take a look.");
});

test("pause, then resume", async () => {
  b.notificationsPause.mockResolvedValue({ ...STATE, paused_until: "15:21" });
  b.notificationsResume.mockResolvedValue(STATE);
  render(<MenuBar />);
  await flush();
  fireEvent.click(screen.getByText("Pause notifications for 1 hour"));
  await flush();
  fireEvent.click(screen.getByText("Paused until 15:21 · Resume"));
  await flush();
  expect(b.notificationsResume).toHaveBeenCalled();
  expect(screen.getByText("Pause notifications for 1 hour")).toBeTruthy();
});

test("an agent it can only watch still offers Open", async () => {
  b.menuState.mockResolvedValue({ ...STATE, teams: [{ name: "t", where: "This Mac", need: 1, items: [{ ...STUCK, buttons: [], fix: null }] }] });
  render(<MenuBar />);
  await flush();
  fireEvent.click(screen.getByText("Open"));
  await flush();
  expect(b.windowOpen).toHaveBeenCalledWith({ team: "t", role: "builder-3", view: "terminals" });
});

test("no teams, and no service, both still offer the way in", async () => {
  b.menuState.mockResolvedValue({ ...STATE, count: 0, teams: [], spend_tokens: 0, auto_fixes: 0 });
  render(<MenuBar />);
  await flush();
  expect(screen.getByText("No teams running.")).toBeTruthy();
  expect(screen.getByText("No tokens used yet today")).toBeTruthy();
  cleanup();
  b.menuState.mockRejectedValue(new Error("gone"));
  render(<MenuBar />);
  await flush();
  expect(screen.getByText(/isn’t answering/)).toBeTruthy();
  expect(screen.getByText("Open Assieme")).toBeTruthy();
});

test("the brain's regular poll keeps the panel fresh while it's hidden", async () => {
  render(<MenuBar />);
  await flush();
  await act(async () => pushed.run({ ...STATE, count: 0, teams: [{ name: "fresh", where: "This Mac", need: 0, items: [] }] }));
  expect(screen.getByText("fresh")).toBeTruthy();
});

test("notifications turned off say so, with the fix", async () => {
  b.notificationsOff.mockResolvedValue(true);
  render(<MenuBar />);
  await flush();
  expect(screen.getByText("Mac notifications are off for Assieme.")).toBeTruthy();
  fireEvent.click(screen.getByText("Turn on"));
  expect(b.notificationSettingsOpen).toHaveBeenCalled();
  b.notificationsOff.mockResolvedValue(false);
});
