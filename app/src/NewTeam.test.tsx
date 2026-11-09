import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Preset, RoleDraft } from "./bridge";

const b = vi.hoisted(() => ({
  teamPresets: vi.fn(),
  checkProject: vi.fn(),
  pickFolder: vi.fn(),
  suggestTeam: vi.fn(),
  startTeam: vi.fn(),
}));
vi.mock("./bridge", async (orig) => ({ ...(await orig<typeof import("./bridge")>()), ...b }));

import NewTeam from "./NewTeam";

const r = (name: string, extra: Partial<RoleDraft> = {}): RoleDraft => ({
  name, agent: "claude", job: `${name} job`, checkout: "branch", parent: null, brief: `You are ${name}.`, ...extra,
});
const PRESETS: Preset[] = [
  { id: "one-agent", label: "One agent", roles: [r("builder")] },
  { id: "builder-reviewer", label: "Builder + reviewer", roles: [r("builder"), r("reviewer", { checkout: "read-only", parent: "builder" })] },
  { id: "full", label: "Planner, 3 builders, reviewer, tester", roles: [r("planner"), r("builder-1")] },
];
export const PRACTICES = {
  groups: ["messages", "safety", "reviews", "stopping", "machine"].map((id) => ({
    id, title: { messages: "Fewer, cheaper messages", safety: "Safe by default", reviews: "Careful reviews",
      stopping: "Know when to stop", machine: "Easy on your Mac" }[id]!, summary: `${id} summary`, rules: ["a", "b"],
  })),
  basics: [1, 2, 3, 4, 5].map((i) => ({ title: `Basic ${i}`, detail: "d" })),
};

const LEVELS = ["low", "medium", "high", "xhigh", "max"];
const MODELS = {
  claude: { models: [{ id: "fable", label: "Fable", efforts: LEVELS }, { id: "opus", label: "Opus", efforts: LEVELS }], efforts: LEVELS },
  codex: {
    models: [{ id: "gpt-6.1-sol", label: "gpt-6.1-sol", efforts: [...LEVELS, "ultra"] }, { id: "gpt-6-luna", label: "gpt-6-luna", efforts: LEVELS }],
    efforts: LEVELS,
  },
};

const flush = () => act(async () => {});
const both = { claude: true, codex: true };

beforeEach(() => {
  vi.clearAllMocks();
  b.teamPresets.mockResolvedValue({ presets: PRESETS, practices: PRACTICES, last: null, models: MODELS });
  b.checkProject.mockResolvedValue({ ok: true });
  b.pickFolder.mockResolvedValue("/Users/me/shop");
});
afterEach(cleanup);

const names = () => screen.getAllByLabelText("Agent name").map((e) => (e as HTMLInputElement).value);

async function open(clis = both) {
  const onClose = vi.fn();
  render(<NewTeam clis={clis} onClose={onClose} />);
  await flush();
  return onClose;
}

async function fillProjectAndTask() {
  fireEvent.click(screen.getByText("Choose…"));
  await flush();
  fireEvent.change(screen.getByLabelText("What should the team work on?"), { target: { value: "Fix login" } });
}

test("choosing a starting team fills the agents", async () => {
  await open();
  expect(names()).toEqual(["builder"]);
  fireEvent.click(screen.getByText("Builder + reviewer"));
  expect(names()).toEqual(["builder", "reviewer"]);
});

test("only installed CLIs are offered", async () => {
  await open({ claude: true, codex: false });
  expect(screen.queryByRole("button", { name: "Codex" })).toBeNull();
  expect(screen.getAllByRole("button", { name: "Claude Code" }).length).toBe(1);
  cleanup();
  await open({ claude: false, codex: false });
  expect(screen.getByText("Install Claude Code or Codex, then reopen New team.")).toBeTruthy();
});

test("says what's missing until Next can be pressed, then what agents are told", async () => {
  await open();
  expect(screen.getByText("Choose a project folder to continue.")).toBeTruthy();
  fireEvent.click(screen.getByText("Choose…"));
  await flush();
  expect(screen.getByText("Say what the team should work on to continue.")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("What should the team work on?"), { target: { value: "Fix login" } });
  expect(screen.getByText(/told not to push or deploy/)).toBeTruthy();
  expect(screen.queryByText(/can’t push/)).toBeNull();
});

test("a stopped service isn't reported as missing agents, and New team recovers", async () => {
  b.teamPresets.mockRejectedValue(new Error("no brain"));
  render(<NewTeam clis={null} retryMs={1} onClose={vi.fn()} />);
  await flush();
  expect(screen.getByText(/service isn’t running/)).toBeTruthy();
  expect(screen.queryByText(/Install Claude Code or Codex/)).toBeNull();
  b.teamPresets.mockResolvedValue({ presets: PRESETS, practices: PRACTICES, last: null, models: MODELS });
  expect(await screen.findByText("One agent")).toBeTruthy();
  expect(screen.queryByText(/service isn’t running/)).toBeNull();
});

test("while the agent check hasn't answered, it doesn't say to install one", async () => {
  render(<NewTeam clis={null} onClose={vi.fn()} />);
  await flush();
  expect(screen.queryByText(/Install Claude Code or Codex/)).toBeNull();
  expect(screen.getByText("Checking which agents are installed…")).toBeTruthy();
});

test("add, rename, remove and edit a brief", async () => {
  await open();
  await fillProjectAndTask();
  fireEvent.click(screen.getByText("Add an agent"));
  expect(names()).toEqual(["builder", "builder-2"]);
  fireEvent.click(screen.getAllByLabelText("Remove agent")[1]);
  expect(names()).toEqual(["builder"]);
  fireEvent.click(screen.getByText("Brief"));
  fireEvent.change(screen.getByLabelText("Brief for builder"), { target: { value: "New brief." } });
  fireEvent.click(screen.getByText("Done"));
  fireEvent.click(screen.getByText("Brief"));
  expect((screen.getByLabelText("Brief for builder") as HTMLTextAreaElement).value).toBe("New brief.");
  fireEvent.click(screen.getByText("Done"));
  fireEvent.change(screen.getAllByLabelText("Agent name")[0], { target: { value: "Bad Name" } });
  fireEvent.click(screen.getByText("Next"));
  expect(screen.getByText("Names use lower-case letters, digits and dashes, and must differ.")).toBeTruthy();
  expect(screen.queryByText("Recommended setup")).toBeNull();
});

test("project problem blocks Next; task is required", async () => {
  b.checkProject.mockResolvedValue({ ok: false, reason: "This folder isn't a git repository. Assieme gives each agent its own branch, so it needs one." });
  await open();
  const next = screen.getByText("Next") as HTMLButtonElement;
  expect(next.disabled).toBe(true);
  await fillProjectAndTask();
  expect(b.checkProject).toHaveBeenCalledWith("/Users/me/shop");
  expect(screen.getByText(/isn't a git repository/)).toBeTruthy();
  expect(next.disabled).toBe(true);
  b.checkProject.mockResolvedValue({ ok: true });
  fireEvent.click(screen.getByText("Choose…"));
  await flush();
  expect(next.disabled).toBe(false);
  fireEvent.change(screen.getByLabelText("What should the team work on?"), { target: { value: "  " } });
  expect(next.disabled).toBe(true);
});

test("remote machine is coming later", async () => {
  await open();
  const remote = screen.getByRole("radio", { name: /A remote machine/ }) as HTMLInputElement;
  expect(remote.disabled).toBe(true);
  expect(screen.getByText("Coming later")).toBeTruthy();
});

test("help me choose fills the agents", async () => {
  b.suggestTeam.mockResolvedValue({ roles: [r("solo", { job: "Does it all" })], why: "A small change needs one agent." });
  await open();
  await fillProjectAndTask();
  fireEvent.click(screen.getByText("Help me choose"));
  fireEvent.click(screen.getByText("Suggest a team"));
  expect(screen.getByText("Asking Claude Code…")).toBeTruthy();
  await flush();
  expect(b.suggestTeam).toHaveBeenCalledWith({ project: "/Users/me/shop", size: "feature", review: "every", budget: "lean" });
  expect(names()).toEqual(["solo"]);
  expect(screen.getByText("A small change needs one agent.")).toBeTruthy();

  b.suggestTeam.mockRejectedValue("suggest: Couldn't get a suggestion: Please run /login");
  fireEvent.click(screen.getByText("Help me choose"));
  fireEvent.click(screen.getByText("Suggest a team"));
  await flush();
  expect(screen.getByText("Couldn't get a suggestion: Please run /login")).toBeTruthy();
  fireEvent.click(screen.getByText("Cancel", { selector: ".sheet button" }));
  fireEvent.click(screen.getByText("Builder + reviewer"));
  expect(names()).toEqual(["builder", "reviewer"]);
});

test("replacing an edited list asks first", async () => {
  await open();
  fireEvent.change(screen.getAllByLabelText("Agent name")[0], { target: { value: "lead" } });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByText("Builder + reviewer"));
  expect(confirm).toHaveBeenCalled();
  expect(names()).toEqual(["lead"]);
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByText("Builder + reviewer"));
  expect(names()).toEqual(["builder", "reviewer"]);
});

test("cancel closes", async () => {
  const onClose = await open();
  fireEvent.click(within(screen.getByRole("contentinfo")).getByText("Cancel"));
  expect(onClose).toHaveBeenCalled();
});

async function toSetup(onClose?: () => void) {
  render(<NewTeam clis={both} onClose={onClose ?? vi.fn()} />);
  await flush();
  await fillProjectAndTask();
  fireEvent.click(screen.getByText("Builder + reviewer"));
  fireEvent.click(screen.getByText("Next"));
}

test("groups are on and switch off", async () => {
  b.startTeam.mockResolvedValue({ team: "shop" });
  await toSetup();
  expect(screen.getByText("Recommended setup")).toBeTruthy();
  const switches = screen.getAllByRole("switch") as HTMLInputElement[];
  expect(switches.map((s) => s.checked)).toEqual([true, true, true, true, true]);
  fireEvent.click(screen.getByRole("switch", { name: "Careful reviews" }));
  fireEvent.click(screen.getByText("Start 2 agents"));
  await flush();
  expect(b.startTeam).toHaveBeenCalledWith({
    project: "/Users/me/shop", task: "Fix login", roles: PRESETS[1].roles,
    practices: { messages: true, safety: true, reviews: false, stopping: true, machine: true },
  });
});

test("basics count comes from the list", async () => {
  await toSetup();
  expect(screen.getByText("5 basics that are always on").closest(".basics")?.textContent).toMatch(/^Plus 5 basics that are always on/);
  fireEvent.click(screen.getByText("See all"));
  expect(screen.getByText("Basic 3")).toBeTruthy();
});

test("start button counts agents and shows the trust line", async () => {
  await toSetup();
  expect(screen.getByText("Start 2 agents")).toBeTruthy();
  expect(screen.getByText("Agents can read, edit and run files in shop.")).toBeTruthy();
  expect(screen.getAllByText("2 rules").length).toBe(5);
});

test("start is pressed once", async () => {
  b.startTeam.mockReturnValue(new Promise(() => {}));
  await toSetup();
  const start = screen.getByText("Start 2 agents") as HTMLButtonElement;
  fireEvent.click(start);
  fireEvent.click(start);
  await flush();
  expect(b.startTeam).toHaveBeenCalledTimes(1);
  expect(start.disabled).toBe(true);
});

test("errors go back to their field; success closes", async () => {
  b.startTeam.mockRejectedValueOnce("role:0: Names use lower-case letters, digits and dashes, and must differ.");
  const onClose = vi.fn();
  await toSetup(onClose);
  fireEvent.click(screen.getByText("Start 2 agents"));
  await flush();
  expect(screen.getByText("New team")).toBeTruthy();
  expect(screen.getByText("Names use lower-case letters, digits and dashes, and must differ.")).toBeTruthy();

  fireEvent.click(screen.getByText("Next"));
  b.startTeam.mockRejectedValueOnce("start: not installed: codex. Install it, then start again.");
  fireEvent.click(screen.getByText("Start 2 agents"));
  await flush();
  expect(screen.getByText("not installed: codex. Install it, then start again.")).toBeTruthy();
  expect(screen.getByText("Recommended setup")).toBeTruthy();

  b.startTeam.mockResolvedValueOnce({ team: "shop" });
  fireEvent.click(screen.getByText("Start 2 agents"));
  await flush();
  expect(onClose).toHaveBeenCalled();

  fireEvent.click(screen.getByText("Back"));
  expect(screen.getByText("New team")).toBeTruthy();
});

test("renaming an agent keeps the agents that report to it", async () => {
  b.startTeam.mockResolvedValue({ team: "shop" });
  await toSetup();
  fireEvent.click(screen.getByText("Back"));
  fireEvent.change(screen.getAllByLabelText("Agent name")[0], { target: { value: "lead" } });
  fireEvent.click(screen.getByText("Next"));
  fireEvent.click(screen.getByText("Start 2 agents"));
  await flush();
  const sent = b.startTeam.mock.calls[0][0].roles;
  expect(sent.map((x: RoleDraft) => [x.name, x.parent])).toEqual([["lead", null], ["reviewer", "lead"]]);
});

test("a cancelled suggestion never replaces the agents", async () => {
  let answer: (v: unknown) => void = () => {};
  b.suggestTeam.mockReturnValue(new Promise((res) => (answer = res)));
  await open();
  await fillProjectAndTask();
  fireEvent.click(screen.getByText("Help me choose"));
  fireEvent.click(screen.getByText("Suggest a team"));
  fireEvent.click(screen.getByText("Cancel", { selector: ".sheet button" }));
  fireEvent.click(screen.getByText("Builder + reviewer"));
  answer({ roles: [r("solo")], why: "late" });
  await flush();
  expect(names()).toEqual(["builder", "reviewer"]);
  expect(screen.queryByText("late")).toBeNull();
});

const efforts = () => Array.from((screen.getByLabelText("Thinking effort") as HTMLSelectElement).options).map((o) => o.text);

test("model menu shows Default, then the choice, and it reaches Start", async () => {
  b.startTeam.mockResolvedValue({ team: "shop" });
  await open();
  await fillProjectAndTask();
  const menu = screen.getByRole("button", { name: "Model and effort for builder" });
  expect(menu.textContent).toBe("Default");
  fireEvent.click(menu);
  fireEvent.change(screen.getByLabelText("Model"), { target: { value: "opus" } });
  fireEvent.change(screen.getByLabelText("Thinking effort"), { target: { value: "xhigh" } });
  expect(menu.textContent).toBe("Opus · Extra high");
  fireEvent.click(screen.getByText("Next"));
  fireEvent.click(screen.getByText("Start 1 agent"));
  await flush();
  expect(b.startTeam.mock.calls[0][0].roles[0]).toMatchObject({ model: "opus", effort: "xhigh" });
});

test("codex efforts follow the chosen model; switching agent resets the choice", async () => {
  await open();
  const menu = screen.getByRole("button", { name: "Model and effort for builder" });
  fireEvent.click(menu);
  fireEvent.change(screen.getByLabelText("Model"), { target: { value: "opus" } });
  fireEvent.click(screen.getByRole("button", { name: "Codex" }));
  expect(menu.textContent).toBe("Default");
  expect(efforts()).toEqual(["Default", "Low", "Medium", "High", "Extra high", "Max"]);
  fireEvent.change(screen.getByLabelText("Model"), { target: { value: "gpt-6.1-sol" } });
  expect(efforts()).toContain("Ultra");
  fireEvent.change(screen.getByLabelText("Thinking effort"), { target: { value: "ultra" } });
  fireEvent.change(screen.getByLabelText("Model"), { target: { value: "gpt-6-luna" } });
  expect(efforts()).not.toContain("Ultra");
  expect(menu.textContent).toBe("gpt-6-luna");
});

test("a draft from an assistant fills the form and keeps its practice choices", async () => {
  const draft = { id: "d1", project: "/Users/me/shop", task: "Add a footer", why: "A small change.", practices: { safety: false },
    roles: [r("builder"), r("reviewer", { agent: "codex", checkout: "read-only", parent: "builder" })] };
  render(<NewTeam clis={both} draft={draft} onClose={() => {}} />);
  await flush();
  screen.getByText("~/shop");
  expect((screen.getByPlaceholderText(/rate limiting/) as HTMLInputElement).value).toBe("Add a footer");
  screen.getByText("A small change.");
  expect(screen.getByDisplayValue("reviewer")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Next/ }));
  await flush();
  const safety = screen.getByRole("switch", { name: /Safe by default/ }) as HTMLInputElement;
  expect(safety.checked).toBe(false);
});
