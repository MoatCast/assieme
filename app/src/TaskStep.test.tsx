import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import TaskStep from "./TaskStep";

afterEach(cleanup);
const PANEL = { steps: ["project", "task", "team", "start"] as ("project" | "task" | "team" | "start")[], at: "task" as const, answers: {}, onGo: () => {} };

function setup(start = "") {
  const h = { onTask: vi.fn(), onNext: vi.fn(), onSkip: vi.fn(), onBack: vi.fn() };
  function Harness() {
    const [task, setTask] = useState(start);
    return <TaskStep panel={PANEL} task={task} {...h} onTask={(t) => { h.onTask(t); setTask(t); }} />;
  }
  render(<Harness />);
  return h;
}

test("the field is named by the question", () => {
  setup();
  screen.getByRole("heading", { name: "What should the team work on?" });
  screen.getByText("Agents start on this. It becomes the goal in the team brief.");
  const field = screen.getByRole("textbox", { name: "What should the team work on?" });
  expect(field).toHaveAttribute("placeholder", "Add rate limiting to the login endpoint");
});

test("Continue waits for words", () => {
  const h = setup();
  const go = screen.getByRole("button", { name: "Continue" });
  expect(go).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "   " } });
  expect(go).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Fix login" } });
  expect(h.onTask).toHaveBeenLastCalledWith("Fix login");
  fireEvent.click(go);
  expect(h.onNext).toHaveBeenCalledTimes(1);
});

test("Return continues", () => {
  const h = setup("Fix login");
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
  expect(h.onNext).toHaveBeenCalledTimes(1);
  cleanup();
  const e = setup("");
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
  expect(e.onNext).not.toHaveBeenCalled();
});

test("Skip moves on with no task", () => {
  const h = setup("half typed");
  fireEvent.click(screen.getByRole("button", { name: "Skip" }));
  expect(h.onSkip).toHaveBeenCalled();
});

test("Back goes back", () => {
  const h = setup();
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(h.onBack).toHaveBeenCalled();
});
