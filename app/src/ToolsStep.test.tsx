import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { SetupStatus } from "./bridge";

const b = vi.hoisted(() => ({ installTools: vi.fn() }));
vi.mock("./bridge", async (orig) => ({ ...(await orig<typeof import("./bridge")>()), ...b }));

import { ToolsStep } from "./ToolsStep";

const flush = () => act(async () => {});
const PANEL = { steps: ["tools", "agent", "project", "task", "team", "start"] as ("tools" | "agent" | "project" | "task" | "team" | "start")[], at: "tools" as const, answers: {}, onGo: () => {} };
const st = (git: boolean): SetupStatus => ({ git, agents: {} });
beforeEach(() => { vi.clearAllMocks(); b.installTools.mockResolvedValue({ ok: true }); });
afterEach(cleanup);

function step(s: SetupStatus | null) {
  const onNext = vi.fn();
  const view = render(<ToolsStep panel={PANEL} status={s} onNext={onNext} />);
  return { onNext, rerender: (x: SetupStatus | null) => view.rerender(<ToolsStep panel={PANEL} status={x} onNext={onNext} />) };
}

test("developer tools: install, then Continue while it installs", async () => {
  const h = step(st(false));
  screen.getByRole("heading", { name: "Install Apple’s developer tools" });
  screen.getByText("Welcome to Assieme. These keep versions of your project, so each agent can work on its own copy.");
  screen.getByText("Assieme runs on this Mac and has no server of its own.");
  fireEvent.click(screen.getByRole("button", { name: "Install developer tools" }));
  await flush();
  expect(b.installTools).toHaveBeenCalledTimes(1);
  screen.getByText("Waiting for Apple’s installer");
  screen.getByText("Finish in Apple’s window. It’s a large download, so you can carry on while it installs; this updates by itself.");
  fireEvent.click(screen.getByRole("button", { name: "Open the installer again" }));
  await flush();
  expect(b.installTools).toHaveBeenCalledTimes(2);
  expect(h.onNext).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Continue while it installs" }));
  expect(h.onNext).toHaveBeenCalledTimes(1);
});

test("installed tools say so and offer Continue, never moving on by themselves", async () => {
  const h = step(st(false));
  fireEvent.click(screen.getByRole("button", { name: "Install developer tools" }));
  await flush();
  h.rerender(st(true));
  screen.getByText("Developer tools are installed.");
  expect(h.onNext).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(h.onNext).toHaveBeenCalled();
});

test("an installer that can't open says so", async () => {
  b.installTools.mockRejectedValue(new Error("xcode-select: not found"));
  step(st(false));
  fireEvent.click(screen.getByRole("button", { name: "Install developer tools" }));
  await flush();
  screen.getByText("xcode-select: not found");
});
