import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { TeamDraft } from "./bridge";

const b = vi.hoisted(() => ({ herdrFind: vi.fn(), herdrImport: vi.fn(), pickScript: vi.fn() }));
vi.mock("./bridge", async (orig) => ({ ...(await orig<typeof import("./bridge")>()), ...b }));

import HerdrImport from "./HerdrImport";

const DRAFT: TeamDraft = { id: "d1", project: "/Users/me/shop", task: "Add a cart", roles: [], practices: {}, why: "", from: "herdr" };
const flush = () => act(async () => {});

beforeEach(() => {
  vi.clearAllMocks();
  b.herdrImport.mockResolvedValue(DRAFT);
});
afterEach(cleanup);

async function open() {
  const onImported = vi.fn();
  render(<HerdrImport onClose={() => {}} onImported={onImported} />);
  await flush();
  return onImported;
}

test("lists what herdr has, says when a team is still running there, and imports the one picked", async () => {
  b.herdrFind.mockResolvedValue({ teams: [
    { id: "running:shop", label: "shop", where: "Running in herdr", agents: 9, running: true },
    { id: "saved:default:w1", label: "docs", where: "Saved in herdr", agents: 1, running: false },
  ] });
  const onImported = await open();
  expect(screen.getByText("Running in herdr · 9 agents")).toBeTruthy();
  expect(screen.getByText("Saved in herdr · 1 agent")).toBeTruthy();
  expect(screen.getAllByText("Still running in herdr: stop it there first.")).toHaveLength(1);
  fireEvent.click(screen.getAllByText("Import")[1]);
  await flush();
  expect(b.herdrImport).toHaveBeenCalledWith("saved:default:w1");
  expect(onImported).toHaveBeenCalledWith(DRAFT);
});

test("with nothing from herdr, a launch script can still be chosen", async () => {
  b.herdrFind.mockResolvedValue({ teams: [] });
  b.pickScript.mockResolvedValue("/Users/me/shop/team.sh");
  const onImported = await open();
  expect(screen.getByText("No herdr teams on this Mac")).toBeTruthy();
  fireEvent.click(screen.getByText("Choose a launch script…"));
  await flush();
  expect(b.herdrImport).toHaveBeenCalledWith("script:/Users/me/shop/team.sh");
  expect(onImported).toHaveBeenCalled();
});

test("a script that can't be imported says why and opens nothing", async () => {
  b.herdrFind.mockResolvedValue({ teams: [] });
  b.pickScript.mockResolvedValue("/Users/me/notes.txt");
  b.herdrImport.mockRejectedValue(new Error("No Claude Code or Codex agents were found in it."));
  const onImported = await open();
  fireEvent.click(screen.getByText("Choose a launch script…"));
  await flush();
  expect(screen.getByRole("alert").textContent).toContain("No Claude Code or Codex agents");
  expect(onImported).not.toHaveBeenCalled();
});
