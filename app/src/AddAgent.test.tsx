import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const b = vi.hoisted(() => ({ addAgent: vi.fn(), teamPresets: vi.fn(), containerStatus: vi.fn(), containerBuild: vi.fn(),
  containerOpen: vi.fn(), terminalState: vi.fn(), openLink: vi.fn(), setupStatus: vi.fn(), fieldOf: (e: unknown) => ({ field: "start", message: String((e as Error).message).replace(/^\w+:\d* ?: ?/, "") }) }));
vi.mock("./bridge", () => b);

import AddAgent, { nextName } from "./AddAgent";

const MODELS = { claude: { models: [{ id: "opus", label: "Opus", efforts: ["high"] }], efforts: ["low"] }, codex: { models: [], efforts: [] } };
const open = async (clis: Record<string, boolean> = { claude: true, codex: true }) => {
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

test("installed agents can be chosen; the others offer Install…", async () => {
  await open({ claude: true, codex: false });
  fireEvent.click(screen.getByRole("button", { name: "Agent: Claude Code" }));
  expect(screen.getByRole("menuitemradio", { name: /Claude Code/ }).getAttribute("aria-checked")).toBe("true");
  expect(screen.queryByRole("menuitemradio", { name: /Codex/ })).toBeNull();
  expect(screen.getByText("Not on this Mac yet")).toBeTruthy();
  for (const name of ["Codex", "Gemini CLI", "OpenCode", "Cursor"])
    expect(screen.getByRole("menuitem", { name: new RegExp(`${name}.*Install…`) })).toBeTruthy();
});

test("a container agent says what it sees, and Add waits for the container", async () => {
  b.containerStatus.mockResolvedValue({ tool: "docker", running: false, image_ready: false });
  await open({ claude: true, codex: true, gemini: true });
  fireEvent.click(screen.getByRole("button", { name: "Agent: Claude Code" }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: /Gemini CLI/ }));
  fireEvent.click(screen.getByRole("radio", { name: /A container/ }));
  await act(async () => {});
  expect(screen.getByText("Docker is on this Mac but not running.")).toBeTruthy();
  expect(screen.getByText(/in its copy of shop, and nothing outside it/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Add agent" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: /Let it use my Gemini sign-in/ }));
  expect(screen.getByText("On: it can read your Gemini sign-in folder, nothing else of yours.")).toBeTruthy();
});

test("Claude Code in a container is never offered a shared sign-in", async () => {
  b.containerStatus.mockResolvedValue({ tool: "docker", running: true, image_ready: true });
  await open();
  fireEvent.click(screen.getByRole("radio", { name: /A container/ }));
  await act(async () => {});
  expect(screen.getByText("Uses Docker, already on this Mac. Takes a little longer to start.")).toBeTruthy();
  expect(screen.queryByRole("checkbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Add agent" }));
  await act(async () => {});
  expect(b.addAgent).toHaveBeenCalledWith("shop", expect.objectContaining({ agent: "claude", container: true, share_signin: false }));
});

test("reports-to lists this team, and Add sends the choices then closes", async () => {
  const close = await open();
  fireEvent.change(screen.getByLabelText("Reports to"), { target: { value: "builder-2" } });
  fireEvent.change(screen.getByLabelText("Model"), { target: { value: "opus" } });
  fireEvent.change(screen.getByLabelText("Thinking"), { target: { value: "high" } });
  fireEvent.click(screen.getByRole("button", { name: "Add agent" }));
  await act(async () => {});
  expect(b.addAgent).toHaveBeenCalledWith("shop", { name: "builder-4", agent: "claude", kind: "builder", parent: "builder-2", model: "opus", effort: "high",
    container: false, share_signin: false });
  expect(close).toHaveBeenCalled();
});

test("switching CLI clears a model only the other one has", async () => {
  await open();
  fireEvent.change(screen.getByLabelText("Model"), { target: { value: "opus" } });
  fireEvent.click(screen.getByRole("button", { name: "Agent: Claude Code" }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: /Codex/ }));
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
