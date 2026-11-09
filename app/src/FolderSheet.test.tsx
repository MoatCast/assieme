import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const b = vi.hoisted(() => ({ folderPreview: vi.fn(), folderSave: vi.fn() }));
vi.mock("./bridge", async (orig) => ({ ...(await orig<typeof import("./bridge")>()), ...b }));

import FolderSheet from "./FolderSheet";

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

async function open(onClose = vi.fn(), onSaved = vi.fn()) {
  render(<FolderSheet project="/Users/me/shop" onClose={onClose} onSaved={onSaved} />);
  await flush();
  return { onClose, onSaved };
}

test("a first save shows the count, what git does, and at most 3 left-out names", async () => {
  await open();
  expect(b.folderPreview).toHaveBeenCalledWith("/Users/me/shop");
  screen.getByRole("heading", { name: "Save 214 files as the first version?" });
  screen.getByText(/Your files stay as they are\. Assieme adds a hidden \.git folder to track versions\. Saving stays on this Mac\. When agents work, what they read goes to Anthropic or OpenAI\./);
  screen.getByText("Uses git: git init, then a first commit.");
  screen.getByText("Left out of the saved version, so agents don’t get a copy");
  screen.getByText(".env, private-key.pem, node_modules and 5 more");
  screen.getByText(/Your project may need these to run\./);
});

test("Show all files swaps in the list, and Hide files swaps back", async () => {
  await open();
  fireEvent.click(screen.getByText("Show all 214 files"));
  const list = screen.getByLabelText("Files in the first version");
  expect(list.textContent).toBe("index.html\nrecipes/pasta.md\nand 212 more");
  expect(screen.queryByText("Left out of the saved version, so agents don’t get a copy")).toBeNull();
  screen.getByText(/Left out: \.env, private-key\.pem, node_modules and 5 more/);
  fireEvent.click(screen.getByText("Hide files"));
  expect(screen.queryByLabelText("Files in the first version")).toBeNull();
  screen.getByText("Show all 214 files");
});

test("Save them first reuses the dialog with its own title, singular handled", async () => {
  b.folderPreview.mockResolvedValue({ ...PREVIEW, files: 3, sample: ["a", "b", "c"], first: false, left_out: [] });
  await open();
  screen.getByRole("heading", { name: "Save 3 files as a new version?" });
  expect(screen.queryByText(/Left out of the saved version/)).toBeNull();
  cleanup();
  b.folderPreview.mockResolvedValue({ ...PREVIEW, files: 1, sample: ["a"], first: false, left_out: [] });
  await open();
  screen.getByRole("heading", { name: "Save 1 file as a new version?" });
  fireEvent.click(screen.getByText("Show all 1 file"));
  expect(screen.getByLabelText("Files in the new version").textContent).toBe("a");
});

test("an empty folder asks to start the empty project", async () => {
  b.folderPreview.mockResolvedValue({ files: 0, sample: [], left_out: [], first: true, empty: true });
  const { onSaved } = await open();
  screen.getByRole("heading", { name: "Start this empty project?" });
  screen.getByText("Uses git: git init, then an empty first commit.");
  fireEvent.click(screen.getByText("Start project"));
  await flush();
  expect(b.folderSave).toHaveBeenCalledWith("/Users/me/shop");
  expect(onSaved).toHaveBeenCalled();
});

test("Save and continue saves, then reports it; Not now changes nothing", async () => {
  const { onClose, onSaved } = await open();
  fireEvent.click(screen.getByText("Not now"));
  expect(onClose).toHaveBeenCalled();
  expect(b.folderSave).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Save and continue"));
  await flush();
  expect(b.folderSave).toHaveBeenCalledWith("/Users/me/shop");
  expect(onSaved).toHaveBeenCalled();
});

test("a failed save shows what git said, with Cancel and Try again", async () => {
  b.folderSave.mockRejectedValueOnce("Unable to create .git/index.lock: File exists");
  const { onClose, onSaved } = await open();
  fireEvent.click(screen.getByText("Save and continue"));
  await flush();
  screen.getByRole("heading", { name: "Couldn’t save the first version" });
  screen.getByText("Your files are as they were. Try again, or choose another folder.");
  screen.getByText("Git said: Unable to create .git/index.lock: File exists");
  expect(onSaved).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Try again"));
  await flush();
  expect(b.folderSave).toHaveBeenCalledTimes(2);
  expect(onSaved).toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
});

test("a folder that can't be looked through says so and can be tried again", async () => {
  b.folderPreview.mockRejectedValueOnce(new Error("Listing this folder took too long."));
  await open();
  screen.getByText("Git said: Listing this folder took too long.");
  b.folderPreview.mockResolvedValue(PREVIEW);
  fireEvent.click(screen.getByText("Try again"));
  await flush();
  screen.getByRole("heading", { name: "Save 214 files as the first version?" });
});

test("it is a modal: focus on the primary action, Escape is Not now, Tab stays inside, focus returns", async () => {
  const opener = document.createElement("button");
  document.body.append(opener);
  opener.focus();
  const { onClose } = await open();
  expect(screen.getByRole("dialog").getAttribute("aria-modal")).toBe("true");
  const save = screen.getByText("Save and continue");
  expect(document.activeElement).toBe(save);
  fireEvent.keyDown(save, { key: "Tab" });
  expect(document.activeElement).toBe(screen.getByText("Show all 214 files"));
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(onClose).toHaveBeenCalled();
  cleanup();
  expect(document.activeElement).toBe(opener);
  opener.remove();
});
