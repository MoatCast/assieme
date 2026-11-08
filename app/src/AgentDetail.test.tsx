import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { AgentDetail as Detail } from "./bridge";

const b = vi.hoisted(() => ({ agentDetail: vi.fn(), paneAct: vi.fn(), teamMap: vi.fn(), tipApply: vi.fn(), activityUndo: vi.fn() }));
vi.mock("./bridge", () => b);
vi.mock("./Terminal", () => ({ default: () => <div /> }));

import AgentDetail from "./AgentDetail";

const D = (over: Partial<Detail> = {}): Detail => ({
  role: "builder-3", cli: "codex", job: "tests the backoff", state: "working", since_s: 40, detail: "", fix: null, reachable: true, command: null,
  memory: null, recent: [], wakers: [], empty_wakes: 0, ...over,
});
const open = async (d: Detail) => {
  b.agentDetail.mockResolvedValue(d);
  const calls = { close: vi.fn(), term: vi.fn(), changed: vi.fn() };
  render(<AgentDetail team="shop" role="builder-3" pollMs={100000} onClose={calls.close} onOpenTerminal={calls.term} onChanged={calls.changed} />);
  await act(async () => {});
  return calls;
};

beforeEach(() => { vi.resetAllMocks(); });
afterEach(cleanup);

test("the meter is amber above 75%, with its note", async () => {
  await open(D({ memory: { pct: 88, amber: true } }));
  expect(screen.getByText("88% full").className).toBe("amber");
  expect(screen.getByText(/free up memory/)).toBeTruthy();
});

test("below 75% it has no note, and no data means no meter", async () => {
  await open(D({ memory: { pct: 40, amber: false } }));
  expect(screen.getByText("40% full").className).toBe("");
  expect(screen.queryByText(/free up memory/)).toBeNull();
  cleanup();
  await open(D());
  expect(screen.queryByText("Memory")).toBeNull();
});

test("what it's been doing, with the program in the mono face", async () => {
  await open(D({ recent: [{ at: "14:20", text: "Ran pytest, failed" }, { at: "14:09", text: "Edited retry.py" }] }));
  expect(screen.getByText("14:20")).toBeTruthy();
  expect(screen.getByText("pytest").tagName).toBe("CODE");
  expect(screen.getByText(", failed", { exact: false }).textContent).toBe("Ran pytest, failed");
  expect(screen.getByText("retry.py")).toBeTruthy();
});

test("who wakes it", async () => {
  await open(D({ wakers: [{ role: "planner", count: 5 }, { role: "tester", count: 1 }] }));
  expect(screen.getByText("planner").parentElement!.textContent).toBe("planner 5 times");
  expect(screen.getByText("tester").parentElement!.textContent).toBe("tester once");
  expect(screen.getByText(/None of today’s were empty/)).toBeTruthy();
  cleanup();
  await open(D({ wakers: [{ role: "planner", count: 2 }], empty_wakes: 3 }));
  expect(screen.getByText(/3 of today’s did nothing/)).toBeTruthy();
  cleanup();
  await open(D());
  expect(screen.getByText("Nothing woke it in the last hour.")).toBeTruthy();
});

test("closes on X and on Escape", async () => {
  const calls = await open(D());
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(calls.close).toHaveBeenCalledTimes(2);
});

test("a fix button acts, and Open terminal works", async () => {
  b.paneAct.mockResolvedValue({ ok: true, detail: "" });
  const fix = { id: "f", text: "stuck", buttons: [{ id: "n", label: "Ask it to try another way", kind: "action" as const }] };
  const calls = await open(D({ state: "stuck", since_s: 1080, detail: "Ran the same test 4 times", fix }));
  expect(screen.getByText("Seems stuck for 18 minutes")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Ask it to try another way" }));
  await act(async () => {});
  expect(b.paneAct).toHaveBeenCalledWith({ team: "shop", role: "builder-3", fix: "f", button: "n" });
  expect(calls.changed).toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Open terminal" }));
  expect(calls.term).toHaveBeenCalledWith("builder-3");
});
