import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const b = vi.hoisted(() => ({ addAgent: vi.fn(), teamPresets: vi.fn(), fieldOf: (e: unknown) => ({ field: "start", message: String((e as Error).message).replace(/^\w+:\d* ?: ?/, "") }) }));
vi.mock("./bridge", () => b);

import AddAgent, { nextName } from "./AddAgent";

const MODELS = { claude: { models: [{ id: "opus", label: "Opus", efforts: ["high"] }], efforts: ["low"] }, codex: { models: [], efforts: [] } };
const open = async (clis = { claude: true, codex: true }) => {
  const close = vi.fn();
  render(<AddAgent team="shop" roles={["planner", "builder-1", "builder-2", "builder-3"]} clis={clis} onClose={close} />);
  await act(async () => {});
  return close;
};

beforeEach(() => {
  vi.resetAllMocks();
  b.teamPresets.mockResolvedValue({ models: MODELS });
  b.addAgent.mockResolvedValue({ team: "shop" });
});
afterEach(cleanup);

test("the name defaults to the next free one, and follows the kind until edited", async () => {
  expect(nextName("builder", ["builder-1"])).toBe("builder-2");
  expect(nextName("reviewer", [])).toBe("reviewer");
  expect(nextName("reviewer", ["reviewer"])).toBe("reviewer-1");
  await open();
  const name = screen.getByLabelText("Name") as HTMLInputElement;
  expect(name.value).toBe("builder-4");
  fireEvent.click(screen.getByRole("button", { name: "Reviewer" }));
  expect(name.value).toBe("reviewer");
  fireEvent.change(name, { target: { value: "mine" } });
  fireEvent.click(screen.getByRole("button", { name: "Tester" }));
  expect(name.value).toBe("mine");
});

test("only installed CLIs are offered", async () => {
  await open({ claude: true, codex: false });
  expect(screen.getByRole("button", { name: "Claude Code" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Codex" })).toBeNull();
});

test("reports-to lists this team, and Add sends the choices then closes", async () => {
  const close = await open();
  fireEvent.change(screen.getByLabelText("Reports to"), { target: { value: "builder-2" } });
  fireEvent.change(screen.getByLabelText("Model"), { target: { value: "opus" } });
  fireEvent.change(screen.getByLabelText("Thinking"), { target: { value: "high" } });
  fireEvent.click(screen.getByRole("button", { name: "Add agent" }));
  await act(async () => {});
  expect(b.addAgent).toHaveBeenCalledWith("shop", { name: "builder-4", agent: "claude", kind: "builder", parent: "builder-2", model: "opus", effort: "high" });
  expect(close).toHaveBeenCalled();
});

test("switching CLI clears a model only the other one has", async () => {
  await open();
  fireEvent.change(screen.getByLabelText("Model"), { target: { value: "opus" } });
  fireEvent.click(screen.getByRole("button", { name: "Codex" }));
  expect((screen.getByLabelText("Model") as HTMLSelectElement).value).toBe("");
});

test("a bad name shows the brain's message and stays open", async () => {
  b.addAgent.mockRejectedValue(new Error("Names use lower-case letters."));
  const close = await open();
  fireEvent.click(screen.getByRole("button", { name: "Add agent" }));
  await act(async () => {});
  expect(screen.getByText("Names use lower-case letters.")).toBeTruthy();
  expect(close).not.toHaveBeenCalled();
});

test("two quick clicks add one agent", async () => {
  await open();
  const add = screen.getByRole("button", { name: "Add agent" });
  fireEvent.click(add);
  fireEvent.click(add);
  await act(async () => {});
  expect(b.addAgent).toHaveBeenCalledTimes(1);
});
