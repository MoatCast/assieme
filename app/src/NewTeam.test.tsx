import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Preset, RoleDraft } from "./bridge";
import { KINDS, TABLE } from "./editorFixtures";
import { PRACTICES } from "./practicesFixture";

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
  bringOverFind: vi.fn(),
  bringOverWorkspaces: vi.fn(),
  bringOverImport: vi.fn(),
  confirmReplace: vi.fn(),
  containerStatus: vi.fn(),
  containerBuild: vi.fn(),
  containerOpen: vi.fn(),
  toolsFind: vi.fn(),
  toolsSuggest: vi.fn(),
  setupStatus: vi.fn(),
  confirmDiscard: vi.fn(),
  getSettings: vi.fn(),
}));
vi.mock("./bridge", async (orig) => ({ ...(await orig<typeof import("./bridge")>()), ...b }));

import NewTeam from "./NewTeam";

const r = (name: string, extra: Partial<RoleDraft> = {}): RoleDraft => ({
  name, agent: "claude", job: `${name} job`, checkout: "branch", parent: null, brief: `You are ${name}.`, ...extra,
});
const PRESETS: Preset[] = [
  { id: "one-agent", label: "One agent", roles: [r("builder")] },
  { id: "builder-reviewer", label: "Builder and reviewer", roles: [r("builder"), r("reviewer", { checkout: "read-only", parent: "builder" })] },
  { id: "full", label: "Full team", roles: [r("planner"), r("builder-1")] },
];

/** What New team sends: every agent with a role (from its name) and a permission level. */
const sent = (rs: RoleDraft[]) => rs.map((x) => ({ ...x, kind: /^(planner|builder|reviewer|tester)/.exec(x.name)?.[1] ?? null,
  level: x.checkout === "read-only" ? "read-only" : "safe" }));

const LEVELS = ["low", "medium", "high", "xhigh", "max"];
const MODELS = {
  claude: { models: [{ id: "fable", label: "Fable", efforts: LEVELS }, { id: "opus", label: "Opus", efforts: LEVELS }], efforts: LEVELS },
  codex: {
    models: [{ id: "gpt-6.1-sol", label: "gpt-6.1-sol", efforts: [...LEVELS, "ultra"] }, { id: "gpt-6-luna", label: "gpt-6-luna", efforts: LEVELS }],
    efforts: LEVELS,
  },
};

const settle = () => act(async () => {});
/** Let promises settle, then step past the 300 ms click guard, as a person does between deliberate clicks. */
const flush = async () => { await settle(); vi.setSystemTime(new Date(Date.now() + 400)); };
const both = { claude: true, codex: true };
const READY = { git: true, agents: { claude: { installed: true, signed_in: true }, codex: { installed: true, signed_in: true } } };
const SETUP = { ok: false, setup: true, reason: "This folder has no saved version yet." };
const PREVIEW = { files: 214, sample: ["index.html"], first: true, empty: false, left_out: [] };
const deferred = <T,>() => {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => (resolve = res));
  return { promise, resolve };
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear(); // a draft kept by an earlier test would offer to continue
  vi.useFakeTimers({ toFake: ["Date"] }); // the 300 ms click guard reads the clock
  b.teamPresets.mockResolvedValue({ presets: PRESETS, practices: PRACTICES, last: null, models: MODELS, kinds: KINDS, permissions: TABLE });
  b.checkProject.mockResolvedValue({ ok: true });
  b.pickFolder.mockResolvedValue("/Users/me/shop");
  b.setupStatus.mockResolvedValue(READY);
  b.getSettings.mockResolvedValue({ fixes: { nudge: "auto", compact: "auto", tell: "auto", quiet: "auto" } });
  b.confirmDiscard.mockResolvedValue(true);
  b.toolsFind.mockResolvedValue({ items: [], notices: [] });
  b.toolsSuggest.mockResolvedValue([]);
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

/** Past the click guard: a click right after a step change is ignored on purpose. */
const later = () => vi.setSystemTime(new Date(Date.now() + 400));
const heading = () => screen.getByRole("heading", { level: 1 }).textContent;
const names = () => screen.getAllByLabelText("Agent name").map((e) => (e as HTMLInputElement).value);
const button = (name: string | RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;

async function open(clis: Record<string, boolean> | null = both, extra: Partial<Parameters<typeof NewTeam>[0]> = {}) {
  const onClose = vi.fn();
  render(<NewTeam clis={clis} onClose={onClose} {...extra} />);
  await flush();
  return onClose;
}
async function chooseFolder() {
  fireEvent.click(button(/Choose a folder…/));
  await flush();
}
async function toTeam(task = "Fix login") {
  await chooseFolder();
  if (task) {
    fireEvent.change(screen.getByRole("textbox", { name: "What should the team work on?" }), { target: { value: task } });
    fireEvent.click(button("Continue"));
  } else fireEvent.click(button("Skip"));
  await flush();
}
async function pick(name: RegExp) {
  later();
  fireEvent.click(button(name));
  await flush();
}
async function toStart(preset = /Builder and reviewer/, task = "Fix login") {
  await toTeam(task);
  await pick(preset);
  later();
}
async function editAgents() {
  fireEvent.click(button("Edit agents ›"));
  await flush();
}

test("a new team goes Project → Task → Team → Start and starts with what was chosen", async () => {
  b.startTeam.mockResolvedValue({ team: "shop" });
  const onClose = await open();
  expect(heading()).toBe("Which project should the team work on?");
  await chooseFolder();
  expect(b.checkProject).toHaveBeenCalledWith("/Users/me/shop");
  expect(heading()).toBe("What should the team work on?");
  fireEvent.change(screen.getByRole("textbox", { name: "What should the team work on?" }), { target: { value: "Fix login" } });
  fireEvent.click(button("Continue"));
  await flush();
  expect(heading()).toBe("Who should work on it?");
  await pick(/Full team/);
  later();
  expect(heading()).toBe("What Assieme does for this team");
  expect(screen.getByRole("navigation", { name: "Setup steps" }).textContent).toContain("✓ Full team");
  fireEvent.click(button("Start 2 agents"));
  await flush();
  expect(b.startTeam).toHaveBeenCalledWith({ project: "/Users/me/shop", task: "Fix login", roles: sent(PRESETS[2].roles), practices: {} });
  expect(onClose).toHaveBeenCalled();
});

test("Back keeps every answer", async () => {
  await open();
  await toStart();
  fireEvent.click(button("Back"));
  expect(heading()).toBe("Who should work on it?");
  later();
  fireEvent.click(button("Back"));
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("Fix login");
  later();
  fireEvent.click(button("Back"));
  expect(heading()).toBe("Which project should the team work on?");
  screen.getByText("~/shop");
  await flush();
  expect(heading()).toBe("Which project should the team work on?"); // a good folder doesn't move on again by itself
});

test("a skipped task starts with an empty task", async () => {
  b.startTeam.mockResolvedValue({ team: "shop" });
  await open();
  await toStart(/One agent/, "");
  expect(screen.getByRole("navigation", { name: "Setup steps" }).textContent).toContain("✓ No task");
  fireEvent.click(button("Start 1 agent"));
  await flush();
  expect(b.startTeam.mock.calls[0][0].task).toBe("");
});

test("the indicator's done steps go back", async () => {
  await open();
  await toStart();
  fireEvent.click(button("✓ Fix login"));
  expect(heading()).toBe("What should the team work on?");
  later();
  fireEvent.click(button("✓ shop"));
  expect(heading()).toBe("Which project should the team work on?");
  expect(screen.queryByRole("button", { name: "✓ Fix login" })).toBeNull(); // later steps aren't buttons
});

test("a slow check for an earlier folder can't move on for the current one", async () => {
  const a = deferred<object>();
  b.pickFolder.mockResolvedValueOnce("/Users/me/a").mockResolvedValueOnce("/Users/me/b");
  b.checkProject.mockImplementation((p: string) => (p.endsWith("/a") ? a.promise : Promise.resolve(SETUP)));
  b.folderPreview.mockResolvedValue(PREVIEW);
  await open();
  fireEvent.click(button(/Choose a folder…/));
  await flush();
  fireEvent.click(button("Choose another…"));
  await flush();
  screen.getByText("This folder has no saved version yet");
  await act(async () => a.resolve({ ok: true }));
  expect(heading()).toBe("Which project should the team work on?");
  screen.getByText("This folder has no saved version yet");
});

test("a double-click on a team option doesn't press the next panel", async () => {
  b.startTeam.mockResolvedValue({ team: "shop" });
  await open();
  await toTeam();
  later();
  fireEvent.click(button(/Builder and reviewer/));
  await settle();
  vi.setSystemTime(new Date(Date.now() + 50));
  // The second click lands where the option was: Start's primary sits elsewhere, and a click inside the guard does nothing.
  const startButton = button("Start 2 agents");
  fireEvent.click(startButton);
  await settle();
  expect(heading()).toBe("What Assieme does for this team");
  expect(b.startTeam).not.toHaveBeenCalled();
});

test("errors go back to their step", async () => {
  b.startTeam.mockRejectedValueOnce("project: This project already has a running team: shop. Stop it first.");
  const onClose = await open();
  await toStart();
  later();
  fireEvent.click(button("Start 2 agents"));
  await flush();
  expect(heading()).toBe("Which project should the team work on?");
  screen.getByText("This project already has a running team: shop. Stop it first.");

  b.startTeam.mockRejectedValueOnce("role:0: Names use lower-case letters, digits and dashes, and must differ.");
  later();
  fireEvent.click(button("Continue")); // Project → Task
  later();
  fireEvent.click(button("Continue")); // Task → Team
  await pick(/Builder and reviewer/);
  later();
  fireEvent.click(button("Start 2 agents"));
  await flush();
  screen.getByRole("dialog", { name: "Agents for shop" });
  screen.getByText("Names use lower-case letters, digits and dashes, and must differ.");
  fireEvent.click(button("Done"));

  b.startTeam.mockRejectedValueOnce("start: not installed: codex. Install it, then start again.");
  fireEvent.click(button("Start 2 agents"));
  await flush();
  screen.getByText("not installed: codex. Install it, then start again.");
  expect(heading()).toBe("What Assieme does for this team");

  b.startTeam.mockResolvedValueOnce({ team: "shop" });
  fireEvent.click(button("Start 2 agents"));
  await flush();
  expect(onClose).toHaveBeenCalled();
});

test("start is pressed once", async () => {
  b.startTeam.mockReturnValue(new Promise(() => {}));
  await open();
  await toStart();
  await flush();
  const start = button("Start 2 agents");
  fireEvent.click(start);
  fireEvent.click(start);
  await flush();
  expect(b.startTeam).toHaveBeenCalledTimes(1);
  expect(start.disabled).toBe(true);
});

test("a bad agent name opens Edit agents with the error instead of starting", async () => {
  await open();
  await toStart();
  await editAgents();
  fireEvent.change(screen.getAllByLabelText("Agent name")[0], { target: { value: "Bad Name" } });
  fireEvent.click(button("Done"));
  fireEvent.click(button("Start 2 agents"));
  await flush();
  expect(b.startTeam).not.toHaveBeenCalled();
  screen.getByText("Names use lower-case letters, digits and dashes, and must differ.");
});

test("a draft opens at Start; a draft whose folder fails opens at Project", async () => {
  const draft = { id: "d1", project: "/Users/me/shop", task: "Add a footer", why: "A small change.", practices: { safety: false },
    roles: [r("builder"), r("reviewer", { agent: "codex", checkout: "read-only", parent: "builder" })] };
  await open(both, { draft });
  expect(heading()).toBe("What Assieme does for this team");
  screen.getByText(/Safe by default off/);
  await editAgents();
  screen.getByText("A small change.");
  expect(names()).toEqual(["builder", "reviewer"]);
  cleanup();
  b.checkProject.mockResolvedValue(SETUP);
  b.folderPreview.mockResolvedValue(PREVIEW);
  await open(both, { draft });
  expect(b.checkProject).toHaveBeenCalledWith("/Users/me/shop");
  expect(heading()).toBe("Which project should the team work on?");
  screen.getByText("This folder has no saved version yet");
});

const herdrRow = (id: string, label: string, summary: string, running: string | null = null) =>
  ({ projects: [{ id: `herdr:${id}`, tool: "herdr", label, summary, count: 1, running }], unreadable: [], problems: [], looked_for: [] });

test("herdr imports open at Start with their notes in Edit agents", async () => {
  b.bringOverFind.mockResolvedValue(herdrRow("running:shop", "shop", "Running in herdr · 2 agents", "Still running in herdr: stop it there first."));
  b.herdrImport.mockResolvedValue({
    id: "d1", from: "herdr", project: "/Users/me/shop", task: "Add a cart", why: "", practices: {}, running: "shop",
    roles: [r("lead", { model: "opus", effort: "high" }), r("builder", { agent: "codex", parent: "lead" })],
    notes: [{ text: "2 agents ran with wider permissions (auto mode).", agents: ["lead", "builder"] }],
  });
  await open();
  await toTeam();
  fireEvent.click(button("Bring your team over ›"));
  await flush();
  fireEvent.click(screen.getByText("Bring over"));
  await flush();
  expect(b.herdrImport).toHaveBeenCalledWith("running:shop");
  expect(heading()).toBe("What Assieme does for this team");
  screen.getByText("shop is still running in herdr");
  expect(screen.getByRole("navigation", { name: "Setup steps" }).textContent).toContain("✓ Brought over");
  await editAgents();
  expect(names()).toEqual(["lead", "builder"]);
  screen.getByText("2 agents ran with wider permissions (auto mode).");
  screen.getByText("lead, builder");
  screen.getByText("Opus · High");
});

test("an import over edited agents asks first, and No keeps them", async () => {
  b.bringOverFind.mockResolvedValue(herdrRow("saved:default:w1", "docs", "Saved in herdr · 1 agent"));
  b.herdrImport.mockResolvedValue({ id: "d2", from: "herdr", project: "/Users/me/docs", task: "", why: "", practices: {},
    roles: [r("writer")], notes: [] });
  await open();
  await toStart(/One agent/);
  await editAgents();
  fireEvent.change(screen.getAllByLabelText("Agent name")[0], { target: { value: "lead" } });
  fireEvent.click(button("Done"));
  fireEvent.click(button("Back"));
  later();
  b.confirmReplace.mockResolvedValue(false);
  fireEvent.click(button("Bring your team over ›"));
  await flush();
  fireEvent.click(screen.getByText("Bring over"));
  await flush();
  expect(b.confirmReplace).toHaveBeenCalled();
  expect(screen.queryByText("Bring your team over")).toBeNull(); // the sheet closes either way
  expect(heading()).toBe("Who should work on it?");
});

test("a team brought over from Conductor starts with its scripts, the ones said yes to, its cards and where it came from", async () => {
  b.bringOverFind.mockResolvedValue({ projects: [{ id: "conductor:/Users/me/shop", tool: "conductor", label: "shop",
    summary: "1 workspace · Claude Code · 1 with unmerged work", count: 1, running: null }], unreadable: [], problems: [], looked_for: [] });
  b.bringOverWorkspaces.mockResolvedValue({ label: "shop", tool: "Conductor",
    rows: [{ name: "tokyo", branch: "feature/retry", ticked: true, becomes: "Becomes builder-1 · Claude Code · 4 commits not merged", note: "" }],
    scripts: [{ which: "setup", line: "npm ci", becomes: "runs in each new agent’s copy" }] });
  const roles = [r("builder-1", { worktree: "/Users/me/conductor/workspaces/shop/tokyo", branch: "feature/retry", resume: "abc" })];
  b.bringOverImport.mockResolvedValue({ id: "d3", from: "bringover", tool: "Conductor", label: "shop", project: "/Users/me/shop",
    task: "", why: "", practices: {}, roles, scripts: { setup: "npm ci" }, running: null,
    cards: [{ title: "Retry", status: "doing", role: "builder-1" }],
    notes: [{ text: "They start with Assieme's safe permissions.", agents: [] }] });
  b.startTeam.mockResolvedValue({ team: "shop" });
  await open();
  await toTeam("Finish the retry");
  fireEvent.click(button("Bring your team over ›"));
  await flush();
  fireEvent.click(screen.getByText("Bring over"));
  await flush();
  fireEvent.click(screen.getByLabelText("Run this setup in each new agent’s copy"));
  fireEvent.click(screen.getByText("Bring over 1 agent"));
  await flush();
  expect(b.bringOverImport).toHaveBeenCalledWith("conductor:/Users/me/shop", ["tokyo"]);
  await editAgents();
  screen.getByText("Brought over from Conductor. What didn’t carry over");
  fireEvent.click(button("Done"));
  later();
  fireEvent.click(button("Start 1 agent"));
  await flush();
  expect(b.startTeam).toHaveBeenCalledWith(expect.objectContaining({
    scripts: { setup: "npm ci" }, allow_scripts: ["setup"], cards: [{ title: "Retry", status: "doing", role: "builder-1" }],
    origin: { tool: "Conductor", label: "shop" },
    roles: [expect.objectContaining({ worktree: "/Users/me/conductor/workspaces/shop/tokyo", branch: "feature/retry", resume: "abc" })],
  }));
});

test("Escape asks before discarding once a folder is chosen", async () => {
  const onClose = await open();
  fireEvent.keyDown(window, { key: "Escape" });
  await flush();
  expect(b.confirmDiscard).not.toHaveBeenCalled();
  expect(onClose).toHaveBeenCalledTimes(1);
  cleanup();
  const close2 = await open();
  await chooseFolder();
  b.confirmDiscard.mockResolvedValueOnce(false);
  fireEvent.keyDown(window, { key: "Escape" });
  await flush();
  expect(b.confirmDiscard).toHaveBeenCalled();
  expect(close2).not.toHaveBeenCalled();
  expect(heading()).toBe("What should the team work on?"); // never Back
  fireEvent.keyDown(window, { key: "Escape" });
  await flush();
  expect(close2).toHaveBeenCalled();
});

test("Escape closes Team rules before it means Cancel", async () => {
  const onClose = await open();
  await toStart();
  fireEvent.click(button("Review team rules ›"));
  expect(heading()).toBe("Team rules for shop");
  fireEvent.keyDown(window, { key: "Escape" });
  await flush();
  expect(heading()).toBe("What Assieme does for this team");
  expect(onClose).not.toHaveBeenCalled();
});

test("cancel closes", async () => {
  const onClose = await open();
  fireEvent.click(within(screen.getByRole("contentinfo")).getByText("Cancel"));
  await flush();
  expect(onClose).toHaveBeenCalled();
});

test("a stopped service shows Offline and recovers", async () => {
  b.teamPresets.mockRejectedValue(new Error("no brain"));
  render(<NewTeam clis={null} retryMs={1} onClose={vi.fn()} />);
  await flush();
  screen.getByText(/service isn’t running/);
  b.teamPresets.mockResolvedValue({ presets: PRESETS, practices: PRACTICES, last: null, models: MODELS });
  await waitFor(() => expect(screen.queryByText(/service isn’t running/)).toBeNull());
  expect(heading()).toBe("Which project should the team work on?");
});

test("a new user without developer tools or a signed-in agent starts at Developer tools", async () => {
  b.setupStatus.mockResolvedValue({ git: false, agents: {} });
  b.installTools.mockResolvedValue({ ok: true });
  await open({ claude: false, codex: false });
  expect(heading()).toBe("Install Apple’s developer tools");
  expect(screen.getByRole("navigation", { name: "Setup steps" }).textContent).toContain("Agent");
  fireEvent.click(button("Install developer tools"));
  await flush();
  fireEvent.click(button("Continue while it installs"));
  expect(heading()).toBe("Which agent do you pay for?");
});

test("an agent installed but signed out adds the Agent step", async () => {
  b.setupStatus.mockResolvedValue({ git: true, agents: { claude: { installed: true, signed_in: false } } });
  await open({ claude: true });
  expect(heading()).toBe("Which agent do you pay for?");
});

test("Same as last time comes first", async () => {
  b.teamPresets.mockResolvedValue({ presets: PRESETS, practices: PRACTICES, last: { id: "shop", label: "Same as last time", roles: [r("planner"), r("builder-1")] },
    models: MODELS, recent: ["/Users/me/recipes"] });
  await open();
  screen.getByRole("button", { name: /recipes/ });
  await toTeam();
  const options = screen.getAllByRole("button").filter((x) => x.classList.contains("option"));
  expect(options[0].textContent).toContain("Same as last time");
});

test("developer tools missing for the folder: install, then a recheck offers Continue", async () => {
  b.checkProject.mockResolvedValueOnce({ ok: false, tools: true, reason: "x" });
  b.installTools.mockResolvedValue({ ok: true });
  render(<NewTeam clis={both} recheckMs={5} onClose={vi.fn()} />);
  await flush();
  await chooseFolder();
  fireEvent.click(button("Install developer tools"));
  await flush();
  expect(b.installTools).toHaveBeenCalled();
  b.checkProject.mockResolvedValue({ ok: true });
  await waitFor(() => screen.getByRole("button", { name: "Continue" }));
  expect(heading()).toBe("Which project should the team work on?");
  fireEvent.click(button("Continue"));
  expect(heading()).toBe("What should the team work on?");
});

test("a folder with no saved version: Save and continue rechecks, then Continue", async () => {
  b.checkProject.mockResolvedValueOnce(SETUP);
  b.folderPreview.mockResolvedValue(PREVIEW);
  b.folderSave.mockResolvedValue({ ok: true });
  await open();
  await chooseFolder();
  fireEvent.click(button("Save and continue"));
  await flush();
  expect(b.folderSave).toHaveBeenCalledWith("/Users/me/shop");
  expect(b.checkProject).toHaveBeenCalledTimes(2);
  fireEvent.click(button("Continue"));
  expect(heading()).toBe("What should the team work on?");
});

test("replacing edited agents asks first", async () => {
  await open();
  await toStart(/One agent/);
  await editAgents();
  fireEvent.change(screen.getAllByLabelText("Agent name")[0], { target: { value: "lead" } });
  fireEvent.click(button("Done"));
  fireEvent.click(button("Back"));
  b.confirmReplace.mockResolvedValue(false);
  await pick(/Builder and reviewer/);
  expect(b.confirmReplace).toHaveBeenCalled();
  expect(heading()).toBe("Who should work on it?");
  b.confirmReplace.mockResolvedValue(true);
  await pick(/Builder and reviewer/);
  await editAgents();
  expect(names()).toEqual(["builder", "reviewer"]);
});

test("renaming an agent keeps the agents that report to it", async () => {
  b.startTeam.mockResolvedValue({ team: "shop" });
  await open();
  await toStart();
  await editAgents();
  fireEvent.change(screen.getAllByLabelText("Agent name")[0], { target: { value: "lead" } });
  fireEvent.click(button("Done"));
  fireEvent.click(button("Start 2 agents"));
  await flush();
  expect(b.startTeam.mock.calls[0][0].roles.map((x: RoleDraft) => [x.name, x.parent])).toEqual([["lead", null], ["reviewer", "lead"]]);
});

test("Help me choose reads the task, and the suggestion's models reach Start", async () => {
  b.suggestTeam.mockResolvedValue({ roles: [r("builder"), r("reviewer", { model: "opus", effort: "high" })], why: "A small change with a check." });
  b.startTeam.mockResolvedValue({ team: "shop" });
  await open();
  await toTeam();
  await pick(/Help me choose/);
  fireEvent.click(button("Suggest a team"));
  await flush();
  expect(b.suggestTeam).toHaveBeenCalledWith({ project: "/Users/me/shop", size: "", review: "every", budget: "lean", task: "Fix login" });
  fireEvent.click(button("Use this team"));
  await flush();
  later();
  expect(screen.getByRole("navigation", { name: "Setup steps" }).textContent).toContain("✓ Suggested team");
  await editAgents();
  screen.getByText("A small change with a check.");
  expect(button("Model and effort for reviewer").textContent).toBe("Opus · High");
  fireEvent.click(button("Done"));
  fireEvent.click(button("Start 2 agents"));
  await flush();
  expect(b.startTeam.mock.calls[0][0].roles[1]).toMatchObject({ model: "opus", effort: "high" });
});

test("a group turned off in Team rules reaches Start for this team", async () => {
  b.startTeam.mockResolvedValue({ team: "shop" });
  await open();
  await toStart();
  fireEvent.click(button("Review team rules ›"));
  fireEvent.click(screen.getByRole("switch", { name: "Careful reviews" }));
  fireEvent.click(button("Done"));
  later();
  screen.getByText(/Each agent gets 8 team rules \(Careful reviews off\)/);
  fireEvent.click(button("Start 2 agents"));
  await flush();
  expect(b.startTeam.mock.calls[0][0].practices).toEqual({ reviews: false });
});

test("While they work follows Settings › Fixes", async () => {
  b.getSettings.mockResolvedValue({ fixes: { nudge: "ask", compact: "auto", tell: "auto", quiet: "auto" } });
  await open();
  await toStart();
  screen.getByText(/Change this in Settings › Fixes\./);
});

test("each agent shows its tools, suggested for its role, and Start sends the choice", async () => {
  const items = ["server:context7", "server:browser-mcp", "plugin:planban@planban"].map((id) => ({
    id, type: id.split(":")[0] as "server" | "plugin", name: id.split(":")[1], description: "", locked: false, why: "" }));
  b.toolsFind.mockResolvedValue({ items, notices: [] });
  b.toolsSuggest.mockImplementation(async (kind: string | null) => (kind === "reviewer" ? ["server:browser-mcp"] : []));
  b.startTeam.mockResolvedValue({ team: "shop" });
  await open();
  await toStart();
  await flush();
  await editAgents();
  screen.getByRole("button", { name: "Tools for builder: 3 of 3" });
  fireEvent.click(button("Tools for reviewer: 2 of 3"));
  screen.getByText("Suggested for a reviewer: browser and deploy tools are off.");
  fireEvent.click(screen.getByRole("checkbox", { name: "planban" }));
  fireEvent.click(within(screen.getAllByRole("dialog").pop()!).getByText("Done"));
  screen.getByRole("button", { name: "Tools for reviewer: 1 of 3" });
  fireEvent.click(button("Done"));
  fireEvent.click(button("Start 2 agents"));
  await flush();
  const roles = b.startTeam.mock.calls[0][0].roles as RoleDraft[];
  expect(roles[1].tools_off).toEqual(["server:browser-mcp", "plugin:planban@planban"]);
  expect(roles[0].tools_off).toEqual([]);
});

test("changing an agent's kind resets its model and picks its tools again", async () => {
  b.toolsFind.mockImplementation(async (kind: string) => ({ items: [{ id: `server:${kind}-only`, type: "server", name: `${kind}-only`,
    description: "", locked: false, why: "" }], notices: [] }));
  await open();
  await toStart(/One agent/);
  await flush();
  await editAgents();
  fireEvent.click(button("Model and effort for builder"));
  fireEvent.change(screen.getByLabelText("Model"), { target: { value: "opus" } });
  fireEvent.click(button("Agent: Claude Code"));
  fireEvent.click(screen.getByRole("menuitemradio", { name: /Codex/ }));
  await flush();
  expect(button("Model and effort for builder").textContent).toBe("Default");
  fireEvent.click(button("Tools for builder: 1 of 1"));
  screen.getByRole("checkbox", { name: "codex-only" });
});

test("an agent can run sealed off, and Start waits for the container", async () => {
  b.containerStatus.mockResolvedValue({ tool: "docker", running: true, image_ready: false });
  await open();
  await toStart(/One agent/);
  await editAgents();
  fireEvent.click(button("Agent: Claude Code"));
  fireEvent.click(screen.getByRole("menuitemcheckbox", { name: /Run sealed off/ }));
  await flush();
  fireEvent.click(button("Done"));
  screen.getByText("Get the container ready to continue.");
  expect(button("Start 1 agent").disabled).toBe(true);
});

test("closing mid-setup offers to continue next time", async () => {
  localStorage.clear();
  await open();
  await toTeam("Add a cart");
  await pick(/Builder and reviewer/);
  cleanup(); // the window closes
  await open();
  later();
  fireEvent.click(button("Continue setting up shop"));
  await flush();
  expect(heading()).toBe("What Assieme does for this team");
  expect(screen.getByRole("navigation", { name: "Setup steps" }).textContent).toContain("✓ Add a cart");
  await editAgents();
  expect(names()).toEqual(["builder", "reviewer"]);
  localStorage.clear();
});

test("a started or discarded team leaves no draft behind", async () => {
  localStorage.clear();
  b.startTeam.mockResolvedValue({ team: "shop" });
  await open();
  await toStart();
  fireEvent.click(button("Start 2 agents"));
  await flush();
  cleanup();
  await open();
  expect(screen.queryByRole("button", { name: /Continue setting up/ })).toBeNull();
  await chooseFolder();
  fireEvent.keyDown(window, { key: "Escape" });
  await flush();
  cleanup();
  await open();
  expect(screen.queryByRole("button", { name: /Continue setting up/ })).toBeNull();
});

test("a double-click on Project's Continue doesn't press Task's Continue", async () => {
  await open();
  await chooseFolder();
  fireEvent.change(screen.getByRole("textbox", { name: "What should the team work on?" }), { target: { value: "Fix login" } });
  fireEvent.click(button("✓ shop"));
  later();
  fireEvent.click(button("Continue"));
  fireEvent.click(button("Continue"));
  expect(heading()).toBe("What should the team work on?");
});

test("Cancel with no folder chosen keeps the setup kept from last time", async () => {
  await open();
  await toTeam("Add a cart");
  cleanup();
  const onClose = await open();
  fireEvent.click(within(screen.getByRole("contentinfo")).getByText("Cancel"));
  await flush();
  expect(onClose).toHaveBeenCalled();
  cleanup();
  await open();
  button("Continue setting up shop");
});

test("Escape with a group's sheet open doesn't close Team rules behind it", async () => {
  await open();
  await toStart();
  fireEvent.click(button("Review team rules ›"));
  fireEvent.click(button("Careful reviews: each rule"));
  (document.activeElement as HTMLElement | null)?.blur();
  fireEvent.keyDown(window, { key: "Escape" });
  await flush();
  expect(heading()).toBe("Team rules for shop");
});
