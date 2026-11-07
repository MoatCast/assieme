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

const flush = () => act(async () => {});
const both = { claude: true, codex: true };

beforeEach(() => {
  vi.clearAllMocks();
  b.teamPresets.mockResolvedValue({ presets: PRESETS, practices: PRACTICES, last: null });
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
