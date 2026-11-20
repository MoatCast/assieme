import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import StepPanel, { useClickGuard } from "./StepPanel";
import type { StepId } from "./newTeamSteps";

afterEach(() => { cleanup(); vi.useRealTimers(); });

const steps: StepId[] = ["project", "task", "team", "start"];
const panel = (at: StepId, onGo = vi.fn(), announce?: string) => (
  <StepPanel steps={steps} at={at} answers={{ project: "checkout-service" }} title={`Title ${at}`} hint="Hint"
    announce={announce} onGo={onGo} footer={<button type="button">Continue</button>}>
    <p>Body</p>
  </StepPanel>
);

test("the indicator is a named list with the current step marked", () => {
  const onGo = vi.fn();
  render(panel("task", onGo));
  const nav = screen.getByRole("navigation", { name: "Setup steps" });
  expect(nav.querySelector("[aria-current='step']")).toHaveTextContent("Task");
  fireEvent.click(screen.getByRole("button", { name: "✓ checkout-service" }));
  expect(onGo).toHaveBeenCalledWith("project");
  expect(screen.queryByRole("button", { name: "Team" })).toBeNull(); // later steps aren't buttons
});

test("the heading takes focus when the step changes", () => {
  const { rerender } = render(panel("project"));
  rerender(panel("task"));
  expect(screen.getByRole("heading", { level: 1 })).toHaveFocus();
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Title task");
});

test("the live region says what changed", () => {
  render(panel("task", vi.fn(), "checkout-service checked"));
  expect(screen.getByText("checkout-service checked").closest("[aria-live='polite']")).not.toBeNull();
});

test("a click right after a step change is ignored", () => {
  vi.useFakeTimers();
  const { result, rerender } = renderHook(({ at }) => useClickGuard(at), { initialProps: { at: "project" as StepId } });
  act(() => { vi.advanceTimersByTime(300); });
  expect(result.current()).toBe(false);
  rerender({ at: "task" });
  expect(result.current()).toBe(true);
  act(() => { vi.advanceTimersByTime(300); });
  expect(result.current()).toBe(false);
});

test("every button in a panel ignores clicks for 300 ms after the step changes", () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  const pressed = vi.fn();
  const view = (at: StepId) => (
    <StepPanel steps={steps} at={at} answers={{}} title="T" onGo={() => {}} footer={<button type="button" onClick={pressed}>Continue</button>}>
      <button type="button" onClick={pressed}>Option</button>
    </StepPanel>
  );
  const { rerender } = render(view("project"));
  vi.setSystemTime(new Date(Date.now() + 400));
  rerender(view("task"));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Option" }));
  expect(pressed).not.toHaveBeenCalled();
  vi.setSystemTime(new Date(Date.now() + 400));
  fireEvent.click(screen.getByRole("button", { name: "Option" }));
  expect(pressed).toHaveBeenCalledTimes(1);
});

test("the indicator uses no class that another screen styles on its own", async () => {
  const { readFileSync, readdirSync } = await import("node:fs");
  const bare = new Set<string>();
  for (const f of readdirSync("src").filter((x) => x.endsWith(".css")))
    for (const m of readFileSync(`src/${f}`, "utf8").matchAll(/^\.([\w-]+)\s*[{,]/gm)) bare.add(m[1]);
  render(panel("task"));
  const classes = [...screen.getByRole("navigation", { name: "Setup steps" }).querySelectorAll("li")].flatMap((li) => [...li.classList]);
  expect(classes.filter((c) => bare.has(c))).toEqual([]);
});
