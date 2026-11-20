import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const b = vi.hoisted(() => ({ folderPreview: vi.fn(), folderSave: vi.fn() }));
vi.mock("./bridge", async (orig) => ({ ...(await orig<typeof import("./bridge")>()), ...b }));

import FolderSetupBlock, { setupAction, useFolderSetup } from "./FolderSetup";

const flush = () => act(async () => {});
const PREVIEW = {
  files: 214, sample: ["index.html", "recipes/pasta.md"], first: true, empty: false,
  left_out: [".env", "private-key.pem", "node_modules", "a.key", "b.key", "c.key", "d.key", "e.key"],
};

beforeEach(() => {
  vi.clearAllMocks();
  b.folderPreview.mockResolvedValue(PREVIEW);
  b.folderSave.mockResolvedValue({ ok: true });
});
afterEach(cleanup);

/** The block plus its footer action, as the Project step draws them. */
function Harness({ onSaved }: { onSaved: () => void }) {
  const s = useFolderSetup("/Users/me/shop");
  const a = setupAction(s, onSaved);
  return <><FolderSetupBlock setup={s} />{a && <button type="button" disabled={s.busy} onClick={a.act}>{a.label}</button>}</>;
}
async function open(onSaved = vi.fn()) {
  render(<Harness onSaved={onSaved} />);
  await flush();
  return { onSaved };
}

test("a first save shows the title, the count and at most 3 left-out names", async () => {
  await open();
  expect(b.folderPreview).toHaveBeenCalledWith("/Users/me/shop");
  screen.getByText("This folder has no saved version yet");
  screen.getByText("Agents start from a saved version of your project, so Assieme saves one first. Your files stay as they are.");
  screen.getByText("214 files. Left out: .env, private-key.pem, node_modules and 5 more.");
  screen.getByRole("button", { name: "Save and continue" });
});

test("the data line names the company behind each agent", async () => {
  await open();
  screen.getByText("When agents work, what they read goes to the company behind each agent, such as Anthropic, OpenAI or Google.");
});

test("Show all files swaps in the list, and Hide files swaps back", async () => {
  await open();
  fireEvent.click(screen.getByText("Show all files"));
  expect(screen.getByLabelText("Files in the first version").textContent).toBe("index.html\nrecipes/pasta.md\nand 212 more");
  fireEvent.click(screen.getByText("Hide files"));
  expect(screen.queryByLabelText("Files in the first version")).toBeNull();
});

test("git's commands sit behind Details", async () => {
  await open();
  const details = screen.getByRole("button", { name: "Details" });
  expect(details).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("Uses git: git init, then a first commit.")).toBeNull();
  fireEvent.click(details);
  screen.getByText("Uses git: git init, then a first commit.");
});

test("a new version has its own title, singular handled, and no left-out line", async () => {
  b.folderPreview.mockResolvedValue({ ...PREVIEW, files: 3, sample: ["a", "b", "c"], first: false, left_out: [] });
  await open();
  screen.getByText("Save 3 files as a new version?");
  screen.getByText("3 files.");
  cleanup();
  b.folderPreview.mockResolvedValue({ ...PREVIEW, files: 1, sample: ["a"], first: false, left_out: [] });
  await open();
  screen.getByText("Save 1 file as a new version?");
});

test("an empty folder offers Start project", async () => {
  b.folderPreview.mockResolvedValue({ files: 0, sample: [], left_out: [], first: true, empty: true });
  const { onSaved } = await open();
  screen.getByText("Start a new project in this folder?");
  screen.getByText("The folder is empty, so agents start from scratch.");
  fireEvent.click(screen.getByText("Start project"));
  await flush();
  expect(b.folderSave).toHaveBeenCalledWith("/Users/me/shop");
  expect(onSaved).toHaveBeenCalled();
});

test("Save and continue saves, then reports it", async () => {
  const { onSaved } = await open();
  fireEvent.click(screen.getByText("Save and continue"));
  await flush();
  expect(b.folderSave).toHaveBeenCalledWith("/Users/me/shop");
  expect(onSaved).toHaveBeenCalledTimes(1);
});

test("a failed save keeps git's words behind Details, and Try again saves again", async () => {
  b.folderSave.mockRejectedValueOnce("Unable to create .git/index.lock: File exists");
  const { onSaved } = await open();
  fireEvent.click(screen.getByText("Save and continue"));
  await flush();
  screen.getByText("Couldn’t save the first version");
  screen.getByText("Your files are unchanged. Another app may be using this folder’s versions. Close it, then try again.");
  expect(screen.queryByText("Git said: Unable to create .git/index.lock: File exists")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Details" }));
  screen.getByText("Git said: Unable to create .git/index.lock: File exists");
  expect(onSaved).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Try again"));
  await flush();
  expect(b.folderSave).toHaveBeenCalledTimes(2);
  expect(onSaved).toHaveBeenCalled();
});

test("a failed new version says so", async () => {
  b.folderPreview.mockResolvedValue({ ...PREVIEW, first: false, left_out: [] });
  b.folderSave.mockRejectedValueOnce("locked");
  await open();
  fireEvent.click(screen.getByText("Save and continue"));
  await flush();
  screen.getByText("Couldn’t save the new version");
});

test("a folder that can't be looked through says so and can be tried again", async () => {
  b.folderPreview.mockRejectedValueOnce(new Error("Listing this folder took too long."));
  await open();
  screen.getByText("Couldn’t look through this folder");
  fireEvent.click(screen.getByRole("button", { name: "Details" }));
  screen.getByText("Git said: Listing this folder took too long.");
  fireEvent.click(screen.getByText("Try again"));
  await flush();
  screen.getByText("This folder has no saved version yet");
});

test("while loading it says so and offers no action", () => {
  b.folderPreview.mockReturnValue(new Promise(() => {}));
  render(<Harness onSaved={vi.fn()} />);
  screen.getByText("Looking through this folder…");
  expect(screen.queryByRole("button")).toBeNull();
});

test("while a save is running the action is disabled", async () => {
  let done!: () => void;
  b.folderSave.mockReturnValue(new Promise<void>((res) => (done = res)));
  const { onSaved } = await open();
  fireEvent.click(screen.getByText("Save and continue"));
  await flush();
  expect(screen.getByText("Save and continue")).toBeDisabled();
  await act(async () => done());
  expect(onSaved).toHaveBeenCalled();
});
