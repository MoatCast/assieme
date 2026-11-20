import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { AgentDetail, Pane } from "./bridge";
import { Q, fixRow, pane, waitingFor } from "./windowFixtures";

const b = vi.hoisted(() => ({ agentDetail: vi.fn(), paneAct: vi.fn(), agentRestart: vi.fn(), agentRemove: vi.fn(), openLink: vi.fn(), activityUndo: vi.fn() }));
vi.mock("./bridge", () => b);
vi.mock("./Terminal", () => ({ default: () => <div data-testid="xterm" /> }));
vi.mock("./AgentDetail", async (orig) => ({ ...(await orig<object>()),
  default: ({ only, role }: { only?: string; role: string }) => <div data-testid="agent-settings">{role} {only}</div> }));
const preview = vi.hoisted(() => ({ current: null as null | { state: string; url: string } }));
const pv = vi.hoisted(() => ({ start: vi.fn(), stop: vi.fn() }));
vi.mock("./Preview", () => ({
  usePreviews: () => ({ of: () => preview.current, start: pv.start, stop: pv.stop, busy: false, sheet: null }),
  host: (p: { url: string }) => p.url.replace(/^https?:\/\//, ""),
}));

import AgentPanel from "./AgentPanel";

const DETAIL = (over: Partial<AgentDetail> = {}): AgentDetail => ({
  role: "builder-3", cli: "claude", job: "writes the backoff test", state: "working", since_s: 60, detail: "", fix: null, reachable: true,
  command: null, memory: { pct: 40, amber: false }, recent: [{ at: "14:11", text: "Edited test_retry.py" }], wakers: [{ role: "planner", count: 2 }],
  empty_wakes: 0, ...over,
});

const flush = () => act(async () => {});
const show = async (p: Pane, over: Partial<Parameters<typeof AgentPanel>[0]> = {}) => {
  const props = { team: "shop", pane: p, fixes: [], waiters: [], failed: null, review: null, onReview: vi.fn(), onChanged: vi.fn(), onFullSize: vi.fn(), ...over };
  const r = render(<AgentPanel {...props} />);
  await flush();
  return { ...r, props };
};

beforeEach(() => {
  vi.resetAllMocks();
  b.agentDetail.mockResolvedValue(DETAIL());
  b.paneAct.mockResolvedValue({ ok: true, detail: "" });
  b.agentRestart.mockResolvedValue({ ok: true, detail: "" });
  b.agentRemove.mockResolvedValue({ ok: true, detail: "" });
  preview.current = null;
});
afterEach(cleanup);

test("the header names the agent, its kind and its job", async () => {
  await show(pane("builder-3"));
  expect(screen.getByRole("heading", { name: "builder-3" })).toBeTruthy();
  expect(screen.getByText("Claude Code · writes the backoff test")).toBeTruthy();
});

test("a question: who waits, the question, outlined answers and a ghost reply", async () => {
  await show(pane("planner", { state: "asking", since_s: 720, fix: Q("Ship the retry change behind a flag, or straight to main?") }),
    { waiters: ["builder-1", "reviewer"] });
  const card = screen.getByTestId("card");
  expect(within(card).getByText("Asking you · 12 min · holding up builder-1 and reviewer")).toBeTruthy();
  expect(within(card).getByText("“Ship the retry change behind a flag, or straight to main?”")).toBeTruthy();
  expect(within(card).getByRole("button", { name: "Behind a flag" }).className).toBe("btn-outline");
  expect(within(card).getByRole("button", { name: "To main" }).className).toBe("btn-outline");
  expect(within(card).getByRole("button", { name: "✎ Write a reply" }).className).toBe("btn-ghost");
  expect(card.querySelector(".btn-primary")).toBeNull(); // none reads as Assieme's recommendation
});

test("Write a reply puts the cursor in this panel's terminal", async () => {
  await show(pane("planner", { state: "asking", fix: Q("Go?") }));
  fireEvent.click(screen.getByRole("button", { name: "✎ Write a reply" }));
  expect(document.activeElement?.hasAttribute("data-terminal")).toBe(true);
});

test("after an answer the card says what was sent, until the agent moves on", async () => {
  const { rerender, props } = await show(pane("planner", { state: "asking", fix: Q("Go?") }));
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Behind a flag" })); });
  expect(b.paneAct).toHaveBeenCalledWith({ team: "shop", role: "planner", fix: "q-Go?", button: "b1" });
  expect(screen.getByText(/^Sent ‘Behind a flag’ to planner at \d\d:\d\d$/)).toBeTruthy();
  expect(screen.queryByTestId("card")).toBeNull();
  rerender(<AgentPanel {...props} pane={pane("planner", { state: "working" })} />);
  expect(screen.queryByText(/^Sent/)).toBeNull();
});

test("paused after a fix: the measured failure, Give it a hint and Resume as is", async () => {
  const fix = { id: "fixpause:shop/builder-3", text: "No progress for 10 min after Assieme asked it to try another way.",
    detail: "Before the fix: test_retry_backoff still fails. 38K tokens used on it.", buttons: [
      { id: "hint", label: "Give it a hint", kind: "reply" as const }, { id: "resume", label: "Resume as is", kind: "action" as const }],
    pause: { at: "14:32", of: "f1" } };
  await show(pane("builder-3", { state: "idle", fix, paused_by_fix: true }));
  const card = screen.getByTestId("card");
  expect(within(card).getByText("Paused by Assieme at 14:32 · Still stuck")).toBeTruthy();
  expect(within(card).getByText("No progress for 10 min after Assieme asked it to try another way.")).toBeTruthy();
  expect(within(card).getByText(/^Before the fix: test_retry_backoff still fails\. 38K tokens used on it\. Assieme paused it so it stops using tokens; its work is kept\./)).toBeTruthy();
  expect(within(card).getByRole("button", { name: "✎ Give it a hint" }).className).toBe("btn-primary");
  expect(within(card).getByRole("button", { name: "Resume as is" }).className).toBe("btn-outline");
  fireEvent.click(within(card).getByRole("button", { name: "✎ Give it a hint" }));
  expect(document.activeElement?.hasAttribute("data-terminal")).toBe(true);
});

test("a failed start: the reason, then Try again and Remove agent", async () => {
  const { props } = await show(pane("builder-4", { state: "exited" }), { failed: { reason: "Codex isn't signed in on this Mac." } });
  const card = screen.getByTestId("card");
  expect(within(card).getByText("Couldn't start")).toBeTruthy();
  expect(within(card).getByText("Codex isn't signed in on this Mac.")).toBeTruthy();
  expect(within(card).getByText("Sign in from builder-4's terminal below, then try again. The rest of the team keeps working.")).toBeTruthy();
  await act(async () => { fireEvent.click(within(card).getByRole("button", { name: "Try again" })); });
  expect(b.agentRestart).toHaveBeenCalledWith("shop", "builder-4");
  fireEvent.click(within(card).getByRole("button", { name: "Remove agent" }));
  expect(b.agentRemove).not.toHaveBeenCalled(); // it asks first
  expect(within(card).getByText("Remove builder-4 from the team? Its branch and files stay.")).toBeTruthy();
  fireEvent.click(within(card).getByRole("button", { name: "Keep it" }));
  fireEvent.click(within(card).getByRole("button", { name: "Remove agent" }));
  await act(async () => { fireEvent.click(within(card).getByRole("button", { name: "Remove" })); });
  expect(b.agentRemove).toHaveBeenCalledWith("shop", "builder-4");
  expect(props.onChanged).toHaveBeenCalled();
});

test("while Assieme fixes it: a blue card with the steps and no primary button", async () => {
  await show(pane("builder-3", { state: "stuck" }), { fixes: [fixRow("builder-3", "checking", "14:22", { sent: "Try another way: run one test at a time." })] });
  const card = screen.getByTestId("card");
  expect(card.className).toContain("assieme");
  expect(within(card).getByText("Assieme is on it · since 14:22")).toBeTruthy();
  expect(within(card).getByText("Stuck 9 min: the same test failed 4 times (38K tokens).")).toBeTruthy();
  for (const s of ["Noticed 14:13", "Asked it to try another way 14:22", "◌ Checking until 14:32", "Fixed, or paused for you"])
    expect(within(card).getByText(s)).toBeTruthy();
  expect(card.querySelector(".btn-primary")).toBeNull();
  fireEvent.click(within(card).getByRole("button", { name: "Show the message Assieme sent ›" }));
  expect(within(card).getByText("Try another way: run one test at a time.")).toBeTruthy();
  expect(within(card).getByRole("button", { name: "✎ Write a reply" })).toBeTruthy();
});

test("finished with work to review: Review changes and the preview controls", async () => {
  preview.current = { state: "running", url: "http://localhost:5173" };
  const { props } = await show(pane("tester", { state: "waiting", since_s: 540 }), { review: waitingFor("tester", 3) });
  const card = screen.getByTestId("card");
  expect(within(card).getByText("Finished 9 min ago · preview running at localhost:5173")).toBeTruthy();
  expect(within(card).getByText("3 commits are ready to review.")).toBeTruthy();
  fireEvent.click(within(card).getByRole("button", { name: "Review changes" }));
  expect(props.onReview).toHaveBeenCalledWith("shop", "tester");
  fireEvent.click(within(card).getByRole("button", { name: "■ Stop preview" }));
  expect(pv.stop).toHaveBeenCalledWith("shop", "tester");
  expect(within(card).getByRole("button", { name: "■ Stop preview" }).className).toBe("btn-ghost");
  fireEvent.click(within(card).getByRole("button", { name: "Open preview" }));
  expect(b.openLink).toHaveBeenCalledWith("http://localhost:5173");
});

test("finished with no preview yet offers one", async () => {
  await show(pane("tester", { state: "waiting", since_s: 540 }), { review: waitingFor("tester", 1) });
  expect(screen.getByText("Finished 9 min ago")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  expect(pv.start).toHaveBeenCalledWith("shop", "tester");
});

test("what the agent did never lists Assieme's actions; Assieme's are under their own title", async () => {
  b.agentDetail.mockResolvedValue(DETAIL({ recent: [{ at: "14:11", text: "Edited test_retry.py" }] }));
  await show(pane("builder-3"), { fixes: [fixRow("builder-3", "fixed", "13:41", { title: "Asked builder-3 to try another way" })] });
  const did = screen.getByTestId("agent-did");
  expect(within(did).getByText("What builder-3 did")).toBeTruthy();
  expect(within(did).queryByText(/Assieme|try another way/)).toBeNull();
  const assieme = screen.getByTestId("assieme-did");
  expect(within(assieme).getByText("What Assieme did")).toBeTruthy();
  expect(within(assieme).getByText("✓ Fixed")).toBeTruthy();
});

test("memory and who wakes it sit under the history", async () => {
  await show(pane("builder-3"));
  expect(screen.getByText("40% full")).toBeTruthy();
  expect(screen.getByText(/planner/)).toBeTruthy();
});

test("••• opens the agent's settings only", async () => {
  await show(pane("builder-3"));
  fireEvent.click(screen.getByRole("button", { name: "More for builder-3" }));
  expect(screen.getByTestId("agent-settings").textContent).toBe("builder-3 settings");
});

test("Open full size hides the list", async () => {
  const { props } = await show(pane("builder-3"));
  fireEvent.click(screen.getByRole("button", { name: "Open full size ↗" }));
  expect(props.onFullSize).toHaveBeenCalledWith(true);
});
