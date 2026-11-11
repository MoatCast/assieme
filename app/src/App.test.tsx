import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const serviceRunning = vi.fn<() => Promise<boolean>>();
const serviceInstall = vi.fn<() => Promise<void>>();
const windowShow = vi.hoisted(() => vi.fn(() => Promise.resolve()));
vi.mock("./bridge", () => ({
  serviceRunning: () => serviceRunning(),
  serviceInstall: () => serviceInstall(),
  snapshot: () => new Promise(() => {}),
  windowShow,
}));
vi.mock("./Shell", () => ({ default: () => <div>first run</div> }));

import App from "./App";

const flush = () => act(async () => {});
const QUESTION = "Assieme runs a small background service so it can watch your agents while this window is closed. Start it?";

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

test("asks before starting the service", async () => {
  serviceRunning.mockResolvedValue(false);
  serviceInstall.mockResolvedValue(undefined);
  render(<App />);
  await flush();
  expect(screen.getByText(QUESTION)).toBeTruthy();
  expect(serviceInstall).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Start it"));
  await flush();
  expect(serviceInstall).toHaveBeenCalledTimes(1);
  expect(screen.queryByText(QUESTION)).toBeNull();
});

test("no question when the service already runs", async () => {
  serviceRunning.mockResolvedValue(true);
  render(<App />);
  await flush();
  expect(screen.queryByText(QUESTION)).toBeNull();
  expect(screen.getByText("first run")).toBeTruthy();
});

test("not now keeps the app usable", async () => {
  serviceRunning.mockResolvedValue(false);
  render(<App />);
  await flush();
  fireEvent.click(screen.getByText("Not now"));
  expect(screen.queryByText(QUESTION)).toBeNull();
  expect(serviceInstall).not.toHaveBeenCalled();
});

test("a failed start shows the error and the log path", async () => {
  serviceRunning.mockResolvedValue(false);
  serviceInstall.mockRejectedValue("launchctl bootstrap failed (5)");
  render(<App />);
  await flush();
  fireEvent.click(screen.getByText("Start it"));
  await flush();
  expect(screen.getByText(/launchctl bootstrap failed \(5\)/)).toBeTruthy();
  expect(screen.getByText(/~\/\.local\/state\/assieme\/brain\.log/)).toBeTruthy();
});

test("shows the window, which starts hidden so it never flashes the wrong theme", async () => {
  serviceRunning.mockResolvedValue(true);
  render(<App />);
  await flush();
  expect(windowShow).toHaveBeenCalled();
});
