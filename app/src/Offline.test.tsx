import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const serviceInstall = vi.hoisted(() => vi.fn());
vi.mock("./bridge", () => ({ serviceInstall }));

import Offline from "./Offline";

beforeEach(() => {
  vi.useFakeTimers();
  serviceInstall.mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

test("a short drop only says Reconnecting", async () => {
  render(<Offline afterMs={15000} />);
  expect(screen.getByText("Reconnecting…")).toBeTruthy();
  await act(async () => { vi.advanceTimersByTime(14000); });
  expect(screen.queryByRole("button", { name: "Start it" })).toBeNull();
});

test("after a while it says what's wrong and offers to start the service", async () => {
  serviceInstall.mockResolvedValue(undefined);
  render(<Offline afterMs={15000} />);
  await act(async () => { vi.advanceTimersByTime(15000); });
  expect(screen.getByText(/service isn’t answering/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Start it" }));
  await act(async () => {});
  expect(serviceInstall).toHaveBeenCalledTimes(1);
});

test("a failed start shows why and can be tried again", async () => {
  serviceInstall.mockRejectedValue(new Error("launchctl said no"));
  render(<Offline afterMs={0} />);
  fireEvent.click(screen.getByRole("button", { name: "Start it" }));
  await act(async () => {});
  expect(screen.getByText(/launchctl said no/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Start it" }) as HTMLButtonElement).disabled).toBe(false);
});
