import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { RoleDraft, ToolItem } from "./bridge";

const b = vi.hoisted(() => ({ containerStatus: vi.fn(), containerBuild: vi.fn(), containerOpen: vi.fn() }));
vi.mock("./bridge", async (orig) => ({ ...(await orig<typeof import("./bridge")>()), ...b }));

import { assignMusicians } from "./band";
import EditAgents from "./EditAgents";

const flush = () => act(async () => {});
const r = (name: string, extra: Partial<RoleDraft> = {}): RoleDraft => ({
  name, agent: "claude", job: `${name} job`, checkout: "branch", parent: null, brief: "", ...extra,
});
const LEVELS = ["low", "medium", "high", "xhigh", "max"];
const MODELS = {
  claude: { models: [{ id: "opus", label: "Opus", efforts: LEVELS }], efforts: LEVELS },
  codex: { models: [], efforts: LEVELS },
} as never;
const ITEMS: ToolItem[] = ["server:context7", "server:browser-mcp", "plugin:planban@planban"].map((id) => ({
  id, type: id.split(":")[0] as "server" | "plugin", name: id.split(":")[1], description: "", locked: false, why: "" }));

beforeEach(() => {
  vi.clearAllMocks();
  b.containerStatus.mockResolvedValue({ tool: "docker", running: true, image_ready: false });
});
afterEach(cleanup);

function sheet(p: Partial<Parameters<typeof EditAgents>[0]> = {}) {
  const h = { onEdit: vi.fn(), onAdd: vi.fn(), onRemove: vi.fn(), onBrief: vi.fn(), onTools: vi.fn(), onInstall: vi.fn(),
    onNotReady: vi.fn(), onDone: vi.fn() };
  const props = { project: "/Users/me/checkout-service", roles: [r("builder"), r("reviewer", { checkout: "read-only" as const })],
    installed: { claude: true, codex: true }, models: MODELS, tools: {}, listKey: (x: RoleDraft) => `${x.agent}|true`,
    roleErrors: {}, why: "", picked: "Builder and reviewer", imported: null, ...h, ...p };
  const view = render(<EditAgents {...props} />);
  return { ...h, rerender: (q: Partial<typeof props>) => view.rerender(<EditAgents {...props} {...q} />) };
}

test("the sheet names the project and says why these agents", () => {
  sheet();
  screen.getByRole("dialog", { name: "Agents for checkout-service" });
  screen.getByText("Assieme picked these for a builder and reviewer. Change an agent’s kind here, or open it for its instructions, permissions and tools.");
  cleanup();
  sheet({ why: "A small change needs one agent." });
  screen.getByText("A small change needs one agent.");
  expect(screen.queryByText(/Mix any agents/)).toBeNull();
});

test("only installed kinds are offered", () => {
  sheet({ installed: { claude: true, codex: false } });
  fireEvent.click(screen.getAllByRole("button", { name: "Agent: Claude Code" })[0]);
  expect(screen.queryByRole("menuitemradio", { name: /Codex/ })).toBeNull();
  screen.getByRole("menuitem", { name: /Codex.*Install…/ });
});

test("an agent can run sealed off: the box shows with the legend, and its container line reports", async () => {
  const h = sheet();
  expect(screen.queryByText("The box icon means it runs sealed off.")).toBeNull();
  fireEvent.click(screen.getAllByRole("button", { name: "Agent: Claude Code" })[0]);
  const item = screen.getByRole("menuitemcheckbox", { name: /Run sealed off/ });
  expect(item).toHaveTextContent("Sees only its copy of checkout-service. Uses Docker.");
  fireEvent.click(item);
  expect(h.onEdit).toHaveBeenCalledWith(0, { container: true });
  h.rerender({ roles: [r("builder", { container: true }), r("reviewer")] });
  await flush();
  screen.getByRole("button", { name: "Agent: Claude Code, in a container" });
  screen.getByText("The box icon means it runs sealed off.");
  screen.getByRole("button", { name: "What does sealed off mean?" });
  screen.getByText("Uses Docker. The first time, it downloads and builds the container, about 1.5 GB.");
  expect(h.onNotReady).toHaveBeenCalledWith("claude", true);
});

test("add, remove, rename and Open", () => {
  const h = sheet();
  fireEvent.click(screen.getByText("Add an agent"));
  expect(h.onAdd).toHaveBeenCalled();
  fireEvent.click(screen.getAllByLabelText("Remove agent")[1]);
  expect(h.onRemove).toHaveBeenCalledWith(1);
  fireEvent.change(screen.getAllByLabelText("Agent name")[0], { target: { value: "lead" } });
  expect(h.onEdit).toHaveBeenCalledWith(0, { name: "lead" });
  fireEvent.click(screen.getAllByRole("button", { name: "Open ›" })[0]);
  expect(h.onBrief).toHaveBeenCalledWith(0);
  expect(screen.queryByText("Brief")).toBeNull();
});

test("a model and effort show on the row, and the menu changes them", () => {
  const h = sheet({ roles: [r("builder"), r("reviewer", { model: "opus", effort: "high" })] });
  expect(screen.getByRole("button", { name: "Model and effort for builder" }).textContent).toBe("Default");
  expect(screen.getByRole("button", { name: "Model and effort for reviewer" }).textContent).toBe("Opus · High");
  fireEvent.click(screen.getByRole("button", { name: "Model and effort for builder" }));
  fireEvent.change(screen.getByLabelText("Model"), { target: { value: "opus" } });
  expect(h.onEdit).toHaveBeenCalledWith(0, expect.objectContaining({ model: "opus" }));
});

test("each agent shows its tools and opens its picker", () => {
  const h = sheet({ tools: { "claude|true": ITEMS }, roles: [r("builder", { tools_off: [] }), r("reviewer", { tools_off: ["server:browser-mcp"] })] });
  screen.getByRole("button", { name: "Tools for builder: 3 of 3" });
  fireEvent.click(screen.getByRole("button", { name: "Tools for reviewer: 2 of 3" }));
  expect(h.onTools).toHaveBeenCalledWith(1);
});

test("faces: each agent row shows the musician it will be saved with", () => {
  const roles = [r("builder"), r("reviewer", { checkout: "read-only", parent: "builder" })];
  sheet({ roles });
  const seats = assignMusicians(roles);
  screen.getByRole("img", { name: `builder, ${seats[0].musician}, idle` });
  screen.getByRole("img", { name: `reviewer, ${seats[1].musician}, idle` });
});

test("a name error shows on its row", () => {
  sheet({ roleErrors: { 1: "Names use lower-case letters, digits and dashes, and must differ." } });
  screen.getByText("Names use lower-case letters, digits and dashes, and must differ.");
});

test("an import's notes stay under the rows", () => {
  sheet({ imported: { notes: [{ text: "Its layout didn't carry over.", agents: ["builder"] }], from: "herdr" } });
  screen.getByText("Imported from herdr. What didn’t carry over");
  screen.getByText("Its layout didn't carry over.");
});

test("Done, Cancel and Escape all close; edits already applied", () => {
  const h = sheet();
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(h.onDone).toHaveBeenCalledTimes(3);
});
