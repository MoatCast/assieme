import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Preset, RoleDraft } from "./bridge";

const b = vi.hoisted(() => ({
  teamPresets: vi.fn(),
  checkProject: vi.fn(),
  pickFolder: vi.fn(),
  suggestTeam: vi.fn(),
  startTeam: vi.fn(),
  folderPreview: vi.fn(),
  folderSave: vi.fn(),
  folderCreate: vi.fn(),
  pickNewFolder: vi.fn(),
  installTools: vi.fn(),
  herdrFind: vi.fn(),
  herdrImport: vi.fn(),
  confirmReplace: vi.fn(),
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
  screen.getByText("Agents work on their own copy and can’t git push. They’re told not to deploy.");
});

test("the footer says can’t git push with Safe by default on, and can git push with it off", async () => {
  b.teamPresets.mockResolvedValue({ presets: PRESETS, practices: PRACTICES, last: null, models: MODELS, practice_defaults: { safety: false } });
  await open();
  await fillProjectAndTask();
  screen.getByText("Agents work on their own copy and can git push.");
  fireEvent.click(screen.getByText("Customise"));
  screen.getByText("Agents work on their own copy and can git push.");
  fireEvent.click(screen.getByRole("switch", { name: "Safe by default" }));
  screen.getByText("Agents work on their own copy and can’t git push. They’re told not to deploy.");
});

test("Start with recommended starts the team without step 2", async () => {
  b.startTeam.mockResolvedValue({ team: "shop" });
  const onClose = vi.fn();
  const onStarted = vi.fn();
  render(<NewTeam clis={both} onClose={onClose} onStarted={onStarted} />);
  await flush();
  const go = screen.getByText("Start with recommended") as HTMLButtonElement;
  expect(go.disabled).toBe(true);
  await fillProjectAndTask();
  fireEvent.click(go);
  await flush();
  expect(b.startTeam).toHaveBeenCalledWith({
    project: "/Users/me/shop", task: "Fix login", roles: PRESETS[0].roles,
    practices: { messages: true, safety: true, reviews: true, stopping: true, machine: true },
  });
  expect(onStarted).toHaveBeenCalled();
  expect(onClose).toHaveBeenCalled();
});

test("Start with recommended keeps the Settings defaults, and shows what went wrong", async () => {
  b.teamPresets.mockResolvedValue({ presets: PRESETS, practices: PRACTICES, last: null, models: MODELS, practice_defaults: { reviews: false } });
  b.startTeam.mockRejectedValueOnce("start: not installed: codex. Install it, then start again.");
  await open();
  await fillProjectAndTask();
  fireEvent.click(screen.getByText("Start with recommended"));
  await flush();
  expect(b.startTeam.mock.calls[0][0].practices).toMatchObject({ reviews: false, safety: true });
  screen.getByText("not installed: codex. Install it, then start again.");
  expect((screen.getByText("Start with recommended") as HTMLButtonElement).disabled).toBe(false);
});

test("Start with recommended checks the agent names first", async () => {
  await open();
  await fillProjectAndTask();
  fireEvent.change(screen.getAllByLabelText("Agent name")[0], { target: { value: "Bad Name" } });
  fireEvent.click(screen.getByText("Start with recommended"));
  screen.getByText("Names use lower-case letters, digits and dashes, and must differ.");
  expect(b.startTeam).not.toHaveBeenCalled();
});

test("Customise opens Recommended setup", async () => {
  await open();
  await fillProjectAndTask();
  fireEvent.click(screen.getByText("Customise"));
  screen.getByText("Recommended setup");
  expect(b.startTeam).not.toHaveBeenCalled();
});

test("Where should the agents run starts on this Mac, with nothing to set up", async () => {
  await open();
  screen.getByText("Where should the agents run?");
  expect(screen.getByRole("radio", { name: /This Mac/ }).getAttribute("aria-checked")).toBe("true");
  expect(screen.queryByLabelText("Machine")).toBeNull();
});

test("Mix Claude Code and Codex shows only when both are installed", async () => {
  await open({ claude: true, codex: false });
  expect(screen.queryByText(/Mix Claude Code and Codex/)).toBeNull();
  cleanup();
  await open(both);
  screen.getByText("Mix Claude Code and Codex however you like");
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
  fireEvent.click(screen.getByText("Customise"));
  expect(screen.getByText("Names use lower-case letters, digits and dashes, and must differ.")).toBeTruthy();
  expect(screen.queryByText("Recommended setup")).toBeNull();
});

test("project problem blocks Next; task is required", async () => {
  b.checkProject.mockResolvedValue({ ok: false, reason: "This folder isn't a git repository. Assieme gives each agent its own branch, so it needs one." });
  await open();
  const next = screen.getByText("Customise") as HTMLButtonElement;
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

const SETUP = { ok: false, setup: true, reason: "This folder has no saved version yet. Agents work from a saved version of your project, so it needs one." };
const PREVIEW = { files: 214, sample: ["index.html"], left_out: [".env"], empty: false, first: true };
const checking = (c: object) => b.checkProject.mockResolvedValue(c);

test("not ready: developer tools missing offers the installer and rechecks when it's done", async () => {
  checking({ ok: false, tools: true, reason: "This needs Apple’s developer tools first." });
  b.installTools.mockResolvedValue({ ok: true });
  render(<NewTeam clis={both} retryMs={1} recheckMs={5} onClose={vi.fn()} />);
  await flush();
  await fillProjectAndTask();
  screen.getByText("This needs Apple’s developer tools first.");
  screen.getByText("Install developer tools to continue.");
  expect((screen.getByText("Start with recommended") as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByText("Install developer tools"));
  await flush();
  expect(b.installTools).toHaveBeenCalled();
  checking({ ok: true });
  await waitFor(() => expect(screen.queryByText("Install developer tools")).toBeNull());
  expect((screen.getByText("Start with recommended") as HTMLButtonElement).disabled).toBe(false);
});

test("an installer that can't open says so next to its button", async () => {
  checking({ ok: false, tools: true, reason: "This needs Apple’s developer tools first." });
  b.installTools.mockRejectedValue(new Error("xcode-select: not found"));
  await open();
  await fillProjectAndTask();
  fireEvent.click(screen.getByText("Install developer tools"));
  await flush();
  screen.getByText("xcode-select: not found");
});

test("folder too large says to choose another and blocks both buttons", async () => {
  checking({ ok: false, too_large: true, reason: "This folder is very large. Choose the folder for just this project." });
  await open();
  await fillProjectAndTask();
  screen.getByText("This folder is very large. Choose the folder for just this project.");
  screen.getByText("Choose a different folder to continue.");
  expect((screen.getByText("Customise") as HTMLButtonElement).disabled).toBe(true);
});

test("a folder with no saved version offers Set up this folder, in neutral text", async () => {
  checking(SETUP);
  b.folderPreview.mockResolvedValue(PREVIEW);
  await open();
  await fillProjectAndTask();
  expect(screen.getByText(SETUP.reason).closest(".folder-note")).not.toBeNull(); // neutral note, not the red error line
  screen.getByText("Set up this folder to continue.");
  expect((screen.getByText("Start with recommended") as HTMLButtonElement).disabled).toBe(true);
});

test("Save and continue clears the problem and enables the buttons", async () => {
  checking(SETUP);
  b.folderPreview.mockResolvedValue(PREVIEW);
  b.folderSave.mockResolvedValue({ ok: true });
  await open();
  await fillProjectAndTask();
  fireEvent.click(screen.getByText("Set up this folder"));
  await flush();
  screen.getByRole("heading", { name: "Save 214 files as the first version?" });
  checking({ ok: true });
  b.checkProject.mockClear();
  fireEvent.click(screen.getByText("Save and continue"));
  await flush();
  expect(b.folderSave).toHaveBeenCalledWith("/Users/me/shop");
  expect(b.checkProject).toHaveBeenCalledWith("/Users/me/shop");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByText("Set up this folder")).toBeNull();
  expect((screen.getByText("Start with recommended") as HTMLButtonElement).disabled).toBe(false);
  expect((screen.getByText("Customise") as HTMLButtonElement).disabled).toBe(false);
});

test("Not now changes nothing", async () => {
  checking(SETUP);
  b.folderPreview.mockResolvedValue(PREVIEW);
  await open();
  await fillProjectAndTask();
  fireEvent.click(screen.getByText("Set up this folder"));
  await flush();
  fireEvent.click(screen.getByText("Not now"));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(b.folderSave).not.toHaveBeenCalled();
  screen.getByText(SETUP.reason);
  expect((screen.getByText("Customise") as HTMLButtonElement).disabled).toBe(true);
});

test("uncommitted files warn but keep the buttons enabled", async () => {
  checking({ ok: true, uncommitted: 3 });
  await open();
  await fillProjectAndTask();
  screen.getByText("3 files have changes that aren’t in a saved version (uncommitted), so agents won’t see them.");
  expect((screen.getByText("Customise") as HTMLButtonElement).disabled).toBe(false);
  expect((screen.getByText("Start with recommended") as HTMLButtonElement).disabled).toBe(false);
  checking({ ok: true, uncommitted: 1 });
  fireEvent.click(screen.getByText("Choose…"));
  await flush();
  screen.getByText("1 file has changes that aren’t in a saved version (uncommitted), so agents won’t see them.");
});

test("Save them first reuses the dialog with its own title, and keeps Customise enabled when declined", async () => {
  checking({ ok: true, uncommitted: 3 });
  b.folderPreview.mockResolvedValue({ files: 3, sample: ["a", "b", "c"], left_out: [], empty: false, first: false });
  await open();
  await fillProjectAndTask();
  fireEvent.click(screen.getByText("Save them first"));
  await flush();
  screen.getByRole("heading", { name: "Save 3 files as a new version?" });
  fireEvent.click(screen.getByText("Not now"));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect((screen.getByText("Customise") as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(screen.getByText("Save them first"));
  await flush();
  checking({ ok: true });
  fireEvent.click(screen.getByText("Save and continue"));
  await flush();
  expect(b.folderSave).toHaveBeenCalledTimes(1);
  expect(screen.queryByText("Save them first")).toBeNull();
});

test("a failed save shows what git said, with Cancel and Try again", async () => {
  checking(SETUP);
  b.folderPreview.mockResolvedValue(PREVIEW);
  b.folderSave.mockRejectedValueOnce("Unable to create .git/index.lock: File exists");
  await open();
  await fillProjectAndTask();
  fireEvent.click(screen.getByText("Set up this folder"));
  await flush();
  fireEvent.click(screen.getByText("Save and continue"));
  await flush();
  screen.getByText("Git said: Unable to create .git/index.lock: File exists");
  fireEvent.click(screen.getByText("Cancel", { selector: ".modal button" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  screen.getByText(SETUP.reason);
});

test("an empty folder is started from the same button", async () => {
  checking(SETUP);
  b.folderPreview.mockResolvedValue({ files: 0, sample: [], left_out: [], empty: true, first: true });
  await open();
  await fillProjectAndTask();
  fireEvent.click(screen.getByText("Set up this folder"));
  await flush();
  screen.getByRole("heading", { name: "Start this empty project?" });
  checking({ ok: true });
  fireEvent.click(screen.getByText("Start project"));
  await flush();
  expect((screen.getByText("Customise") as HTMLButtonElement).disabled).toBe(false);
});

test("New folder creates it and offers to set it up", async () => {
  b.pickNewFolder.mockResolvedValue("/Users/me/recipes");
  b.folderCreate.mockResolvedValue({ path: "/Users/me/recipes" });
  checking(SETUP);
  b.folderPreview.mockResolvedValue({ files: 0, sample: [], left_out: [], empty: true, first: true });
  await open();
  fireEvent.click(screen.getByText("New folder…"));
  await flush();
  expect(b.folderCreate).toHaveBeenCalledWith("/Users/me/recipes");
  expect(b.checkProject).toHaveBeenCalledWith("/Users/me/recipes");
  screen.getByText("~/recipes");
  screen.getByText(SETUP.reason);
  screen.getByText("Set up this folder");
});

test("New folder: cancelling the picker does nothing, and a refusal shows next to the button", async () => {
  b.pickNewFolder.mockResolvedValue(null);
  await open();
  fireEvent.click(screen.getByText("New folder…"));
  await flush();
  expect(b.folderCreate).not.toHaveBeenCalled();
  b.pickNewFolder.mockResolvedValue("/Users/me/shop");
  b.folderCreate.mockRejectedValue("That folder already exists.");
  fireEvent.click(screen.getByText("New folder…"));
  await flush();
  screen.getByText("That folder already exists.");
  screen.getByText("No folder chosen");
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

test("a suggested model and effort show on the row and reach Start", async () => {
  b.suggestTeam.mockResolvedValue({ roles: [r("builder"), r("reviewer", { model: "opus", effort: "high" })], why: "w" });
  b.startTeam.mockResolvedValue({ team: "shop" });
  await open();
  await fillProjectAndTask();
  fireEvent.click(screen.getByText("Help me choose"));
  fireEvent.click(screen.getByText("Suggest a team"));
  await flush();
  expect(screen.getByRole("button", { name: "Model and effort for builder" }).textContent).toBe("Default");
  expect(screen.getByRole("button", { name: "Model and effort for reviewer" }).textContent).toBe("Opus · High");
  fireEvent.click(screen.getByText("Customise"));
  fireEvent.click(screen.getByText("Start 2 agents"));
  await flush();
  expect(b.startTeam.mock.calls[0][0].roles[1]).toMatchObject({ model: "opus", effort: "high" });
});

test("replacing an edited list asks first", async () => {
  await open();
  fireEvent.change(screen.getAllByLabelText("Agent name")[0], { target: { value: "lead" } });
  const confirm = vi.spyOn(window, "confirm");
  b.confirmReplace.mockResolvedValue(false);
  fireEvent.click(screen.getByText("Builder + reviewer"));
  await flush();
  expect(b.confirmReplace).toHaveBeenCalled();
  expect(confirm).not.toHaveBeenCalled(); // shows nothing in the app's web view
  expect(names()).toEqual(["lead"]);
  b.confirmReplace.mockResolvedValue(true);
  fireEvent.click(screen.getByText("Builder + reviewer"));
  await flush();
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
  fireEvent.click(screen.getByText("Customise"));
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
  expect(screen.getByText("Agents work on their own copy and can’t git push. They’re told not to deploy.")).toBeTruthy();
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

  fireEvent.click(screen.getByText("Customise"));
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
  fireEvent.click(screen.getByText("Customise"));
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
  fireEvent.click(screen.getByText("Customise"));
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
  fireEvent.click(screen.getByRole("button", { name: /Customise/}));
  await flush();
  const safety = screen.getByRole("switch", { name: /Safe by default/ }) as HTMLInputElement;
  expect(safety.checked).toBe(false);
});

const deferred = <T,>() => {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => (resolve = res));
  return { promise, resolve };
};
const go = () => screen.getByText("Start with recommended") as HTMLButtonElement;

test("the buttons wait for the folder check, and say so", async () => {
  const slow = deferred<{ ok: boolean }>();
  b.checkProject.mockReturnValue(slow.promise);
  await open();
  await fillProjectAndTask();
  screen.getByText("Checking this folder…");
  expect(go().disabled).toBe(true);
  expect((screen.getByText("Customise") as HTMLButtonElement).disabled).toBe(true);
  await act(async () => slow.resolve({ ok: true }));
  expect(go().disabled).toBe(false);
});

test("a folder check that fails shows why, keeps the buttons off, and Choose… tries again", async () => {
  b.checkProject.mockRejectedValueOnce(new Error("service went away"));
  await open();
  await fillProjectAndTask();
  screen.getByText("service went away");
  screen.getByText("Couldn’t check this folder. Choose it again to continue.");
  expect(go().disabled).toBe(true);
  fireEvent.click(screen.getByText("Choose…"));
  await flush();
  expect(go().disabled).toBe(false);
  expect(screen.queryByText("service went away")).toBeNull();
});

test("a slow check for an earlier folder can't enable the buttons for the current one", async () => {
  const a = deferred<object>();
  b.pickFolder.mockResolvedValueOnce("/Users/me/a").mockResolvedValueOnce("/Users/me/b");
  b.checkProject.mockImplementation((p: string) => (p.endsWith("/a") ? a.promise : Promise.resolve(SETUP)));
  await open();
  fireEvent.change(screen.getByLabelText("What should the team work on?"), { target: { value: "Fix login" } });
  fireEvent.click(screen.getByText("Choose…"));
  await flush();
  fireEvent.click(screen.getByText("Choose…"));
  await flush();
  screen.getByText("Set up this folder");
  await act(async () => a.resolve({ ok: true }));
  screen.getByText("Set up this folder");
  expect(go().disabled).toBe(true);
});

test("a picker that fails says so", async () => {
  b.pickFolder.mockRejectedValue(new Error("dialog refused"));
  await open();
  fireEvent.click(screen.getByText("Choose…"));
  await flush();
  screen.getByText("dialog refused");
});

test("an assistant's draft folder goes through the same check and offers the same click", async () => {
  checking(SETUP);
  const draft = { id: "d1", project: "/Users/me/shop", task: "Add a footer", why: "x", practices: {}, roles: [r("builder")] };
  render(<NewTeam clis={both} draft={draft} onClose={() => {}} />);
  await flush();
  expect(b.checkProject).toHaveBeenCalledWith("/Users/me/shop");
  screen.getByText("Set up this folder");
  expect(go().disabled).toBe(true);
});

test("the developer tools poll waits for the answer before asking again", async () => {
  vi.useFakeTimers();
  try {
    const slow = deferred<object>();
    b.checkProject.mockResolvedValueOnce({ ok: false, tools: true, reason: "This needs Apple’s developer tools first." });
    render(<NewTeam clis={both} recheckMs={10} onClose={vi.fn()} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    fireEvent.click(screen.getByText("Choose…"));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    b.checkProject.mockReturnValue(slow.promise);
    b.checkProject.mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(b.checkProject).toHaveBeenCalledTimes(1);
  } finally {
    vi.useRealTimers();
  }
});

test("an import from herdr fills the form, says what didn't carry over, and a preset takes it back out", async () => {
  b.herdrFind.mockResolvedValue({ teams: [{ id: "running:shop", label: "shop", where: "Running in herdr", agents: 2, running: true }] });
  b.herdrImport.mockResolvedValue({
    id: "d1", from: "herdr", project: "/Users/me/shop", task: "Add a cart", why: "", practices: {}, running: "shop",
    roles: [r("lead", { model: "opus", effort: "high" }), r("builder", { agent: "codex", parent: "lead" })],
    notes: [{ text: "2 agents ran with wider permissions (auto mode).", agents: ["lead", "builder"] }],
  });
  await open();
  expect(screen.getByText("Start with recommended")).toBeTruthy();
  fireEvent.click(screen.getByText("From herdr…"));
  await flush();
  fireEvent.click(screen.getByText("Import"));
  await flush();
  expect(names()).toEqual(["lead", "builder"]);
  expect((screen.getByLabelText("What should the team work on?") as HTMLInputElement).value).toBe("Add a cart");
  expect(b.checkProject).toHaveBeenCalledWith("/Users/me/shop");
  expect(screen.getByText("shop is still running in herdr")).toBeTruthy();
  expect(screen.getByText("2 agents ran with wider permissions (auto mode).")).toBeTruthy();
  expect(screen.getByText("lead, builder")).toBeTruthy();
  expect(screen.getByText("Opus · High")).toBeTruthy(); // each agent's model and effort, as New team shows them
  expect(screen.getByText("Start team")).toBeTruthy();
  expect(screen.getByText("From herdr…").getAttribute("aria-pressed")).toBe("true");
  b.confirmReplace.mockResolvedValue(true);
  fireEvent.click(screen.getByText("Builder + reviewer"));
  await flush();
  expect(screen.queryByText("shop is still running in herdr")).toBeNull();
  expect(screen.queryByText("lead, builder")).toBeNull();
  expect(screen.getByText("Start with recommended")).toBeTruthy();
});

test("an import over edited agents asks first, and No keeps them", async () => {
  b.herdrFind.mockResolvedValue({ teams: [{ id: "saved:default:w1", label: "docs", where: "Saved in herdr", agents: 1, running: false }] });
  b.herdrImport.mockResolvedValue({ id: "d2", from: "herdr", project: "/Users/me/docs", task: "", why: "", practices: {},
    roles: [r("writer")], notes: [] });
  await open();
  fireEvent.change(screen.getAllByLabelText("Agent name")[0], { target: { value: "lead" } });
  b.confirmReplace.mockResolvedValue(false);
  fireEvent.click(screen.getByText("From herdr…"));
  await flush();
  fireEvent.click(screen.getByText("Import"));
  await flush();
  expect(b.confirmReplace).toHaveBeenCalled();
  expect(names()).toEqual(["lead"]);
  expect(screen.queryByText("Import a team from herdr")).toBeNull(); // the sheet closes either way
});
