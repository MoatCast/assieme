import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { SetupStatus } from "./bridge";

const b = vi.hoisted(() => ({
  setupCommands: vi.fn(), setupStatus: vi.fn(), installAgent: vi.fn(), signinAgent: vi.fn(), terminalState: vi.fn(),
  installStop: vi.fn(), openLink: vi.fn(), connectAntigravity: vi.fn(),
}));
vi.mock("./bridge", async (orig) => ({ ...(await orig<typeof import("./bridge")>()), ...b }));
vi.mock("./Terminal", () => ({ default: ({ address }: { address: string }) => <div data-testid="terminal">{address}</div> }));

import { AgentStep } from "./AgentStep";

const flush = () => act(async () => {});
const PANEL = { steps: ["agent", "project", "task", "team", "start"] as ("agent" | "project" | "task" | "team" | "start")[], at: "agent" as const, answers: {}, onGo: () => {} };
const COMMANDS = { claude: { name: "Claude Code", install: "curl -fsSL https://claude.ai/install.sh | bash", signin: "s", status: "s" } };
const none: SetupStatus = { git: true, agents: {} };

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ toFake: ["Date"] });
  b.setupCommands.mockResolvedValue(COMMANDS);
  b.installAgent.mockResolvedValue({ address: "t7" });
  b.signinAgent.mockResolvedValue({ address: "t8" });
  b.terminalState.mockResolvedValue("running");
  b.installStop.mockResolvedValue({ ok: true });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });
const later = () => vi.setSystemTime(new Date(Date.now() + 400));

function step(s: SetupStatus | null = none) {
  const h = { onNext: vi.fn(), onBack: vi.fn() };
  const view = render(<AgentStep panel={PANEL} status={s} {...h} />);
  later();
  return { ...h, rerender: (x: SetupStatus | null) => view.rerender(<AgentStep panel={PANEL} status={x} {...h} />) };
}

test("each agent kind is a button with its plan", () => {
  step();
  screen.getByRole("heading", { name: "Which agent do you pay for?" });
  screen.getByText("Assieme runs it for you. Choosing one shows how it installs; it installs only when you say so.");
  const kinds = screen.getAllByRole("button").filter((x) => x.classList.contains("option")).map((x) => x.textContent);
  expect(kinds).toEqual([
    "Claude CodeAnthropic · paid Claude plan", "CodexOpenAI · ChatGPT, limits on the free plan",
    "AntigravityGoogle · works on the free plan", "Gemini CLIGoogle · paid key only",
    "OpenCodeOpen source · your own model keys", "CursorAnysphere · Cursor plan",
  ]);
  expect(screen.queryByRole("radio")).toBeNull();
});

test("choosing a kind shows its install inside the panel and puts Install {name} in the footer", async () => {
  step();
  fireEvent.click(screen.getByRole("button", { name: /Claude Code/ }));
  await flush();
  screen.getByText(/This downloads Anthropic’s installer from claude\.ai/);
  screen.getByText("curl -fsSL https://claude.ai/install.sh | bash");
  expect(b.installAgent).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Install Claude Code" }));
  await flush();
  expect(b.installAgent).toHaveBeenCalledWith("claude");
  screen.getByTestId("terminal");
});

test("a signed-in agent moves on", async () => {
  const h = step();
  fireEvent.click(screen.getByRole("button", { name: /Claude Code/ }));
  await flush();
  expect(h.onNext).not.toHaveBeenCalled();
  h.rerender({ git: true, agents: { claude: { installed: true, signed_in: true } } });
  await flush();
  expect(h.onNext).toHaveBeenCalledTimes(1);
});

test("a signed-out agent starts at sign-in", async () => {
  step({ git: true, agents: { claude: { installed: true, signed_in: false } } });
  fireEvent.click(screen.getByRole("button", { name: /Claude Code/ }));
  await flush();
  expect(b.signinAgent).toHaveBeenCalledWith("claude");
  screen.getByText("Sign in to Claude Code");
});

test("Skip for now moves on", async () => {
  const h = step({ git: true, agents: { claude: { installed: true, signed_in: false } } });
  fireEvent.click(screen.getByRole("button", { name: /Claude Code/ }));
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
  await flush();
  expect(h.onNext).toHaveBeenCalled();
});

test("Back goes back", () => {
  const h = step();
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(h.onBack).toHaveBeenCalled();
});
