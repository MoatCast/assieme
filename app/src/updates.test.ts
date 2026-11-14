import { beforeEach, expect, test, vi } from "vitest";

const check = vi.hoisted(() => vi.fn());
const ask = vi.hoisted(() => vi.fn());
const relaunch = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/plugin-updater", () => ({ check }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ ask }));
vi.mock("@tauri-apps/plugin-process", () => ({ relaunch }));

import { offerUpdate } from "./updates";

beforeEach(() => { check.mockReset(); ask.mockReset(); relaunch.mockReset(); });

test("a newer version is installed and the app restarts after a yes", async () => {
  const install = vi.fn();
  check.mockResolvedValue({ version: "0.2.0", downloadAndInstall: install });
  ask.mockResolvedValue(true);
  await offerUpdate();
  expect(ask.mock.calls[0][0]).toContain("Assieme 0.2.0");
  expect(install).toHaveBeenCalled();
  expect(relaunch).toHaveBeenCalled();
});

test("on a no, nothing is downloaded", async () => {
  const install = vi.fn();
  check.mockResolvedValue({ version: "0.2.0", downloadAndInstall: install });
  ask.mockResolvedValue(false);
  await offerUpdate();
  expect(install).not.toHaveBeenCalled();
  expect(relaunch).not.toHaveBeenCalled();
});

test("no update, or no network, asks nothing", async () => {
  check.mockResolvedValue(null);
  await offerUpdate();
  check.mockRejectedValue(new Error("offline"));
  await offerUpdate();
  expect(ask).not.toHaveBeenCalled();
});
